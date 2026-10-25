from backend.tests.auth_helpers import login_admin
import json
import sqlite3
from datetime import date
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import connect
from backend.app.main import create_app
from backend.tests.quote_fixtures import sample_data,insert_published


@pytest.fixture
def environment(tmp_path,monkeypatch):
    import backend.app.quotation as quotation
    monkeypatch.setattr(quotation,'today_in_zone',lambda timezone:date(2026,11,15))
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai')
    with TestClient(create_app(settings)) as client:
        login_admin(client)
        path=settings.data_dir/'asw.sqlite3'
        data=sample_data(path)
        yield client,path,data


def search(client,preview=False,**filters):
    response=client.post('/api/manual-drafts/search' if preview else '/api/quotes/search',json=filters)
    assert response.status_code==200,response.text
    return response.json()


def draft(client,data):
    response=client.post('/api/manual-drafts',json=data)
    assert response.status_code==201,response.text
    return response.json()


def test_defaults_incomplete_save_edit_evidence_and_no_publish(environment):
    client,path,data=environment
    initial=client.get('/api/manual-drafts/defaults').json()['data']
    assert initial['origin_port_id']==data['origin_port_id'] and initial['origin_confirmed'] is False
    saved=draft(client,{})
    assert not saved['ready_for_review'] and saved['issues']
    assert search(client,True,date_scope='all')['excluded_incomplete_drafts']==1
    response=client.put(f"/api/manual-drafts/{saved['id']}?expected_revision=1",json=data)
    assert response.status_code==200 and response.json()['ready_for_review']
    assert response.json()['revision']==2
    assert client.put(f"/api/manual-drafts/{saved['id']}?expected_revision=1",json=data).status_code==409
    assert search(client)['total']==0
    assert search(client,True)['total']==1
    assert search(client,True)['source']=='draft_preview'
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]==0
        assert json.loads(db.execute('SELECT raw_json FROM draft_rows').fetchone()[0])['initial']['currency']==''
        assert db.execute('SELECT manual_note FROM source_evidence').fetchone()[0]==data['manual_note']
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='manual_draft'").fetchone()[0]==2
        db.execute("UPDATE upload_batches SET status='pending'")
    assert client.put(f"/api/manual-drafts/{saved['id']}?expected_revision=2",json=data).status_code==409


@pytest.mark.parametrize('price,has_issue',[('',False),(None,False),('0',True),('-1',True),('NaN',True),('Infinity',True),('abc',True),('1.001',True),('1e3',True),('99999999999',True),('0.01',False),('1000.25',False)])
def test_price_validation(environment,price,has_issue):
    client,_,data=environment
    data['prices']['40HQ']=price
    result=draft(client,data)
    assert any(i['field']=='prices.40HQ' for i in result['issues'])==has_issue
    if price=='0':
        data['zero_confirmed']=['40HQ']
        assert draft(client,data)['ready_for_review']


@pytest.mark.parametrize('changes,field',[
    ({'valid_from':''},'valid_from'),({'valid_to':'2026-02-30'},'valid_to'),({'valid_to':'2026-10-01'},'valid_to'),
    ({'currency':'usd'},'currency'),({'currency':'XYZ'},'currency'),({'service_kind':'unknown'},'service_kind'),
    ({'validity_basis':'other'},'validity_basis'),({'conditions':'限轻货','conditions_confirmed':False},'conditions_confirmed'),
    ({'transit_days':'30-35'},'transit_days'),({'manual_note':''},'manual_note'),({'origin_confirmed':False},'origin_confirmed'),
    ({'carrier_id':'missing'},'carrier_id'),({'prices':{}},'prices')])
def test_required_fields_validation(environment,changes,field):
    client,_,data=environment
    saved=draft(client,{**data,**changes})
    assert not saved['ready_for_review'] and any(i['field']==field for i in saved['issues'])


def test_inactive_related_data_revalidates_draft(environment):
    client,path,data=environment
    assert draft(client,data)['ready_for_review']
    with connect(path) as db:
        db.execute("UPDATE suppliers SET active=0 WHERE id='supplier-a'")
    saved=client.get('/api/manual-drafts').json()['items'][0]
    assert not saved['ready_for_review']
    assert search(client,True)['total']==0


def test_explicit_missing_origin_is_not_silently_defaulted(environment):
    client,_,data=environment
    saved=draft(client,{**data,'origin_port_id':''})
    assert saved['data']['origin_port_id']==''
    assert any(i['field']=='origin_port_id' for i in saved['issues'])


def test_mixed_quantities_missing_zero_and_decimal_precision(environment):
    client,path,data=environment
    insert_published(path,data)
    row=search(client,quantities={'20GP':2,'40HQ':1})['items'][0]
    assert row['subtotal']=='3900.00' and row['currency']=='USD'
    assert '全包' in row['subtotal_note'] and '另计' in row['extra_fees']
    insert_published(path,data,'missing-box',prices={'20GP':100025})
    rows=search(client,quantities={'20GP':2,'40HQ':1})['items']
    missing=next(r for r in rows if r['id'].startswith('missing-box'))
    assert missing['subtotal'] is None and missing['missing_boxes']==['40HQ']
    precise=next(r for r in search(client,quantities={'20GP':3})['items'] if r['id'].startswith('missing-box'))
    assert precise['subtotal']=='3000.75'
    insert_published(path,data,'zero-box',prices={'20GP':0})
    assert next(r for r in search(client,quantities={'20GP':2})['items'] if r['id'].startswith('zero-box'))['subtotal']=='0.00'


@pytest.mark.parametrize('count',[0,-1,1.5,True,'2',100001])
def test_invalid_quantities_rejected(environment,count):
    client,_,_=environment
    assert client.post('/api/quotes/search',json={'quantities':{'20GP':count}}).status_code==422


@pytest.mark.parametrize('etd,total',[('2026-11-01',1),('2026-11-30',1),('2026-10-31',0),('2026-12-01',0)])
def test_date_boundaries(environment,etd,total):
    client,path,data=environment
    insert_published(path,data)
    assert search(client,etd=etd)['total']==total


def test_default_date_future_expired_statuses(environment):
    client,path,data=environment
    insert_published(path,data,'normal')
    insert_published(path,{**data,'valid_from':'2026-12-01','valid_to':'2026-12-31'},'future')
    insert_published(path,{**data,'valid_from':'2026-10-01','valid_to':'2026-10-31'},'expired')
    insert_published(path,{**data,'valid_to':'2026-11-25'},'soon')
    result=search(client)
    assert result['reference_date']=='2026-11-15' and result['total']==2
    assert result['status_counts']=={'未生效':0,'有效':1,'即将到期':1,'已过期':0}
    all_dates=search(client,date_scope='all')
    assert all_dates['total']==4 and sum(all_dates['status_counts'].values())==4
    assert all_dates['status_counts']=={'未生效':1,'有效':1,'即将到期':1,'已过期':1}


def test_filters_multicurrency_conditions_pagination_and_sort(environment):
    client,path,data=environment
    insert_published(path,data,'one')
    insert_published(path,{**data,'carrier_id':'carrier-b','source_series_id':'series-b','currency':'EUR','conditions':'最低10柜','conditions_confirmed':True,'transit_days':'9'},'two')
    other=client.get('/api/catalog/ports?q=HAMBURG').json()['items'][0]
    insert_published(path,{**data,'destination_port_id':other['id'],'route_id':other['route_ids'][0],'origin_port_id':other['id']},'three')
    assert search(client)['total']==2
    assert search(client,origin_port_id='')['total']==3
    selected=search(client,destination_port_id=data['destination_port_id'],carrier_id='carrier-b',quantities={'20GP':2})
    assert selected['total']==1 and selected['items'][0]['currency']=='EUR'
    assert selected['items'][0]['condition_note']=='适用条件需确认'
    first=search(client,sort_by='transit_days',page_size=1)
    second=search(client,sort_by='transit_days',page_size=1,page=2)
    assert first['total']==2 and first['items'][0]['id']=='two-v1' and second['items'][0]['id']=='one-v1'
    assert search(client,carrier_id='carrier-a',destination_port_id=other['id'])['total']==0
    assert search(client,page=99)['items']==[]


def test_inactive_superseded_are_not_published_results(environment):
    client,path,data=environment
    insert_published(path,data,'active')
    insert_published(path,data,'old',current=False)
    insert_published(path,data,'disabled')
    with connect(path) as db:
        db.execute("UPDATE quotes SET active=0,disabled_reason='测试停用' WHERE id='disabled'")
    assert [r['id'] for r in search(client)['items']]==['active-v1']


def test_search_and_shape_errors(environment):
    client,_,data=environment
    for body in [{'etd':'2026-02-30'},{'destination_port_id':'missing'},{'page':0},{'page_size':101},{'sort_by':'price'},{'quantities':{'45HQ':1}}]:
        assert client.post('/api/quotes/search',json=body).status_code==422
    assert client.post('/api/manual-drafts',json={**data,'approved_by':'fake'}).status_code==422
    assert client.post('/api/manual-drafts',json={**data,'prices':{'20GP':1000}}).status_code==422


def test_draft_persists_after_new_app_lifespan(environment):
    client,path,data=environment
    saved=draft(client,data)
    settings=Settings(path.parent,path.parent/'restart-logs','Asia/Shanghai')
    with TestClient(create_app(settings)) as other:
        login_admin(other)
        assert other.get('/api/manual-drafts').json()['items'][0]['id']==saved['id']
        assert other.post('/api/quotes/search',json={}).json()['total']==0

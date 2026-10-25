import json
import sqlite3
from copy import deepcopy
from uuid import uuid4
import pytest
from backend.tests.test_quotation import environment, draft, search
from backend.app.database import connect


def get(client,bid):
    r=client.get('/api/review/batches/'+bid);assert r.status_code==200,r.text;return r.json()
def submit(client,bid):
    p=get(client,bid)['preview'];r=client.post(f'/api/review/batches/{bid}/submit',json={'token':p['token']});assert r.status_code==200,r.text;return r.json()['submission_id']
def decide(client,sid,decision='approved',reason='',key=None):return client.post(f'/api/review/submissions/{sid}/decision',json={'operation_key':key or uuid4().hex,'decision':decision,'reason':reason})
def publish(client,data):
    d=draft(client,data);s=submit(client,d['batch_id']);r=decide(client,s);assert r.status_code==200,r.text;return d,s

def test_publish_modify_keep_prices_history_idempotency(environment):
    c,path,data=environment;d,s=publish(c,data)
    found=search(c,date_scope='all')['items'];assert len(found)==1;qid=found[0]['quote_id']
    r=c.post(f'/api/quotes/{qid}/revision').json();row=next(i for i in c.get('/api/manual-drafts').json()['items'] if i['id']==r['row_id'])
    new=deepcopy(row['data']);new['prices']={'20GP':'1200'};new['remark']='新备注'
    assert c.put(f"/api/manual-drafts/{row['id']}?expected_revision=1",json=new).status_code==200
    p=get(c,r['batch_id'])['preview'];assert p['items'][0]['kind']=='modified';assert p['items'][0]['data']['prices']['40HQ']=='1900.00'
    sid=submit(c,r['batch_id']);key=uuid4().hex;assert decide(c,sid,key=key).status_code==200;assert decide(c,sid,key=key).json()['repeated'];assert decide(c,sid).status_code==409
    h=c.get(f'/api/quotes/{qid}/history').json();assert len(h['items'])==2;assert h['items'][1]['prices'][0]['amount_minor']==100000
    assert search(c,date_scope='all')['items'][0]['prices']['20GP']=='1200.00'
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM approval_records').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM source_evidence WHERE quote_version_id IS NOT NULL').fetchone()[0]>=2
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE submissions SET snapshot_json='{}'")

def test_incomplete_and_stale_preview(environment):
    c,path,data=environment;bad=draft(c,{**data,'valid_to':''});p=get(c,bad['batch_id'])['preview'];assert p['can_submit']
    assert decide(c,submit(c,bad['batch_id'])).status_code==200
    d=draft(c,data);p=get(c,d['batch_id'])['preview'];c.put(f"/api/manual-drafts/{d['id']}?expected_revision=1",json={**data,'remark':'changed'})
    assert c.post(f"/api/review/batches/{d['batch_id']}/submit",json={'token':p['token']}).status_code==409

def test_reject_withdraw_lock_and_resubmit(environment):
    c,path,data=environment;d=draft(c,data);sid=submit(c,d['batch_id'])
    assert c.put(f"/api/manual-drafts/{d['id']}?expected_revision=1",json=data).status_code==409
    assert decide(c,sid,'rejected','').status_code==422
    assert decide(c,sid,'rejected','请核对价格').status_code==200
    assert get(c,d['batch_id'])['submissions'][0]['reason']=='请核对价格'
    sid2=submit(c,d['batch_id']);assert sid2!=sid
    assert c.post(f"/api/review/batches/{d['batch_id']}/withdraw").status_code==200
    assert decide(c,sid2).status_code==409
    assert c.put(f"/api/manual-drafts/{d['id']}?expected_revision=1",json=data).status_code==200
    assert decide(c,submit(c,d['batch_id'])).status_code==200

def test_concurrent_new_and_updates_refuse_stale(environment):
    c,path,data=environment;a=draft(c,data);b=draft(c,data);sa=submit(c,a['batch_id']);sb=submit(c,b['batch_id'])
    assert decide(c,sa).status_code==200
    assert decide(c,sb).status_code==409
    q=search(c,date_scope='all')['items'][0]['quote_id'];drafts=[]
    for amount in ('1111','1222'):
        row=c.post(f'/api/quotes/{q}/revision').json();d=next(d for d in c.get('/api/manual-drafts').json()['items'] if d['id']==row['row_id'])
        value=deepcopy(d['data']);value['prices']['20GP']=amount;c.put(f"/api/manual-drafts/{d['id']}?expected_revision=1",json=value);drafts.append(submit(c,d['batch_id']))
    assert decide(c,drafts[0]).status_code==200;assert decide(c,drafts[1]).status_code==409

@pytest.mark.parametrize('field,value',[('source_series_id','series-b'),('carrier_id','carrier-b'),('currency','EUR'),('service_key','另一服务'),('conditions','限轻货')])
def test_distinct_identity_coexists(environment,field,value):
    c,path,data=environment;publish(c,data);new={**data,field:value,'conditions_confirmed':True};d=draft(c,new);assert get(c,d['batch_id'])['preview']['items'][0]['kind']=='new';assert decide(c,submit(c,d['batch_id'])).status_code==200;assert search(c,date_scope='all')['total']==2

def test_new_period_overlap_and_explicit_target(environment):
    c,path,data=environment;publish(c,data)
    d=draft(c,{**data,'valid_from':'2026-12-01','valid_to':'2026-12-31'});assert decide(c,submit(c,d['batch_id'])).status_code==200
    d=draft(c,{**data,'valid_from':'2026-11-15','valid_to':'2026-12-15'});p=get(c,d['batch_id']);assert not p['preview']['can_submit']
    target=p['targets'][0]
    assert c.put('/api/review/rows/'+d['id'],json={'revision':1,'target_quote_id':target['quote_id'],'expected_version':target['version']}).status_code==200
    assert not get(c,d['batch_id'])['preview']['can_submit'] # still collides with second period

def merge(path,ids,bid):
    with connect(path) as db:
        for rid in ids:db.execute('UPDATE draft_rows SET batch_id=? WHERE id=?',(bid,rid))

def test_batch_dedup_conflict_partial_and_atomic_rollback(environment):
    c,path,data=environment;a=draft(c,data);b=draft(c,data);bad=draft(c,{**data,'valid_to':'invalid-date'});merge(path,[b['id'],bad['id']],a['batch_id'])
    p=get(c,a['batch_id']);assert not p['preview']['can_submit']
    assert c.put('/api/review/rows/'+bad['id'],json={'revision':1,'included':False}).status_code==200
    p=get(c,a['batch_id'])['preview'];assert sorted(i['kind'] for i in p['items'])==['duplicate','new']
    sid=submit(c,a['batch_id'])
    rows=c.get('/api/manual-drafts').json()['items'];assert next(r for r in rows if r['id']==bad['id'])['batch_id']!=a['batch_id']
    with connect(path) as db:db.execute("CREATE TRIGGER fail_publish BEFORE INSERT ON quote_prices BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    # client raises server exception; transaction must roll back every insert.
    with pytest.raises(sqlite3.IntegrityError):decide(c,sid)
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM approval_records').fetchone()[0]==0
        assert db.execute('SELECT status FROM upload_batches WHERE id=?',(a['batch_id'],)).fetchone()[0]=='pending'
        db.execute('DROP TRIGGER fail_publish')
    assert decide(c,sid).status_code==200;assert search(c,date_scope='all')['total']==1

def test_contradictory_rows_block_both(environment):
    c,path,data=environment;a=draft(c,data);b=draft(c,{**data,'prices':{'20GP':'2000'}});merge(path,[b['id']],a['batch_id']);p=get(c,a['batch_id'])['preview'];assert not p['can_submit'];assert all(i['problems'] for i in p['items'])

def test_permission_no_client_role_override_and_disable(environment,monkeypatch):
    c,path,data=environment;d,s=publish(c,data);qid=search(c,date_scope='all')['items'][0]['quote_id'];d=draft(c,{**data,'prices':{'20GP':'1234'}});sid=submit(c,d['batch_id'])
    import backend.app.review as review
    original=review.actor
    with connect(path) as db:db.execute("INSERT INTO users VALUES ('business','业务员','!disabled','business',1,0)")
    monkeypatch.setattr(review,'actor',lambda db:{'id':'business','username':'业务员','role':'business','active':1})
    assert decide(c,sid).status_code==403
    assert c.post(f'/api/quotes/{qid}/disable',json={'reason':'旧报价'}).status_code==403
    assert c.post(f'/api/review/batches/{d["batch_id"]}/withdraw',headers={'X-Role':'admin'}).status_code==403
    monkeypatch.setattr(review,'actor',original)
    assert c.post(f'/api/quotes/{qid}/disable',json={'reason':' '}).status_code==422
    assert c.post(f'/api/quotes/{qid}/disable',json={'reason':'船公司撤价'}).status_code==200
    assert search(c,date_scope='all')['total']==0;assert len(c.get(f'/api/quotes/{qid}/history').json()['items'])==1
    assert decide(c,sid).status_code==409

def test_explicit_clear_and_identity_correction(environment):
    c,path,data=environment;publish(c,{**data,'remark':'保留的旧备注'});old=search(c,date_scope='all')['items'][0]
    d=draft(c,{**data,'service_key':'更正服务','remark':''})
    value={'revision':1,'target_quote_id':old['quote_id'],'expected_version':1,'field_actions':{'remark':'clear','prices.40HQ':'clear'}}
    assert c.put('/api/review/rows/'+d['id'],json=value).status_code==200
    p=get(c,d['batch_id'])['preview'];assert p['can_submit'];assert p['items'][0]['data']['remark']=='';assert '40HQ' not in p['items'][0]['data']['prices']
    assert decide(c,submit(c,d['batch_id'])).status_code==200
    result=search(c,date_scope='all');assert result['total']==1;assert result['items'][0]['service_key']=='更正服务'

def test_same_values_unchanged_and_frozen_names(environment):
    c,path,data=environment;publish(c,data);d=draft(c,data);p=get(c,d['batch_id'])['preview'];assert p['items'][0]['kind']=='unchanged';assert not p['can_submit']
    with connect(path) as db:db.execute("UPDATE carriers SET name='更名后的船公司' WHERE id='carrier-a'")
    assert search(c,date_scope='all')['items'][0]['carrier']['name']=='测试船公司a'

def test_pending_freezes_snapshot_after_other_publish(environment):
    c,path,data=environment;a=draft(c,data);b=draft(c,data);sa=submit(c,a['batch_id']);sb=submit(c,b['batch_id']);before=get(c,b['batch_id'])['preview'];assert decide(c,sa).status_code==200
    after=get(c,b['batch_id'])['preview'];assert before==after;assert decide(c,sb).status_code==409

def test_two_distinct_rows_fail_second_rolls_back_first(environment):
    c,path,data=environment;a=draft(c,data);b=draft(c,{**data,'source_series_id':'series-b'});merge(path,[b['id']],a['batch_id']);sid=submit(c,a['batch_id'])
    with connect(path) as db:db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON quote_versions WHEN (SELECT count(*) FROM quote_versions)>0 BEGIN SELECT RAISE(ABORT,'second fails'); END")
    with pytest.raises(sqlite3.IntegrityError):decide(c,sid)
    with connect(path) as db:
        for table in ('quotes','quote_versions','quote_prices','approval_records'):assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        db.execute('DROP TRIGGER fail_second')
    assert decide(c,sid).status_code==200;assert search(c,date_scope='all')['total']==2

def test_clear_required_field_and_inactive_reference_block(environment):
    c,path,data=environment;publish(c,data);d=draft(c,{**data,'prices':{'20GP':'1234'}});qid=search(c,date_scope='all')['items'][0]['quote_id']
    assert c.put('/api/review/rows/'+d['id'],json={'revision':1,'target_quote_id':qid,'expected_version':1,'field_actions':{'valid_from':'clear'}}).status_code==200
    assert get(c,d['batch_id'])['preview']['can_submit']
    assert decide(c,submit(c,d['batch_id'])).status_code==200
    other=draft(c,{**data,'source_series_id':'series-b'});sid=submit(c,other['batch_id'])
    with connect(path) as db:db.execute("UPDATE suppliers SET active=0 WHERE id='supplier-b'")
    assert decide(c,sid).status_code==409


def test_all_blank_fields_publish_search_export_and_revision(environment):
    from backend.app.quotation import Draft
    c,path,data=environment
    blank=Draft().model_dump()
    d=draft(c,blank)
    assert get(c,d['batch_id'])['preview']['can_submit']
    sid=submit(c,d['batch_id'])
    assert decide(c,sid).status_code==200
    found=search(c,origin_port_id='',date_scope='all')
    assert found['total']==1
    row=found['items'][0]
    assert row['prices']=={} and row['currency']=='' and row['valid_from']==''
    assert row['destination']=={'country':{}} and row['date_status']=='有效期未完整'
    assert search(c,origin_port_id='')['total']==1
    assert c.post('/api/excel/export',json={'origin_port_id':'','date_scope':'all'}).status_code==200
    assert c.get('/api/review/batches/'+d['batch_id']+'/export').status_code==200
    revision=c.post('/api/quotes/'+row['quote_id']+'/revision')
    assert revision.status_code==200
    assert get(c,revision.json()['batch_id'])['preview']['items'][0]['kind']=='unchanged'
    with connect(path) as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_missing_reference_and_invalid_supplied_values_still_block(environment):
    c,path,data=environment
    for change in ({'carrier_id':'does-not-exist'},{'valid_to':'bad-date'}, {'prices':{'20GP':'abc'}}):
        d=draft(c,{**data,**change})
        assert not get(c,d['batch_id'])['preview']['can_submit']


def test_optional_field_migration_preserves_versions_prices_evidence(tmp_path,monkeypatch):
    import backend.app.database as database
    from backend.tests.quote_fixtures import sample_data,insert_published
    path=tmp_path/'asw.sqlite3'
    latest=database.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(database,'MIGRATIONS',[m for m in latest if m[0]<=7])
        database.initialize(path)
    data=sample_data(path);vid=insert_published(path,data)
    with connect(path) as db:
        db.execute('INSERT INTO source_evidence(id,quote_version_id,manual_note) VALUES (?,?,?)',('migration-evidence',vid,'虚构迁移依据'))
        before=db.execute('SELECT * FROM quote_versions').fetchall()
        prices=db.execute('SELECT * FROM quote_prices').fetchall()
    database.initialize(path)
    database.initialize(path)
    with connect(path) as db:
        assert db.execute('SELECT * FROM quote_versions').fetchall()==before
        assert db.execute('SELECT * FROM quote_prices').fetchall()==prices
        assert db.execute('SELECT quote_version_id FROM source_evidence').fetchone()[0]==vid
        assert not db.execute('PRAGMA foreign_key_check').fetchall()

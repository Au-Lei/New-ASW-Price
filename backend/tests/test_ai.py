"""No provider network calls: schema, evidence, consent and workflow regression."""
import json
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from backend.app import ai_extract as ai
from backend.app.config import Settings
from backend.app.database import connect
from backend.app.main import create_app
from backend.tests.auth_helpers import login_admin
from backend.tests.quote_fixtures import sample_data
from backend.tests.test_uploads import batch,upload,enqueue,task,image

@pytest.fixture
def env(tmp_path):
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai',ai_enabled=True,ai_api_key='test-key',ai_key_file=tmp_path/'absent')
    app=create_app(settings)
    with TestClient(app) as c:
        login_admin(c);app.state.extraction_worker.stop();sample_data(app.state.database_path)
        yield c,app.state.database_path,settings,app.state.extraction_worker

def quote():
    facts={'origin_port':'QINGDAO','destination_port':'ROTTERDAM','carrier':'测试船公司a','supplier':'测试代理a','source_series':'测试协议a','currency':'USD','valid_from':'2026-11-01','valid_to':'2026-11-30','validity_basis':'ETD','conditions':'Minimum 10 containers'}
    excerpt=' '.join(facts.values())+' 1000.25 1900'
    return {'facts':facts,'prices':{'20GP':'1000.25','40HQ':'1900'},'sources':[{'id':'s','page':1,'excerpt':excerpt}], 'field_sources':{k:['s'] for k in [*facts,'prices.20GP','prices.40HQ']},'uncertainties':[]}
def response(q=None,finish='stop'):
    return {'model':ai.MODEL,'choices':[{'finish_reason':finish,'message':{'content':json.dumps({'quotes':[q or quote()],'notes':[]})}}]}
def consent(c,fid):
    r=c.post('/api/ai/files/'+fid+'/consent',json={'classification':'synthetic','confirmed':True});assert r.status_code==200,r.text

def test_consent_required_and_revocation(env,monkeypatch):
    c,path,s,w=env;f=upload(c,batch(c),image()).json();calls=[]
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:calls.append(1))
    t=enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['error_code']=='AI_SCOPE_REQUIRED';assert not calls
    assert c.post('/api/ai/files/'+f['id']+'/consent',json={'classification':'synthetic','confirmed':False}).status_code==422
    consent(c,f['id']);c.post('/api/ai/files/'+f['id']+'/consent',json={'classification':'none','confirmed':False})
    enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['error_code']=='AI_SCOPE_REQUIRED';assert not calls
    status=c.get('/api/ai/status').text;assert 'test-key' not in status

def test_preview_commit_edit_immutable_no_publish(env,monkeypatch):
    c,path,s,w=env;monkeypatch.setattr(ai,'call_deepseek',lambda *_:response())
    f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();done=task(c,t['id']);assert done['status']=='success',done
    iid=done['result']['ai_import_id'];preview=c.get('/api/ai/imports/'+iid).json();row=preview['rows'][0];d=row['data']
    assert d['prices']=={'20GP':'1000.25','40GP':'1900.00','40HQ':'1900.00'};assert d['carrier_id']=='carrier-a';assert not d['origin_confirmed'] and not d['conditions_confirmed']
    with connect(path) as db:assert db.execute('SELECT count(*) FROM draft_rows').fetchone()[0]==0
    selection={'row_ids':[row['id']]};assert c.post('/api/ai/imports/'+iid+'/commit',json=selection).status_code==200
    assert c.post('/api/ai/imports/'+iid+'/commit',json=selection).json()['already_committed']
    assert c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[row['id'],row['id']]}).status_code==422
    drafts=c.get('/api/manual-drafts').json()['items'];draft=next(v for v in drafts if v['id']==row['id']);assert draft['evidence'][0]['page_number']==1
    r=c.put('/api/manual-drafts/'+row['id']+'?expected_revision=1',json={**d,'remark':'human correction'});assert r.status_code==200,r.text
    assert c.get('/api/ai/imports/'+iid).json()=={**preview,'status':'committed','selection':[row['id']]}
    assert c.get('/api/ai/tasks/'+t['id']+'/calls').json()['items'][0]['response']['content']==response()['choices'][0]['message']['content']
    with connect(path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0

@pytest.mark.parametrize('finish',['length','tool_calls',None])
def test_truncated_not_saved(env,monkeypatch,finish):
    c,path,s,w=env;monkeypatch.setattr(ai,'call_deepseek',lambda *_:response(finish=finish));f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['error_code']=='AI_TRUNCATED'
    with connect(path) as db:assert db.execute('SELECT count(*) FROM ai_imports').fetchone()[0]==0

@pytest.mark.parametrize('mutator',[lambda q:q.update(approved=True),lambda q:q['facts'].update(origin_confirmed='true'),lambda q:q['sources'][0].update(page=9)])
def test_untrusted_fields_and_locations_rejected(env,monkeypatch,mutator):
    c,path,s,w=env;q=quote();mutator(q);monkeypatch.setattr(ai,'call_deepseek',lambda *_:response(q));f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['status']=='failed'

def test_uncertain_values_not_guessed(env,monkeypatch):
    c,path,s,w=env;q=quote();q['facts'].update(currency='$',valid_from='11/01',transit_days='30-35');q['prices']={'20GP':'0','40GP':'1900','40HQ':'1900'}
    q['sources'][0]['excerpt']+=' $ 11/01 30-35 0 40GP/HQ';q['field_sources'].update({k:['s'] for k in ['transit_days','prices.40GP']})
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:response(q));f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();r=task(c,t['id'])['result']['rows'][0]
    assert r['data']['currency']=='' and r['data']['valid_from']=='' and r['data']['transit_days']==''
    assert r['data']['prices']=={'20GP':'0.00','40GP':'1900.00','40HQ':'1900.00'} and not r['data']['zero_confirmed']

def test_completed_chunk_cache_and_failure_retry(env,monkeypatch):
    c,path,s,w=env
    # Two chunks with independent primary page evidence.
    doc=ai.Document('pdf',[{'pages':[1],'images':[],'content':{}},{'pages':[2],'images':[],'content':{}}],{}, {1:'',2:''},'')
    monkeypatch.setattr(ai,'prepare_document',lambda *_:doc);monkeypatch.setattr(ai,'images_for_part',lambda *_:[])
    calls=[]
    def provider(*_):
        calls.append(1)
        if len(calls)==2:raise ai.AIError('AI_RATE_LIMIT','retry')
        q=quote();q['sources'][0]['page']=2 if len(calls)==3 else 1
        return response(q)
    monkeypatch.setattr(ai,'call_deepseek',provider);f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['status']=='failed'
    enqueue(c,f['id']);w.run_once();done=task(c,t['id']);assert done['status']=='success',done
    assert len(calls)==3 and done['result']['calls'][0]['cached']

@pytest.mark.parametrize('text,expected',[('1,000.25','1000.25'),('0','0.00'),('-1',''),('1.234',''),('USD100','')])
def test_numbers(text,expected):assert ai.number(text)==expected

@pytest.mark.parametrize('payload,code',[
 ({'choices':[{'finish_reason':'stop','message':{'content':''}}]},'AI_EMPTY'),
 ({'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]},'AI_SCHEMA_INVALID'),
 ({'choices':[{'finish_reason':'stop','message':{'content':'{}','tool_calls':[{'x':1}]}}]},'AI_REFUSED'),
 ({},'AI_SCHEMA_INVALID'),
])
def test_bad_responses(payload,code):
    with pytest.raises(ai.AIError) as exc:ai.parse_response(payload)
    assert exc.value.code==code

@pytest.mark.parametrize('status,code',[(401,'AI_AUTH_FAILED'),(402,'AI_BALANCE'),(429,'AI_RATE_LIMIT'),(503,'AI_PROVIDER_ERROR')])
def test_transport_errors_hide_secrets(env,monkeypatch,status,code):
    import httpx
    c,path,s,w=env;original=httpx.Client
    def handler(request):
        assert str(request.url)==ai.ENDPOINT and request.headers['authorization']=='Bearer test-key'
        return httpx.Response(status,json={'error':'secret-provider-error-body'})
    monkeypatch.setattr(ai.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(ai.AIError) as exc:ai.call_deepseek(s,{'model':ai.MODEL})
    assert exc.value.code==code and 'secret-provider' not in str(exc.value) and 'test-key' not in str(exc.value)

def test_revoked_during_call_discards_result(env,monkeypatch):
    c,path,s,w=env;f=upload(c,batch(c),image()).json();consent(c,f['id'])
    def provider(*_):
        with connect(path) as db:db.execute('UPDATE uploaded_files SET ai_consent_json=NULL WHERE id=?',(f['id'],))
        return response()
    monkeypatch.setattr(ai,'call_deepseek',provider);t=enqueue(c,f['id']);w.run_once();assert task(c,t['id'])['error_code']=='AI_SCOPE_REQUIRED'
    with connect(path) as db:assert db.execute('SELECT count(*) FROM ai_imports').fetchone()[0]==0

def test_schema_cannot_force_ids_and_missing_evidence(env):
    c,path,s,w=env;q=quote();q['field_sources'].pop('currency');q['facts']['valid_from']='2027-01-01'
    doc=ai.Document('image',[],{}, {},'')
    with connect(path) as db:
        import sqlite3
        db.row_factory=sqlite3.Row;r=ai.normalize_quote(db,ai.Quote.model_validate(q),doc,{'pages':[1]})
    assert r['data']['currency']=='' and r['data']['valid_from']==''

def test_xlsx_evidence_formula_and_bounds(env):
    c,path,s,w=env;doc=ai.Document('xlsx',[],{'Rates':{'cells':{'A1':'1000'},'formulas':{'A1'}}},{},'')
    source=ai.Source(id='s',worksheet='Rates',cell_range='A1',excerpt='1000')
    with pytest.raises(ai.AIError) as exc:ai.source_text(source,doc,{'worksheet':'Rates','rows':[1,20]},True)
    assert exc.value.code=='AI_FORMULA_SOURCE'
    with pytest.raises(ai.AIError):ai.source_text(source.model_copy(update={'cell_range':'XFD1'}),doc,{'worksheet':'Rates','rows':[1,20]},True)


def test_long_image_splits_adaptively_and_does_not_repeat_context_rows(env,monkeypatch):
    from PIL import Image
    from io import BytesIO
    c,path,s,w=env
    picture=BytesIO();Image.new('RGB',(1000,1000),'white').save(picture,format='PNG')
    content=picture.getvalue();calls=[]
    def provider(settings,body):
        context=json.loads(body['messages'][1]['content'][0]['text'].split('\n',1)[1])
        calls.append(context['current']['row_center_y_range'])
        if len(calls)==1:return response(finish='length')
        quotes=[]
        for center in (.10,.30,.60,.90):
            q=quote();q['sources'][0]['region']=[.1,center-.01,.8,.02]
            quotes.append(q)
        return {'model':ai.MODEL,'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'quotes':quotes,'notes':[]})}}]}
    monkeypatch.setattr(ai,'call_deepseek',provider)
    f=upload(c,batch(c),content,'synthetic-long.png').json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();done=task(c,t['id'])
    assert done['status']=='success',done
    assert len(done['result']['rows'])==4
    assert [0,.125] in calls and [.125,.25] in calls
    assert len(calls)==6
    with connect(path) as db:
        assert db.execute("SELECT count(*) FROM ai_calls WHERE status='failed' AND error_code='AI_TRUNCATED'").fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0


def test_image_band_ownership_boundary_and_required_coordinates():
    q=ai.Quote.model_validate({**quote(),'sources':[{**quote()['sources'][0],'region':[0,.49,1,.02]}]})
    assert not ai.in_image_band(q,{'bounds':[0,.5]})
    assert ai.in_image_band(q,{'bounds':[.5,1]})
    with pytest.raises(ai.AIError):ai.in_image_band(ai.Quote.model_validate(quote()),{'bounds':[0,.5]})


def test_image_boundary_dedup_preserves_same_band_multiplicity_and_different_prices():
    from copy import deepcopy
    row={'raw':{'facts':{'destination_port':'TEST'},'prices':{'20GP':'10'}},'sources':[{'excerpt':'TEST 10','region':[0,.49,1,.02]}],'mapping_notes':[]}
    seen=[]
    first=ai.unique_image_rows([deepcopy(row),deepcopy(row)],{'bounds':[0,.5]},seen)
    assert len(first)==2
    second=ai.unique_image_rows([deepcopy(row),deepcopy(row),deepcopy(row)],{'bounds':[.5,1]},seen)
    assert len(second)==1 # max multiplicity across neighboring bands
    changed=deepcopy(row);changed['raw']['prices']['20GP']='11'
    assert len(ai.unique_image_rows([changed],{'bounds':[.5,1]},seen))==1


def test_bilingual_country_composite_ports_and_sailing_rules(env):
    c,path,s,w=env;q=quote();q['facts'].update(destination_port='OSAKA/KOBE',destination_country='日本 JAPAN',route='周六PBT3/周日CJV4')
    q['sources'][0]['excerpt']+=' OSAKA/KOBE 日本 JAPAN 周六PBT3/周日CJV4'
    q['field_sources'].update({k:['s'] for k in ['destination_country','route']})
    with connect(path) as db:
        db.row_factory=__import__('sqlite3').Row
        rows=ai.normalize_extracted_quotes(db,[ai.Quote.model_validate(q)],ai.Document('image',[],{},{},''),{'pages':[1]})
        assert len(rows)==2
        assert {r['raw']['enrichment']['fields']['portCn'] for r in rows}=={'大阪','神户'}
        assert all(r['raw']['enrichment']['fields']['countryEn']=='JAPAN' for r in rows)
        assert all(r['raw']['enrichment']['fields']['countryCn']=='日本' for r in rows)
        assert all(r['raw']['enrichment']['fields']['line']=='日韩航线' for r in rows)
        assert all(r['data']['sailing']=='周六PBT3/周日CJV4' and r['data']['route_id'] for r in rows)
        from backend.app.quote_enrichment import enrich_port
        assert enrich_port(db,'仁川 INCHON')['fields']['portCn']=='仁川'
        assert enrich_port(db,'平泽 PYSONGTAEK')['fields']['countryCn']=='韩国'


def test_local_image_draft_repair_preserves_human_changes_and_is_idempotent(env,monkeypatch):
    from backend.app.repair_ai_drafts import repair_image_drafts
    c,path,s,w=env;q=quote();q['facts']['destination_port']='OSAKA/KOBE';q['sources'][0]['excerpt']+=' OSAKA/KOBE'
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:response(q))
    # Simulate an existing old combined-port draft, preserving its immutable original result.
    original_normalizer=ai.normalize_extracted_quotes
    monkeypatch.setattr(ai,'normalize_extracted_quotes',lambda db,quotes,document,part:[ai.normalize_quote(db,v,document,part) for v in quotes])
    f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();result=task(c,t['id'])['result'];iid=result['ai_import_id'];rid=result['rows'][0]['id']
    assert c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[rid]}).status_code==200
    row=next(r for r in c.get('/api/manual-drafts').json()['items'] if r['id']==rid)
    edited={**row['data'],'remark':'人工修改必须保留'}
    assert c.put('/api/manual-drafts/'+rid+'?expected_revision=1',json=edited).status_code==200
    monkeypatch.setattr(ai,'normalize_extracted_quotes',original_normalizer)
    report=repair_image_drafts(path,True);assert report['updated']==1 and report['added']==1
    rows=c.get('/api/manual-drafts').json()['items'];assert len(rows)==2
    assert all(r['data']['remark']=='人工修改必须保留' for r in rows)
    assert all(r['data']['destination_port_id'] for r in rows)
    assert repair_image_drafts(path,True)['reviewed']==0
    assert len(c.get('/api/ai/imports/'+iid).json()['rows'])==1
    with connect(path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0


def test_image_requests_use_at_most_two_parallel_calls(env,monkeypatch):
    import threading,time
    from PIL import Image
    from io import BytesIO
    c,path,s,w=env;content=BytesIO();Image.new('RGB',(1000,1000),'white').save(content,format='PNG')
    lock=threading.Lock();active=0;peak=0
    def provider(settings,body):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.08)
        context=json.loads(body['messages'][1]['content'][0]['text'].split('\n',1)[1]);top,bottom=context['current']['row_center_y_range']
        q=quote();q['sources'][0]['region']=[0,(top+bottom)/2-.005,1,.01]
        with lock:active-=1
        return response(q)
    monkeypatch.setattr(ai,'call_deepseek',provider)
    f=upload(c,batch(c),content.getvalue(),'synthetic-parallel.png').json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once()
    assert task(c,t['id'])['status']=='success'
    assert peak==2


def test_template_directory_coverage_and_genoa_is_not_a_locode(env):
    from backend.app.catalog_import import SEED
    from backend.app.quote_enrichment import enrich_port
    c,path,s,w=env
    with connect(path) as db:
        db.row_factory=__import__('sqlite3').Row
        ports=json.loads(SEED.read_text())['ports']
        for port in ports:
            fields=enrich_port(db,port['name_en'],country=port['country_en'])['fields']
            assert fields['portCn'] and fields['countryEn']==port['country_en'],port['name_en']
        genoa=enrich_port(db,'Genoa')
        assert genoa['fields']['countryEn']=='ITALY' and genoa['fields']['portCn']=='热那亚'
        assert not genoa['port_code']
        for name in ['Chornomorsk','Constanta','Varna','Burgas','Poti','Derince (Safi)']:
            fields=enrich_port(db,name)['fields']
            assert all(fields[k] for k in ['portEn','portCn','countryEn','countryCn','line']),name


def test_inaccurate_image_coordinate_does_not_drop_recognized_quote(env,monkeypatch):
    c,path,s,w=env
    doc=ai.Document('image',[{'kind':'image','pages':[1],'bounds':[0,.25],'images':[],'content':{}}],{},{},'')
    monkeypatch.setattr(ai,'prepare_document',lambda *_:doc)
    q=quote();q['sources'][0]['region']=[0,.25,1,.05]
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:response(q))
    f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();done=task(c,t['id'])
    assert done['status']=='success' and len(done['result']['rows'])==1
    assert done['result']['counts']['recognized_quotes']==1
    assert any('保留报价' in n for n in done['result']['rows'][0]['mapping_notes'])


def test_geographic_fallback_only_sends_public_names_and_reuses_cache(env,monkeypatch):
    from copy import deepcopy
    from backend.app.port_metadata import complete_unknown_ports
    c,path,s,w=env;requests=[]
    row={'raw':{'facts':{'destination_port':'PUBLIC PORT','supplier':'PRIVATE-CUSTOMER'},'enrichment':{'fields':{'portEn':'PUBLIC PORT'}}},'data':{'prices':{'20GP':'9876'}},'mapping_notes':[]}
    def provider(settings,body):
        requests.append(body)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'ports':[{'input_name':'PUBLIC PORT','name_en':'PUBLIC PORT','name_cn':'示例港','country_en':'ITALY','country_cn':'意大利','route':'地西基本港','confidence':'high'}]})}}]}
    monkeypatch.setattr(ai,'call_deepseek',provider)
    rows=[deepcopy(row)];assert complete_unknown_ports(path,s,rows,lambda:None)==1
    payload=json.dumps(requests,ensure_ascii=False)
    assert 'PRIVATE-CUSTOMER' not in payload and '9876' not in payload and 'image_url' not in payload
    assert rows[0]['raw']['enrichment']['fields']['countryCn']=='意大利'
    assert rows[0]['raw']['enrichment']['reference']['ai_candidate']
    new=[deepcopy(row)];assert complete_unknown_ports(path,s,new,lambda:None)==0
    assert len(requests)==1 and new[0]['raw']['enrichment']['fields']['portCn']=='示例港'


def test_shared_header_dates_fill_only_unambiguous_top_header():
    row={'data':{'valid_from':'','valid_to':''},'sources':[{'page':1,'region':[0,.4,1,.03],'excerpt':'PORT 1000'}],'mapping_notes':[]}
    other={'data':{'valid_from':'2026-10-15','valid_to':'2026-10-31'},'sources':[{'page':1,'region':[0,0,1,.08],'excerpt':'有效期：2026年10月15日 ~ 10月31日'}],'mapping_notes':[]}
    ai.shared_image_dates([row,other])
    assert row['data']['valid_from']=='2026-10-15' and row['data']['valid_to']=='2026-10-31'
    assert len(row['sources'])==2


def test_cached_rebuild_restores_missing_rows_without_ai_or_publication(env,monkeypatch):
    from copy import deepcopy
    from backend.app.rebuild_image_drafts import rebuild
    c,path,s,w=env;first=quote();second=deepcopy(first);second['facts']['destination_port']='HAMBURG';second['sources'][0]['excerpt']+=' HAMBURG'
    provider={'model':ai.MODEL,'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'quotes':[first,second],'notes':[]})}}]}
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:provider)
    f=upload(c,batch(c),image()).json();consent(c,f['id']);t=enqueue(c,f['id']);w.run_once();result=task(c,t['id'])['result'];iid=result['ai_import_id']
    # Simulate a legacy preview that dropped the first recognized line.
    with connect(path) as db:
        value=json.loads(db.execute('SELECT preview_json FROM ai_imports WHERE id=?',(iid,)).fetchone()[0]);value['rows']=value['rows'][1:]
        db.execute('UPDATE ai_imports SET preview_json=? WHERE id=?',(json.dumps(value),iid))
    assert c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[value['rows'][0]['id']]}).status_code==200
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:(_ for _ in ()).throw(AssertionError('network forbidden')))
    report=rebuild(path,t['id'],True);assert report['restored']==1 and report['updated']==1
    assert len(c.get('/api/manual-drafts').json()['items'])==2
    assert rebuild(path,t['id'],True)['already_rebuilt']
    assert len(c.get('/api/ai/imports/'+iid).json()['rows'])==1
    with connect(path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0

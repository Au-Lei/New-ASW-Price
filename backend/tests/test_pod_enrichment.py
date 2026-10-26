from pathlib import Path
import sqlite3
import pytest
from backend.app.quote_enrichment import validity_range,equal_40,enrich_port,reference_ports
from backend.app.pod_excel import parse_pod
from backend.app.excel_format import write_workbook,read_xlsx
from backend.app.database import connect,initialize
from backend.tests.test_uploads import upload_env,batch,upload,enqueue,task

def fixture():
 return write_workbook([{'name':'POL青岛','metadata':['WK42 2026/10/18~10/24'],'headers':['POD',"20'","40'"],'rows':[['SGSGP','123','456'],['MYLPK / MYPEN','234','567'],['MYLZZ','300','700'],['ZZBAD','100','200']]}, {'name':'港口汇总','metadata':[],'headers':['港口代码','港口英文名全称','港口中文名称','国家','航线'],'rows':[['SGSGP','SINGAPORE','新加坡','新加坡',''],['MYLPK','PORT KLANG NORTH PORT','巴生北','马来西亚',''],['MYPEN','PENANG','槟城','马来西亚',''],['MYLZZ','LABUAN','纳闽岛','马来西亚','']]}])
@pytest.mark.parametrize('raw,expected',[
 ('WK42 2026/10/18~10/24',('2026-10-18','2026-10-24')),
 ('2026/12/28～1/3',('2026-12-28','2027-01-03')),
 ('2026-10-18 ~ 2026-10-24',('2026-10-18','2026-10-24')),
 ('WK42 10/18~10/24',('','')),('2026/2/30~3/2',('','')),
 ('2026/10/18~10/24 and 2026/11/1~11/7',('',''))])
def test_period(raw,expected):assert validity_range(raw)==expected
@pytest.mark.parametrize('original,expected',[
 ({'40GP':'123'},{'40GP':'123','40HQ':'123'}),({'40HQ':'0.00'},{'40GP':'0.00','40HQ':'0.00'}),
 ({'40GP':'123','40HQ':'456'},{'40GP':'123','40HQ':'456'}),({'20GP':'123'},{'20GP':'123'})])
def test_price_rule(original,expected):
 result,_=equal_40(original);assert result==expected

def test_source_table_mapping_and_local_upload_no_AI(upload_env,monkeypatch):
 import backend.app.ai_extract as ai
 monkeypatch.setattr(ai,'call_deepseek',lambda *_:pytest.fail('real quotation must not be sent'))
 c,path,data,settings,worker=upload_env;f=upload(c,batch(c),fixture(),'pod.xlsx').json();assert f['metadata']['local_parseable']
 t=enqueue(c,f['id']);worker.run_once();done=task(c,t['id']);assert done['status']=='success',done
 assert done['result']['provider']=='本机解析';rows=done['result']['rows'];assert len(rows)==5
 sg=next(r for r in rows if r['raw']['original_port_code']=='SGSGP');assert sg['data']['valid_from']=='2026-10-18' and sg['data']['valid_to']=='2026-10-24'
 assert sg['data']['prices']=={'20GP':'123.00','40GP':'456.00','40HQ':'456.00'}
 assert sg['raw']['enrichment']['fields']=={'portEn':'SINGAPORE','portCn':'新加坡','countryEn':'SINGAPORE','countryCn':'新加坡','line':'新马航线'}
 assert sg['data']['currency']=='' # USD in unrelated notes must not be extrapolated.
 assert any(s['id']=='port-reference' for s in sg['sources'])
 l=next(r for r in rows if r['raw']['original_port_code']=='MYLZZ');assert l['raw']['enrichment']['fields']['portEn']=='LABUAN' and not l['data']['destination_port_id']
 unknown=next(r for r in rows if r['raw']['original_port_code']=='ZZBAD');assert unknown['raw']['enrichment']['status']=='unresolved'
 iid=done['result']['ai_import_id'];r=c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[r['id'] for r in rows]});assert r.status_code==200,r.text
 drafts=c.get('/api/manual-drafts').json()['items'];assert next(d for d in drafts if d['id']==l['id'])['enrichment']['fields']['portCn']=='纳闽岛'
 with connect(path) as db:
  assert db.execute('SELECT count(*) FROM ai_calls').fetchone()[0]==0
  assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0

def test_private_codes_reference_priority_and_ambiguity(tmp_path):
 path=tmp_path/'db';initialize(path)
 with connect(path) as db:
  db.row_factory=sqlite3.Row
  refs={'BNMRI':{'name_en':'MUARA','name_cn':'穆阿拉港','country_cn':'文莱'}}
  r=enrich_port(db,'BNMRI',refs);assert r['fields']['portEn']=='MUARA' and r['fields']['countryEn']=='BRUNEI'
  r=enrich_port(db,'JPNHA');assert r['status']=='unresolved' and not r['fields']['portCn']

def test_reextract_keeps_edited_draft_and_source(upload_env):
 c,path,data,settings,worker=upload_env;f=upload(c,batch(c),fixture(),'pod.xlsx').json();t=enqueue(c,f['id']);worker.run_once();done=task(c,t['id']);rows=done['result']['rows'];iid=done['result']['ai_import_id']
 c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[r['id'] for r in rows]})
 row=next(r for r in c.get('/api/manual-drafts').json()['items'] if r['id']==rows[0]['id']);edited={**row['data'],'remark':'人工编辑须保留'}
 assert c.put('/api/manual-drafts/'+row['id']+'?expected_revision=1',json=edited).status_code==200
 r=c.post('/api/uploads/files/'+f['id']+'/reextract');assert r.status_code==200,r.text;new=r.json();assert new['id']!=f['id'] and new['batch_id']!=f['batch_id']
 t2=enqueue(c,new['id']);worker.run_once();assert task(c,t2['id'])['result']['ai_import_id']!=iid
 assert next(r for r in c.get('/api/manual-drafts').json()['items'] if r['id']==row['id'])['data']['remark']=='人工编辑须保留'
 assert c.get('/api/uploads/files/'+new['id']+'/original').content==c.get('/api/uploads/files/'+f['id']+'/original').content


def test_confirmed_code_alias_is_reused_without_reference(tmp_path):
 path=tmp_path/'db';initialize(path)
 with connect(path) as db:
  db.row_factory=sqlite3.Row
  port=db.execute("SELECT id FROM ports WHERE name_en='SINGAPORE'").fetchone()[0]
  db.execute("INSERT INTO port_aliases(id,port_id,name,name_key,active,confirmed) VALUES ('alias-test',?,'SGSGP','SGSGP',1,1)",(port,))
  result=enrich_port(db,'SGSGP');assert result['port_id']==port and result['fields']['portCn']=='新加坡'


def test_explicit_different_40_columns_are_preserved(tmp_path):
 path=tmp_path/'db';initialize(path)
 content=write_workbook([{'name':'青岛','metadata':['2026/10/18~10/24'],'headers':['POD',"20'",'40GP','40HQ'],'rows':[['THSGZ','100','200','300']]}])
 with connect(path) as db:r=parse_pod(db,content,20*1024*1024)
 assert r['rows'][0]['data']['prices']=={'20GP':'100.00','40GP':'200.00','40HQ':'300.00'}

def test_AI_normalization_reuses_same_file_port_dictionary_and_date_range(tmp_path):
 from backend.app.ai_extract import Quote,Document,normalize_quote
 path=tmp_path/'db';initialize(path);sheets=read_xlsx(fixture())
 document=Document('xlsx',[],{s['name']:s for s in sheets},{},'')
 q=Quote.model_validate({'facts':{'destination_port':'SGSGP','validity_original':'WK42 2026/10/18~10/24'},'prices':{'20GP':'123','40GP':'456'},'sources':[{'id':'main','worksheet':'POL青岛','cell_range':'A8:C8','excerpt':'SGSGP 123 456'},{'id':'period','worksheet':'POL青岛','cell_range':'A2','excerpt':'WK42 2026/10/18~10/24'}],'field_sources':{'destination_port':['main'],'validity_original':['period'],'prices.20GP':['main'],'prices.40GP':['main']}})
 with connect(path) as db:
  db.row_factory=sqlite3.Row;r=normalize_quote(db,q,document,{'worksheet':'POL青岛','rows':[1,20]})
 assert r['data']['valid_from']=='2026-10-18' and r['data']['valid_to']=='2026-10-24'
 assert r['display']['destination']['name_en']=='SINGAPORE'
 assert r['data']['prices']['40HQ']=='456.00'

@pytest.mark.parametrize('heading,expected',[('备注','仅限普货'),('注意事项','仅限普货'),('Notes','仅限普货'),('其他数据',''),('','')])
def test_local_remark_only_labelled_columns(tmp_path,heading,expected):
 path=tmp_path/'db';initialize(path)
 content=write_workbook([{'name':'青岛','headers':['POD',"20'","40'",heading],'rows':[['SGSGP','100','200','仅限普货']], 'metadata':[]}])
 with connect(path) as db:
  result=parse_pod(db,content,len(content)+1)
 assert result['rows'][0]['data']['remark']==expected
 assert result['rows'][0]['mapping_notes']
 assert result['rows'][0]['raw']['original_port_code']=='SGSGP'


def test_local_remark_after_separate_40_columns(tmp_path):
 path=tmp_path/'db';initialize(path)
 content=write_workbook([{'name':'青岛','headers':['POD','20GP','40GP','40HQ','Remarks'],'rows':[['SGSGP','100','200','250','限普货']], 'metadata':[]}])
 with connect(path) as db:row=parse_pod(db,content,len(content)+1)['rows'][0]
 assert row['data']['remark']=='限普货'
 assert row['data']['prices']['40HQ']=='250.00'
 assert any(s['cell_range'].startswith('E') and s['excerpt']=='限普货' for s in row['sources'])

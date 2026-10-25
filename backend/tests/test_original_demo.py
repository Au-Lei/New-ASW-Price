"""Backend support for the unchanged demo's query, inline rows and diff export."""
from decimal import Decimal
from backend.tests.test_quotation import environment,draft,search
from backend.tests.test_review import publish,get,submit,decide
from backend.app.excel_format import read_xlsx

def test_demo_text_and_container_filter_same_for_export(environment):
 c,path,data=environment;publish(c,data)
 assert search(c,date_scope='all',destination_text='鹿特')['total']==1
 assert search(c,date_scope='all',destination_text='rotter')['total']==1
 assert search(c,date_scope='all',destination_text='汉堡')['total']==0
 assert search(c,date_scope='all',container_type='40HQ')['total']==1
 r=c.post('/api/excel/export',json={'date_scope':'all','destination_text':'鹿特','container_type':'40HQ'})
 assert r.status_code==200 and r.headers['x-export-count']=='1'

def test_move_new_row_into_editing_batch_and_lock(environment):
 c,path,data=environment;a=draft(c,data);b=draft(c,{**data,'currency':'EUR'})
 moved=c.post('/api/review/rows/'+b['id']+'/move',json={'batch_id':a['batch_id'],'revision':1});assert moved.status_code==200,moved.text
 assert moved.json()['revision']==2 and len(get(c,a['batch_id'])['rows'])==2
 assert c.post('/api/review/rows/'+b['id']+'/move',json={'batch_id':a['batch_id'],'revision':1}).status_code==409
 submit(c,a['batch_id'])
 assert c.post('/api/review/rows/'+b['id']+'/move',json={'batch_id':a['batch_id'],'revision':2}).status_code==409

def test_diff_export_before_and_after_pending_frozen(environment):
 c,path,data=environment;d,s=publish(c,data);qid=search(c,date_scope='all')['items'][0]['quote_id']
 revision=c.post('/api/quotes/'+qid+'/revision').json();row=next(r for r in c.get('/api/manual-drafts').json()['items'] if r['id']==revision['row_id'])
 edited={**row['data'],'prices':{**row['data']['prices'],'20GP':'1050'},'remark':'=literal'}
 assert c.put('/api/manual-drafts/'+row['id']+'?expected_revision=1',json=edited).status_code==200
 r=c.get('/api/review/batches/'+revision['batch_id']+'/export');assert r.status_code==200,r.text
 cells=read_xlsx(r.content)[0]['cells'];assert cells['A8']=='修改' and Decimal(cells['H8'])==1050 and Decimal(cells['Z8'])==1000
 assert cells['P8']=='=literal' and not read_xlsx(r.content)[0]['formulas']
 sid=submit(c,revision['batch_id']);frozen=c.get('/api/review/batches/'+revision['batch_id']+'/export').content
 assert decide(c,sid).status_code==200
 assert c.get('/api/review/batches/'+revision['batch_id']+'/export').content==frozen


def test_template_detection_before_extract_no_external_call():
 from backend.app.uploads import validate_file
 from backend.app.config import Settings
 from backend.app.excel_format import write_workbook
 from backend.tests.test_excel import sheet
 from pathlib import Path
 s=Settings(Path('/private/tmp/unused-data'),Path('/private/tmp/unused-logs'),'Asia/Shanghai')
 assert validate_file(write_workbook([sheet()]),'fixed.xlsx',s)['fixed_template'] is True
 assert validate_file(write_workbook([sheet(headers=['自由格式'])]),'other.xlsx',s)['fixed_template'] is False


def test_incomplete_diff_exports_as_literal_not_published(environment):
 c,path,data=environment;d=draft(c,{**data,'source_series_id':'','valid_from':'11/01','prices':{'20GP':'=literal'}})
 r=c.get('/api/review/batches/'+d['batch_id']+'/export');assert r.status_code==200,r.text
 parsed=read_xlsx(r.content)[0];assert not parsed['formulas'];assert parsed['cells']['H8']=='=literal' and parsed['cells']['Q8']=='11/01'
 assert search(c,date_scope='all')['total']==0

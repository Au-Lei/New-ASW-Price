"""Explicit live DeepSeek acceptance. Only locally generated synthetic fixtures are sent."""
from pathlib import Path
import json,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from fastapi.testclient import TestClient
from backend.app.config import Settings,ROOT
from backend.app.main import create_app
from backend.app.database import connect
from backend.app.excel_format import write_workbook
from backend.tests.auth_helpers import login_admin
from backend.tests.quote_fixtures import sample_data
from backend.tests.test_uploads import batch,upload,enqueue,task
from backend.tests.test_ai import consent

LINES=[
'SYNTHETIC TEST ONLY - NOT A BUSINESS QUOTATION',
'Origin: QINGDAO    Destination: ROTTERDAM',
'Carrier: SYNTHETIC-CARRIER',
'Supplier: SYNTHETIC-AGENT    Agreement: DEMO-2026',
'Currency: USD    Service: DIRECT    Fee basis: CY-CY',
'20GP: 1000.25    40GP: 1800    40HQ: 1900',
'Validity: 2026-11-01 to 2026-11-30, based on ETD',
'Included: Ocean freight    Extra: PSS    Unknown: Destination charges',
'Condition: Minimum 10 containers',
]
def fixtures():
    doc=pymupdf.open();page=doc.new_page(width=1000,height=650)
    for i,line in enumerate(LINES):page.insert_text((40,55+i*48),line,fontsize=17)
    pdf=doc.tobytes();pix=page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5));png=pix.tobytes('png');doc.close()
    scan=pymupdf.open();scan.new_page(width=1000,height=650).insert_image(pymupdf.Rect(0,0,1000,650),stream=png);scanned=scan.tobytes();scan.close()
    xlsx=write_workbook([{'name':'Synthetic','title':LINES[0],'metadata':LINES[2:5]+[LINES[6]],'headers':['POL','POD','Small container 20GP','Large container 40GP','High cube 40HQ','ISO currency','Notes'],'rows':[['QINGDAO','ROTTERDAM','1000.25','1800','1900','USD','; '.join(LINES[7:])]]}])
    return [('synthetic.xlsx',xlsx),('synthetic.pdf',pdf),('synthetic-scan.pdf',scanned),('synthetic.png',png)]

def main():
    folder=Path(tempfile.mkdtemp(prefix='asw-task8-live-'));settings=Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=True)
    app=create_app(settings);report=[]
    with TestClient(app) as c:
        login_admin(c);w=app.state.extraction_worker;w.stop();path=app.state.database_path;sample_data(path)
        with connect(path) as db:
            db.execute("UPDATE carriers SET name='SYNTHETIC-CARRIER' WHERE id='carrier-a'");db.execute("UPDATE suppliers SET name='SYNTHETIC-AGENT' WHERE id='supplier-a'");db.execute("UPDATE source_series SET name='DEMO-2026' WHERE id='series-a'")
        assert c.get('/api/ai/status').json()['ready'],'AI key not configured'
        for name,content in fixtures():
            (folder/name).write_bytes(content);f=upload(c,batch(c),content,name).json();consent(c,f['id']);t=enqueue(c,f['id']);print('RUN',name,flush=True);w.run_once();done=task(c,t['id'])
            (folder/(name+'.result.json')).write_text(json.dumps(done,ensure_ascii=False,indent=2))
            calls=c.get('/api/ai/tasks/'+t['id']+'/calls').json();(folder/(name+'.calls.json')).write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            assert done['status']=='success',(name,done.get('error_code'),done.get('error'))
            result=done['result'];assert len(result['rows'])==1,(name,'unexpected row count')
            row=result['rows'][0];d=row['data'];assert d['prices']=={'20GP':'1000.25','40GP':'1800.00','40HQ':'1900.00'},(name,d['prices'])
            assert d['currency']=='USD' and d['valid_from']=='2026-11-01' and d['valid_to']=='2026-11-30',(name,'dates/currency')
            assert d['carrier_id']=='carrier-a' and d['source_series_id']=='series-a',(name,'catalog mapping')
            assert 'Minimum 10' in d['conditions'] and d['extra_fees']=='PSS',(name,'fees/conditions')
            assert not d['origin_confirmed'] and not d['conditions_confirmed']
            iid=result['ai_import_id'];r=c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[row['id']]});assert r.status_code==200,r.text
            with connect(path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0
            report.append({'file':name,'status':'passed','rows':1,'calls':len(result['calls'])});print('PASS',name,flush=True)
    (folder/'report.json').write_text(json.dumps(report,indent=2));print('RESULT_DIR',folder,flush=True)
if __name__=='__main__':main()

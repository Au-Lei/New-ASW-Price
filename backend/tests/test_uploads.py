from backend.tests.auth_helpers import login_admin
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import sqlite3
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from backend.app.config import Settings
from backend.app.database import connect, initialize
from backend.app.excel_format import write_workbook
from backend.app.main import create_app
from backend.app.uploads import validate_file, InvalidFile, ExtractionWorker
from backend.tests.quote_fixtures import sample_data
from backend.tests.test_excel import sheet


def image(kind='PNG',color='red'):
    out=BytesIO();Image.new('RGB',(20,10),color).save(out,format=kind);return out.getvalue()
def pdf(pages=1,encrypted=False):
    writer=PdfWriter()
    for _ in range(pages):writer.add_blank_page(width=200,height=200)
    if encrypted:writer.encrypt('test-only-password')
    out=BytesIO();writer.write(out);return out.getvalue()

@pytest.fixture
def upload_env(tmp_path):
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai')
    app=create_app(settings)
    with TestClient(app) as c:
        login_admin(c)
        worker=app.state.extraction_worker;worker.stop()
        path=settings.data_dir/'asw.sqlite3';data=sample_data(path)
        yield c,path,data,settings,worker

def batch(c):
    r=c.post('/api/uploads/batches');assert r.status_code==201,r.text;return r.json()['id']
def upload(c,bid,content,name='报价.png'):
    return c.post(f'/api/uploads/batches/{bid}/files',params={'filename':name},content=content,headers={'Content-Type':'application/octet-stream'})
def enqueue(c,fid):
    r=c.post(f'/api/uploads/files/{fid}/extract');assert r.status_code==200,r.text;return r.json()
def task(c,tid):
    r=c.get('/api/uploads/tasks/'+tid);assert r.status_code==200,r.text;return r.json()

@pytest.mark.parametrize('ext,content',[('png',image()),('jpg',image('JPEG')),('jpeg',image('JPEG')),('pdf',pdf()),('xlsx',write_workbook([sheet()]))])
def test_valid_formats_saved_real_bytes_protected_download(upload_env,ext,content):
    c,path,data,settings,worker=upload_env;bid=batch(c);r=upload(c,bid,content,'成本报价.'+ext);assert r.status_code==201,r.text
    f=r.json();detail=c.get('/api/uploads/batches/'+bid).json();assert len(detail['files'])==1;assert detail['files'][0]['task'] is None
    response=c.get('/api/uploads/files/'+f['id']+'/original');assert response.status_code==200;assert response.content==content
    assert 'attachment' in response.headers['content-disposition'];assert response.headers['x-content-type-options']=='nosniff';assert response.headers['cache-control']=='no-store'
    with connect(path) as db:
        stored=db.execute('SELECT storage_key FROM uploaded_files').fetchone()[0];assert stored.startswith(f['id']);assert (settings.data_dir/'uploads'/stored).read_bytes()==content
        assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0

@pytest.mark.parametrize('name,content,word',[
 ('old.xls',b'\xd0\xcf\x11\xe0','另存'),('bad.exe',b'MZbad','仅支持'),('fake.pdf',image(),'扩展名'),('fake.png',pdf(),'扩展名'),('fake.xlsx',b'notzip','加密'),
 ('empty.jpg',b'','为空'),('broken.png',image()[:22],'损坏'),('broken.jpg',image('JPEG')[:25],'损坏'),('broken.pdf',b'%PDF-1.7\nbad','损坏'),('encrypted.pdf',pdf(encrypted=True),'加密'),('long.pdf',pdf(51),'50')])
def test_invalid_files_leave_no_files_or_rows(upload_env,name,content,word):
    c,path,data,settings,worker=upload_env;r=upload(c,batch(c),content,name);assert r.status_code==422,r.text;assert word in r.json()['error']['message']
    with connect(path) as db:assert db.execute('SELECT count(*) FROM uploaded_files').fetchone()[0]==0
    assert not list((settings.data_dir/'uploads').iterdir())

@pytest.mark.parametrize('part',['xl/vbaProject.bin','xl/externalLinks/externalLink1.xml'])
def test_reject_macro_external_link(upload_env,part):
    c,*_=upload_env;out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        with ZipFile(BytesIO(write_workbook([sheet()]))) as original:
            for name in original.namelist():z.writestr(name,original.read(name))
        z.writestr(part,b'not executed')
    r=upload(c,batch(c),out.getvalue(),'bad.xlsx');assert r.status_code==422;assert '宏' in r.text

def test_limits_batch_and_stream(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c)
    for i in range(10):assert upload(c,bid,image(color=(i*20,0,0))).status_code==201
    r=upload(c,bid,image(color='blue'));assert r.status_code==422 and '10' in r.text
    assert c.get('/api/uploads/limits').json()['max_file_bytes']==20*1024*1024
    small=replace(settings,data_dir=settings.data_dir/'small',log_dir=settings.log_dir/'small',upload_max_bytes=100)
    with TestClient(create_app(small)) as s:
        login_admin(s)
        r=upload(s,batch(s),b'x'*101);assert r.status_code==413
    with pytest.raises(InvalidFile):validate_file(pdf(2),'test.pdf',replace(settings,pdf_max_pages=1))

def test_duplicate_same_bytes_concurrent_different_name(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);content=image()
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda name:upload(c,bid,content,name),('first.png','second.png')))
    assert all(r.status_code==201 for r in responses);assert sum(r.json()['duplicate'] for r in responses)==1;assert len({r.json()['id'] for r in responses})==1
    other=batch(c);r=upload(c,other,content,'rename.png').json();assert r['duplicate'] and r['batch_id']==bid
    with connect(path) as db:assert db.execute('SELECT count(*) FROM uploaded_files').fetchone()[0]==1

def test_fixed_excel_task_result_commit_raw_immutable_no_auto_publish(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,write_workbook([sheet()]),'test.xlsx').json();t=enqueue(c,f['id']);assert t['status']=='waiting'
    assert enqueue(c,f['id'])['reused'];assert worker.run_once()
    done=task(c,t['id']);assert done['status']=='success' and done['attempts']==1;result=done['result'];assert result['counts']['total']==1
    assert enqueue(c,f['id'])['reused'];assert not worker.run_once()
    with connect(path) as db:assert db.execute('SELECT count(*) FROM draft_rows').fetchone()[0]==0
    iid=result['import_id'];preview=c.get('/api/excel/imports/'+iid).json();row=preview['rows'][0]
    selection={'row_ids':[row['id']]};assert c.post('/api/excel/imports/'+iid+'/commit',json=selection).status_code==200
    assert c.post('/api/excel/imports/'+iid+'/commit',json=selection).json()['already_committed']
    draft=c.get('/api/manual-drafts').json()['items'][0];changed={**draft['data'],'remark':'人工校对后'}
    assert c.put(f"/api/manual-drafts/{draft['id']}?expected_revision=1",json=changed).status_code==200
    assert task(c,t['id'])['result']==result;assert draft['evidence'][0]['original_name']=='test.xlsx'
    with connect(path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0

@pytest.mark.parametrize('name,content',[('picture.png',image()),('scan.pdf',pdf()),('other.xlsx',write_workbook([sheet(headers=['非固定表格'])]))],ids=['picture','pdf','other_excel'])
def test_ai_unconfigured_real_failure_retry_history(upload_env,name,content):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,content,name).json();t=enqueue(c,f['id']);worker.run_once();failed=task(c,t['id']);assert failed['status']=='failed';assert failed['error_code']=='AI_NOT_CONFIGURED';assert failed['result'] is None
    assert enqueue(c,f['id'])['id']==t['id'];worker.run_once();failed=task(c,t['id']);assert failed['attempts']==2;assert len(failed['attempt_history'])==2;assert all(a['status']=='failed' for a in failed['attempt_history'])
    assert c.get('/api/uploads/files/'+f['id']+'/original').content==content
    with connect(path) as db:assert db.execute('SELECT count(*) FROM draft_rows').fetchone()[0]==0

def test_multiple_excel_same_batch_and_locked_commit(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);imports=[]
    for name in ('One','Two'):
        f=upload(c,bid,write_workbook([sheet(name=name)]),name+'.xlsx').json();t=enqueue(c,f['id']);worker.run_once();done=task(c,t['id']);assert done['status']=='success';imports.append(done['result']['import_id'])
    with connect(path) as db:db.execute("UPDATE upload_batches SET status='pending' WHERE id=?",(bid,))
    for iid in imports:
        row=c.get('/api/excel/imports/'+iid).json()['rows'][0]
        assert c.post('/api/excel/imports/'+iid+'/commit',json={'row_ids':[row['id']]}).status_code==409
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM excel_imports').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM draft_rows').fetchone()[0]==0

def test_recovery_waiting_processing_and_old_run_cannot_publish(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,write_workbook([sheet()]),'test.xlsx').json();t=enqueue(c,f['id'])
    token=uuid4().hex
    with connect(path) as db:
        db.execute("UPDATE extraction_tasks SET status='processing',attempts=1,run_token=? WHERE id=?",(token,t['id']))
        db.execute("INSERT INTO extraction_attempts(id,task_id,attempt,status) VALUES (?,?,1,'processing')",(token,t['id']))
    recovered=ExtractionWorker(path,settings);recovered.recover();state=task(c,t['id']);assert state['status']=='waiting';assert state['attempt_history'][0]['error_code']=='INTERRUPTED'
    assert recovered.run_once();state=task(c,t['id']);assert state['status']=='success';assert state['attempts']==2
    assert len(state['attempt_history'])==2;assert not recovered.run_once()
    with connect(path) as db:assert db.execute('SELECT count(*) FROM excel_imports').fetchone()[0]==1

def test_missing_tampered_and_storage_path_escape(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,image()).json()
    with connect(path) as db:key=db.execute('SELECT storage_key FROM uploaded_files').fetchone()[0]
    saved=settings.data_dir/'uploads'/key;saved.write_bytes(b'tampered');t=enqueue(c,f['id']);worker.run_once();assert task(c,t['id'])['error_code']=='INVALID_FILE'
    saved.unlink();assert c.get('/api/uploads/files/'+f['id']+'/original').status_code==404;enqueue(c,f['id']);worker.run_once();assert task(c,t['id'])['error_code']=='FILE_UNAVAILABLE'
    with connect(path) as db:db.execute("UPDATE uploaded_files SET storage_key='../../.env' WHERE id=?",(f['id'],))
    assert c.get('/api/uploads/files/'+f['id']+'/original').status_code==422

def test_unauthorized_actor_no_header_bypass(upload_env,monkeypatch):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,image()).json();t=enqueue(c,f['id'])
    import backend.app.uploads as uploads
    monkeypatch.setattr(uploads,'actor',lambda db:{'id':'other','role':'business','username':'业务员'})
    assert c.get('/api/uploads/files/'+f['id']+'/original',headers={'X-Role':'admin'}).status_code==403
    assert c.get('/api/uploads/batches/'+bid).status_code==403;assert c.get('/api/uploads/tasks/'+t['id']).status_code==403
    assert c.get('/api/uploads/batches').json()['items']==[]

def test_file_write_transaction_failure_removes_orphan(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c)
    with connect(path) as db:db.execute("CREATE TRIGGER fail_file BEFORE INSERT ON uploaded_files BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):upload(c,bid,image())
    assert not list((settings.data_dir/'uploads').iterdir())
    with connect(path) as db:assert db.execute('SELECT count(*) FROM uploaded_files').fetchone()[0]==0

def test_result_save_failure_never_marks_success_and_retry(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,write_workbook([sheet()]),'test.xlsx').json();t=enqueue(c,f['id'])
    with connect(path) as db:db.execute("CREATE TRIGGER fail_result BEFORE INSERT ON excel_imports BEGIN SELECT RAISE(ABORT,'fixture save failure'); END")
    assert worker.run_once();state=task(c,t['id']);assert state['status']=='failed';assert state['error_code']=='PERSIST_FAILED';assert state['result'] is None
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM excel_imports').fetchone()[0]==0
        db.execute('DROP TRIGGER fail_result')
    enqueue(c,f['id']);worker.run_once();state=task(c,t['id']);assert state['status']=='success';assert state['attempts']==2

def test_old_worker_fenced_after_recovery(upload_env,monkeypatch):
    import threading
    import backend.app.uploads as uploads
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,write_workbook([sheet()]),'test.xlsx').json();t=enqueue(c,f['id']);entered=threading.Event();resume=threading.Event();original=uploads.parse_rows
    def blocked(*args,**kwargs):
        entered.set();assert resume.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(uploads,'parse_rows',blocked)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(worker.run_once);assert entered.wait(5);recovered=ExtractionWorker(path,settings);recovered.recover();resume.set();assert future.result(timeout=5)
    assert task(c,t['id'])['status']=='waiting'
    with connect(path) as db:assert db.execute('SELECT count(*) FROM excel_imports').fetchone()[0]==0
    monkeypatch.setattr(uploads,'parse_rows',original);assert recovered.run_once();assert task(c,t['id'])['status']=='success'

def test_task_id_and_evidence_link_on_commit(upload_env):
    c,path,data,settings,worker=upload_env;bid=batch(c);f=upload(c,bid,write_workbook([sheet()]),'test.xlsx').json();t=enqueue(c,f['id']);worker.run_once();result=task(c,t['id'])['result'];row=result['rows'][0]
    assert c.post('/api/excel/imports/'+result['import_id']+'/commit',json={'row_ids':[row['id']]}).status_code==200
    with connect(path) as db:
        assert db.execute('SELECT task_id FROM draft_rows').fetchone()[0]==t['id']
        assert db.execute('SELECT task_id FROM source_evidence').fetchone()[0]==t['id']
    assert c.get('/api/manual-drafts').json()['items'][0]['evidence'][0]['file_id']==f['id']

def test_upgrade_v4_preserves_existing_excel_import_and_original(tmp_path,monkeypatch):
    import backend.app.database as database
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai');path=settings.data_dir/'asw.sqlite3';migrations=database.MIGRATIONS
    monkeypatch.setattr(database,'MIGRATIONS',migrations[:4]);initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO upload_batches(id) VALUES ('legacy-batch')")
        db.execute("INSERT INTO uploaded_files(id,batch_id,original_name,storage_key,sha256,media_type,size_bytes) VALUES ('legacy-file','legacy-batch','旧原件.xlsx','abc.xlsx','digest','application/octet-stream',12)")
        db.execute("INSERT INTO excel_imports(id,batch_id,file_id,sha256,options_json,preview_json) VALUES ('legacy-import','legacy-batch','legacy-file','digest','{}','{}')")
    monkeypatch.setattr(database,'MIGRATIONS',migrations);initialize(path)
    with connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT id,file_id FROM excel_imports').fetchone()==('legacy-import','legacy-file')
        assert db.execute('SELECT metadata_json FROM uploaded_files').fetchone()[0]=='{}'

def test_encrypted_zip_and_formula_never_execute(upload_env):
    c,path,data,settings,worker=upload_env
    from backend.tests.test_excel import mutate
    from backend.app.excel_format import MAIN
    from xml.etree import ElementTree as E
    content=write_workbook([sheet()])
    def formula(root):
        node=root.find(".//{*}c[@r='G8']");E.SubElement(node,'{'+MAIN+'}f').text='WEBSERVICE("https://example.invalid/")'
    content=mutate(content,'xl/worksheets/sheet1.xml',formula)
    f=upload(c,batch(c),content,'formula.xlsx').json();t=enqueue(c,f['id']);worker.run_once();state=task(c,t['id']);assert state['status']=='success';assert state['result']['counts']['importable']==0
    assert state['result']['rows'][0]['fatal_errors']
    encrypted=bytearray(write_workbook([sheet()]));start=encrypted.index(b'PK\x03\x04');encrypted[start+6]|=1
    pos=0
    while True:
        pos=encrypted.find(b'PK\x01\x02',pos)
        if pos<0:break
        encrypted[pos+8]|=1;pos+=4
    r=upload(c,batch(c),bytes(encrypted),'encrypted.xlsx');assert r.status_code==422;assert '加密' in r.text

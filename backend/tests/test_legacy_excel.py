"""Synthetic BIFF8 fixtures, no provider calls or production data."""
from pathlib import Path
import sqlite3
import pytest
from backend.app.excel_format import read_xlsx
from backend.app.ai_extract import prepare_document,source_text,Source,AIError
from backend.app.database import connect
from backend.tests.test_uploads import upload_env,batch,upload,enqueue,task

FIXTURES=Path(__file__).parent/'fixtures'

def test_xls_cells_dates_formulas_merges():
    s=read_xlsx((FIXTURES/'legacy-rates.xls').read_bytes())[0]
    assert s['cells']['B3']=='100'
    assert s['cells']['B5']=='2026-10-18'
    assert 'B6' in s['formulas'] and s['cells']['B6']==''
    assert ('A',1,'D',1) in s['merges']

@pytest.mark.parametrize('name',['rates.xls','rates.XLS','rates.xlsx','rates.xsl'])
def test_legacy_upload_local_extraction_and_original(upload_env,monkeypatch,name):
    import backend.app.ai_extract as ai
    monkeypatch.setattr(ai,'call_deepseek',lambda *_:pytest.fail('local file must not be sent'))
    c,path,data,settings,worker=upload_env
    content=(FIXTURES/'legacy-rates.xls').read_bytes()
    r=upload(c,batch(c),content,name);assert r.status_code==201,r.text
    f=r.json();assert f['metadata']['excel_format']=='xls'
    assert c.get('/api/uploads/files/'+f['id']+'/original').content==content
    t=enqueue(c,f['id']);worker.run_once();done=task(c,t['id'])
    assert done['status']=='success',done
    rows=done['result']['rows'];sg=next(r for r in rows if r['raw']['original_port_code']=='SGSGP')
    assert sg['data']['prices']=={'20GP':'100.00','40GP':'200.00','40HQ':'200.00'}
    assert sg['data']['valid_to']=='2026-10-24' and sg['data']['remark']=='限普货'
    iid=done['result']['ai_import_id']
    assert c.post('/api/ai/imports/'+iid+'/commit',json={'row_ids':[sg['id']]}).status_code==200
    from backend.tests.test_review import submit,decide
    draft_row=next(r for r in c.get('/api/manual-drafts').json()['items'] if r['id']==sg['id'])
    assert decide(c,submit(c,draft_row['batch_id'])).status_code==200
    found=c.post('/api/quotes/search',json={'date_scope':'all'}).json()
    assert found['total']==1 and found['items'][0]['prices']['20GP']=='100.00'
    with connect(path) as db:assert db.execute('SELECT count(*) FROM ai_calls').fetchone()[0]==0
    from backend.app.backups import create_backup,restore_backup
    archive=create_backup(path,settings.data_dir)
    destination=settings.data_dir.parent/'restored'
    restore_backup(settings.data_dir/'backups'/archive['name'],destination)
    assert any(p.read_bytes()==content for p in (destination/'uploads').iterdir())

@pytest.mark.parametrize('extension',['xls','xsl'])
def test_xlsx_renamed_legacy_extension(upload_env,extension):
    from backend.tests.test_pod_enrichment import fixture
    c,path,data,settings,worker=upload_env
    r=upload(c,batch(c),fixture(),'renamed.'+extension)
    assert r.status_code==201,r.text
    assert r.json()['metadata']['excel_format']=='xlsx' and r.json()['metadata']['extension_mismatch']

@pytest.mark.parametrize('content',[b'<xsl:stylesheet/>',b'<html><table><tr><td>100</td></tr></table></html>',b'\xd0\xcf\x11\xe0bad'])
def test_invalid_or_fake_legacy_rejected(upload_env,content):
    c,*_=upload_env
    r=upload(c,batch(c),content,'bad.xls')
    assert r.status_code==422 and '另存' in r.text


def test_legacy_unstructured_ai_document_and_formula_guard(upload_env):
    c,path,data,settings,worker=upload_env
    content=(FIXTURES/'legacy-unstructured.xls').read_bytes()
    doc=prepare_document(content,{'kind':'xlsx'},settings)
    assert doc.kind=='xlsx' and doc.sheets['Other']['cells']['B2']=='1000'
    content=(FIXTURES/'legacy-rates.xls').read_bytes()
    doc=prepare_document(content,{'kind':'xlsx'},settings)
    with pytest.raises(AIError):
        source_text(Source(id='x',worksheet='青岛报价',cell_range='B6',excerpt='300'),doc,{'worksheet':'青岛报价','rows':[6,6]},True)


def test_legacy_fixed_template_import_preview(upload_env):
    c,path,data,settings,worker=upload_env
    content=(FIXTURES/'legacy-template.xls').read_bytes()
    r=c.post('/api/excel/imports/preview',params={'filename':'fixed.xls'},content=content,headers={'Content-Type':'application/octet-stream'})
    assert r.status_code==200,r.text
    assert r.json()['rows'][0]['data']['prices']['20GP']=='1000.25'
    assert r.json()['rows'][0]['data']['valid_from']=='2026-11-01'
    with connect(path) as db:
        row=db.execute('SELECT storage_key,media_type FROM uploaded_files').fetchone()
        assert row[0].endswith('.xls') and row[1]=='application/vnd.ms-excel'

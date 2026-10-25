from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import time
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.database import connect
from backend.app.backups import create_backup, restore_backup
from backend.tests.auth_helpers import TEST_PASSWORD, TEST_HASH
from backend.tests.quote_fixtures import sample_data
from backend.tests.test_uploads import image

@pytest.fixture
def auth_env(tmp_path):
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai');app=create_app(settings)
    with TestClient(app) as c:
        app.state.extraction_worker.stop()
        temporary=(settings.data_dir/'admin-initial.txt').read_text().splitlines()[1].split('：',1)[1]
        yield c,settings,app,temporary

def login(c,name,password):
    response=c.post('/api/auth/login',json={'username':name,'password':password})
    if response.status_code==200:c.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response

def ready(c,temporary):
    assert login(c,'admin',temporary).status_code==200
    response=c.post('/api/auth/password',json={'current_password':temporary,'new_password':TEST_PASSWORD});assert response.status_code==200,response.text
    assert login(c,'admin',TEST_PASSWORD).status_code==200

def staff(c,app,name='staff',role='business'):
    response=c.post('/api/auth/users',json={'username':name,'role':role});assert response.status_code==201,response.text
    record=response.json();other=TestClient(app);temporary=record['temporary_password'];assert login(other,name,temporary).status_code==200
    assert other.post('/api/auth/password',json={'current_password':temporary,'new_password':TEST_PASSWORD}).status_code==200
    assert login(other,name,TEST_PASSWORD).status_code==200
    return other,record['id']

def test_bootstrap_login_forced_change_logout_and_hash(auth_env):
    c,settings,app,temporary=auth_env
    assert (settings.data_dir/'admin-initial.txt').stat().st_mode&0o777==0o600
    assert c.post('/api/quotes/search',json={}).status_code==401
    response=login(c,'admin',temporary);assert response.status_code==200;assert 'HttpOnly' in response.headers['set-cookie'];assert 'SameSite=strict' in response.headers['set-cookie']
    assert c.get('/api/auth/me').json()['user']['must_change_password']==1
    assert c.get('/api/system').status_code==403
    assert c.post('/api/auth/password',json={'current_password':temporary,'new_password':TEST_PASSWORD}).status_code==200
    assert not (settings.data_dir/'admin-initial.txt').exists();assert c.get('/api/system').status_code==401
    assert login(c,'admin',temporary).status_code==401;assert login(c,'admin',TEST_PASSWORD).status_code==200
    assert c.get('/api/system').status_code==200
    with connect(app.state.database_path) as db:
        stored=db.execute("SELECT password_hash FROM users WHERE id='local-admin'").fetchone()[0];assert stored.startswith('scrypt$32768$8$3$');assert TEST_PASSWORD not in stored
        assert db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]!=c.cookies.get('asw_session')
    assert c.post('/api/auth/logout').status_code==200;assert c.get('/api/auth/me').status_code==401

@pytest.mark.parametrize('path,method',[('/api/system','get'),('/api/manual-drafts','get'),('/api/quotes/search','post'),('/api/catalog/ports','get'),('/api/excel/export','post'),('/api/uploads/files/any/original','get'),('/api/backups','get'),('/api/review/batches','get')])
def test_unauthenticated_data_blocked(auth_env,path,method):
    c,*_=auth_env;response=getattr(c,method)(path,**({'json':{}} if method=='post' else {}));assert response.status_code==401

def test_csrf_cross_origin_expiration_and_forged_role(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary)
    token=c.headers.pop('X-CSRF-Token');assert c.post('/api/manual-drafts',json={}).status_code==403;c.headers['X-CSRF-Token']=token
    assert c.post('/api/manual-drafts',json={},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert c.post('/api/auth/login',json={'username':'admin','password':TEST_PASSWORD},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert c.get('/api/system',headers={'Host':'evil.invalid'}).status_code==400
    with connect(app.state.database_path) as db:db.execute('UPDATE auth_sessions SET expires_at=?',(int(time.time())-1,))
    assert c.get('/api/system',headers={'X-Role':'admin','X-Actor-ID':'local-admin'}).status_code==401

def test_business_permissions_and_draft_ownership(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary);a,uid=staff(c,app);b,other_id=staff(c,app,'other')
    for path in ('/api/auth/users','/api/backups'):
        assert a.get(path).status_code==403
    assert a.post('/api/catalog/carriers',json={'name':'forbidden'}).status_code==403
    assert a.post('/api/review/submissions/fake/decision',json={'operation_key':'operation-test','decision':'approved'}).status_code==403
    assert a.post('/api/quotes/fake/disable',json={'reason':'test'}).status_code==403
    assert a.post('/api/quotes/search',json={}).status_code==200
    draft=a.post('/api/manual-drafts',json={}).json();assert b.get('/api/manual-drafts').json()['total']==0
    assert b.put('/api/manual-drafts/'+draft['id']+'?expected_revision=1',json={}).status_code in (403,404)
    assert b.get('/api/review/batches/'+draft['batch_id']).status_code==403
    assert b.get('/api/review/batches').json()['items']==[]
    assert len(c.get('/api/manual-drafts').json()['items'])==1
    bid=a.post('/api/uploads/batches').json()['id'];file=a.post(f'/api/uploads/batches/{bid}/files?filename=test.png',content=image()).json()
    assert b.get(f"/api/uploads/files/{file['id']}/original").status_code==403
    assert c.get(f"/api/uploads/files/{file['id']}/original").status_code==200

def test_deactivation_role_change_reset_revoke_and_last_admin(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary);other,uid=staff(c,app)
    assert c.put('/api/auth/users/local-admin',json={'active':False,'role':'admin'}).status_code==422
    assert c.put('/api/auth/users/'+uid,json={'active':False,'role':'business'}).status_code==200
    assert other.get('/api/system').status_code==401;assert login(other,'staff',TEST_PASSWORD).status_code==401
    assert c.put('/api/auth/users/'+uid,json={'active':True,'role':'admin'}).status_code==200;assert login(other,'staff',TEST_PASSWORD).status_code==200
    reset=c.post(f'/api/auth/users/{uid}/reset-password').json();assert other.get('/api/system').status_code==401
    assert login(other,'staff',TEST_PASSWORD).status_code==401
    assert login(other,'staff',reset['temporary_password']).json()['user']['must_change_password']==1
    assert other.get('/api/system').status_code==403

def test_login_rate_limit_persists_in_db(auth_env):
    c,settings,app,temporary=auth_env
    for _ in range(5):assert login(c,'admin','wrong-password').status_code==401
    assert login(c,'admin',temporary).status_code==429
    with connect(app.state.database_path) as db:assert db.execute('SELECT max(failures) FROM auth_failures').fetchone()[0]==5

def test_user_limit_and_duplicate_names(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary)
    for i in range(9):assert c.post('/api/auth/users',json={'username':f'staff{i}'}).status_code==201
    assert c.post('/api/auth/users',json={'username':'eleventh'}).status_code==422
    assert all('password_hash' not in row for row in c.get('/api/auth/users').json()['items'])

def test_backup_restore_quotes_evidence_accounts_and_sessions(auth_env,tmp_path):
    c,settings,app,temporary=auth_env;ready(c,temporary)
    data=sample_data(app.state.database_path)
    bid=c.post('/api/uploads/batches').json()['id'];content=image();file=c.post(f'/api/uploads/batches/{bid}/files?filename=original.png',content=content).json()
    draft=c.post('/api/manual-drafts',json=data).json();detail=c.get('/api/review/batches/'+draft['batch_id']).json();sid=c.post(f"/api/review/batches/{draft['batch_id']}/submit",json={'token':detail['preview']['token']}).json()['submission_id']
    assert c.post(f'/api/review/submissions/{sid}/decision',json={'operation_key':'backup-approval','decision':'approved','reason':''}).status_code==200
    response=c.post('/api/backups');assert response.status_code==200,response.text;backup=response.json();assert backup['file_count']==1
    archive=settings.data_dir/'backups'/backup['name'];download=c.get('/api/backups/'+backup['name']);assert download.content==archive.read_bytes()
    destination=tmp_path/'restored';restore_backup(archive,destination)
    restored=create_app(replace(settings,data_dir=destination,log_dir=tmp_path/'restored-logs'))
    with TestClient(restored) as other:
        other.cookies.update(c.cookies)
        assert other.get('/api/system').status_code==401
        assert login(other,'admin',TEST_PASSWORD).status_code==200
        assert other.post('/api/quotes/search',json={'date_scope':'all'}).json()['total']==1
        assert other.get(f"/api/uploads/files/{file['id']}/original").content==content
        with connect(restored.state.database_path) as db:
            assert db.execute('SELECT count(*) FROM approval_records').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM source_evidence WHERE quote_version_id IS NOT NULL').fetchone()[0]>=1
    with pytest.raises(ValueError):restore_backup(archive,destination)

@pytest.mark.parametrize('damage',['database','original','traversal','extra'])
def test_restore_rejects_tampering_without_creating_destination(auth_env,tmp_path,damage):
    c,settings,app,temporary=auth_env;ready(c,temporary);bid=c.post('/api/uploads/batches').json()['id'];c.post(f'/api/uploads/batches/{bid}/files?filename=original.png',content=image())
    record=c.post('/api/backups').json();archive=settings.data_dir/'backups'/record['name'];bad=tmp_path/'bad.zip'
    with ZipFile(archive) as source,ZipFile(bad,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            raw=source.read(name)
            if damage=='database' and name=='asw.sqlite3':raw=b'bad database'
            if damage=='original' and name.startswith('uploads/'):raw=b'bad image'
            if damage=='traversal' and name=='manifest.json':
                manifest=json.loads(raw);manifest['files'][0]['storage_key']='../../escape';raw=json.dumps(manifest).encode()
            target.writestr(name,raw)
        if damage=='extra':target.writestr('../../escape',b'bad')
    destination=tmp_path/'rejected'
    with pytest.raises(ValueError):restore_backup(bad,destination)
    assert not destination.exists();assert not (tmp_path/'escape').exists()

def test_backup_refuses_missing_original(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary);bid=c.post('/api/uploads/batches').json()['id'];c.post(f'/api/uploads/batches/{bid}/files?filename=original.png',content=image())
    next((settings.data_dir/'uploads').iterdir()).unlink()
    response=c.post('/api/backups');assert response.status_code==422
    assert not list((settings.data_dir/'backups').glob('backup-*.zip'))


def test_password_change_revokes_other_sessions(auth_env):
    c,settings,app,temporary=auth_env;ready(c,temporary)
    other=TestClient(app);assert login(other,'admin',TEST_PASSWORD).status_code==200
    changed='New-isolated-password-2026!'
    assert c.post('/api/auth/password',json={'current_password':TEST_PASSWORD,'new_password':changed}).status_code==200
    assert c.get('/api/system').status_code==401
    assert other.get('/api/system').status_code==401
    assert login(other,'admin',TEST_PASSWORD).status_code==401
    assert login(other,'admin',changed).status_code==200

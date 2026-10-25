from backend.tests.auth_helpers import login_admin
import sqlite3
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.database import MIGRATIONS


def test_database_survives_restart_and_migrations_are_idempotent(tmp_path):
    settings = Settings(tmp_path / 'data', tmp_path / 'logs', 'Asia/Shanghai')
    with TestClient(create_app(settings)) as client:
        login_admin(client)
        assert client.get('/api/health').json() == {'status': 'ok', 'database': 'ok', 'schema_version': MIGRATIONS[-1][0]}
        installation = client.get('/api/system').json()['installation_id']
    # An independent database connection writes a probe; it is not an exposed write API.
    with sqlite3.connect(settings.data_dir / 'asw.sqlite3') as db:
        db.execute("INSERT INTO application_meta VALUES ('test_probe', 'persisted')")
    with TestClient(create_app(settings)) as client:
        login_admin(client)
        assert client.get('/api/system').json()['installation_id'] == installation
        assert client.get('/api/system').json()['timezone'] == 'Asia/Shanghai'
    with sqlite3.connect(settings.data_dir / 'asw.sqlite3') as db:
        assert db.execute("SELECT value FROM application_meta WHERE key='test_probe'").fetchone()[0] == 'persisted'
        assert db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == len(MIGRATIONS)
    assert 'startup completed' in (settings.log_dir / 'app.log').read_text()


def test_error_responses_and_frontend_are_served(tmp_path):
    with TestClient(create_app(Settings(tmp_path / 'data', tmp_path / 'logs', 'Asia/Shanghai'))) as client:
        login_admin(client)
        missing = client.get('/api/not-found')
        assert missing.status_code == 404
        assert missing.json()['error']['message'] == '接口或页面不存在'
        assert missing.headers['X-Request-ID']
        page = client.get('/')
        assert page.status_code == 200
        assert '<div id="app"></div>' in page.text


def test_database_failure_is_reported_without_internal_details(tmp_path, monkeypatch):
    import backend.app.main as main
    with TestClient(create_app(Settings(tmp_path / 'data', tmp_path / 'logs', 'Asia/Shanghai')), raise_server_exceptions=False) as client:
        login_admin(client)
        def broken_connection(path):
            raise sqlite3.OperationalError('private-database-detail')
        monkeypatch.setattr(main, 'connect', broken_connection)
        response = client.get('/api/health')
        assert response.status_code == 500
        assert response.json()['error']['request_id']
        assert 'private-database-detail' not in response.text

"""Real session login for isolated regression fixtures; no application auth bypass."""
import sqlite3
from backend.app.auth import password_hash
TEST_PASSWORD='Isolated-test-password-2026!'
TEST_HASH=password_hash(TEST_PASSWORD)
def login_admin(client):
    with sqlite3.connect(client.app.state.database_path) as db:
        db.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id='local-admin'",(TEST_HASH,))
    response=client.post('/api/auth/login',json={'username':'admin','password':TEST_PASSWORD})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response.json()

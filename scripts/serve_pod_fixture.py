"""Local UI fixture. No AI and no production data. Actual file is selected by the test only."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app.config import Settings
from backend.app.database import initialize,connect
from backend.app.auth import bootstrap
from backend.app.main import create_app
from backend.tests.auth_helpers import TEST_HASH
folder=Path(sys.argv[1]).resolve()
if not folder.name.startswith('asw-pod-ui-'):raise SystemExit('Explicit disposable directory required')
s=Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=False,ai_key_file=folder/'absent')
db=s.data_dir/'asw.sqlite3';initialize(db);bootstrap(db,s)
with connect(db) as c:c.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id='local-admin'",(TEST_HASH,))
uvicorn.run(create_app(s),host='127.0.0.1',port=8004,proxy_headers=False)

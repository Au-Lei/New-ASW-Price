"""Isolated browser acceptance: synthetic Excel, deliberately slow local parsing, no AI."""
import sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app import uploads
from backend.app.config import Settings
from backend.app.database import initialize,connect
from backend.app.auth import bootstrap
from backend.app.main import create_app
from backend.tests.auth_helpers import TEST_HASH
from backend.tests.quote_fixtures import sample_data
from backend.tests.test_excel import sheet
from backend.app.excel_format import write_workbook
folder=Path(sys.argv[1]).resolve()
if not folder.name.startswith('asw-progress-fixture-') or folder.exists():raise SystemExit('Require a new isolated asw-progress-fixture-* directory')
folder.mkdir(parents=True)
settings=Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=False,ai_key_file=folder/'missing')
path=settings.data_dir/'asw.sqlite3';initialize(path);bootstrap(path,settings);sample_data(path)
with connect(path) as db:db.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id='local-admin'",(TEST_HASH,))
(folder/'synthetic.xlsx').write_bytes(write_workbook([sheet()]))
parse=uploads.parse_rows

def slow(*args,**kwargs):
    time.sleep(7)
    return parse(*args,**kwargs)
uploads.parse_rows=slow
uvicorn.run(create_app(settings),host='127.0.0.1',port=8004,proxy_headers=False)

"""Disposable real backend for browser acceptance of the supplied demonstration UI."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.database import initialize,connect
from backend.app.auth import bootstrap
from backend.tests.auth_helpers import TEST_HASH
from backend.tests.quote_fixtures import sample_data,insert_published
from backend.tests.test_excel import sheet
from backend.app.excel_format import HEADERS,EXTRAS,write_workbook
folder=Path(sys.argv[1]).resolve()
if not folder.name.startswith('asw-original-ui-'):raise SystemExit('Only an explicitly disposable asw-original-ui-* directory is supported')
folder.mkdir(parents=True,exist_ok=True)
s=Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=False,ai_key_file=folder/'missing-key');database=s.data_dir/'asw.sqlite3'
initialize(database);bootstrap(database,s)
with connect(database) as db:db.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id='local-admin'",(TEST_HASH,))
value=sample_data(database);value['path_text']='青岛-鹿特丹';insert_published(database,value)
x=sheet();x['headers']=HEADERS+EXTRAS;x['rows'][0]+= ['青岛','ETD','海运费','PSS另计','目的港费用待确认','','测试直达服务','direct','测试代理a','测试协议a','','','']
(folder/'synthetic.xlsx').write_bytes(write_workbook([x]))
uvicorn.run(create_app(s),host='127.0.0.1',port=8004,proxy_headers=False)

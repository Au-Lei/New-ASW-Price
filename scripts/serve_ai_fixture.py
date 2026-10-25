"""Serve the isolated synthetic live-acceptance DB for browser QA; never production."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app.config import Settings
from backend.app.main import create_app
folder=Path(sys.argv[1]).resolve()
if not folder.name.startswith('asw-task8-live-') or not (folder/'report.json').exists():raise SystemExit('Requires completed synthetic live fixture')
uvicorn.run(create_app(Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=True)),host='127.0.0.1',port=8003,proxy_headers=False)

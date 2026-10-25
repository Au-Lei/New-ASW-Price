"""Live image regression: sends only a newly generated synthetic 15-row table."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.auth_helpers import login_admin
from backend.tests.test_uploads import batch,upload,enqueue,task
from backend.tests.test_ai import consent
from backend.app.database import connect

def main():
    folder=Path(tempfile.mkdtemp(prefix='asw-image-bands-live-'))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24)
    image=Image.new('RGB',(1100,1150),'white');draw=ImageDraw.Draw(image)
    lines=['SYNTHETIC TEST ONLY - NOT A REAL QUOTATION',
           'Origin QINGDAO | Carrier TEST | Currency USD',
           'Validity 2026-11-01 to 2026-11-30 (ETD)',
           'ROW | PORT | 20GP | 40GP/HQ | REMARK']
    for i,text in enumerate(lines):draw.text((20,15+i*40),text,font=font,fill='black')
    for i in range(15):
        y=190+i*60;draw.line((15,y-8,1080,y-8),fill='black')
        draw.text((20,y),f'{i+1:02} | ROTTERDAM | {1000+i} | {2000+i} | SAMPLE-{i+1:02}',font=font,fill='black')
    filename=folder/'synthetic-long.png';image.save(filename)
    settings=Settings(folder/'data',folder/'logs','Asia/Shanghai',ai_enabled=True,ai_timeout_seconds=180)
    app=create_app(settings)
    with TestClient(app) as client:
        login_admin(client);worker=app.state.extraction_worker;worker.stop()
        file=upload(client,batch(client),filename.read_bytes(),filename.name).json();consent(client,file['id'])
        t=enqueue(client,file['id']);print('RUN synthetic 15-row image',flush=True);worker.run_once();result=task(client,t['id'])
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False))
        assert result['status']=='success',(result.get('error_code'),result.get('error'))
        rows=result['result']['rows'];amounts=sorted(r['data']['prices'].get('20GP') for r in rows)
        assert amounts==[f'{1000+i}.00' for i in range(15)],('row/price mismatch',len(rows),amounts)
        with connect(app.state.database_path) as db:assert db.execute('SELECT count(*) FROM quotes').fetchone()[0]==0
        print('PASS',json.dumps({'rows':len(rows),'calls':len(result['result']['calls']),'folder':str(folder)}),flush=True)
if __name__=='__main__':main()

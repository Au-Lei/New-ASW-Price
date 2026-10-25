"""Exercise real HTTP, built assets, restart and SQLite using an isolated directory."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import http.cookiejar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run():
    with tempfile.TemporaryDirectory(prefix='asw-smoke-') as directory:
        env = {**os.environ, 'ASW_DATA_DIR': f'{directory}/data', 'ASW_LOG_DIR': f'{directory}/logs', 'ASW_AI_ENABLED':'false', 'ASW_ALLOWED_HOSTS':'127.0.0.1,localhost'}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        ids = []
        catalog_ids = []
        carrier_id = None
        draft_id = None
        import_id = None
        fixed_task = None
        image_task = None
        def post_json(path, data):
            request = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers={'Content-Type':'application/json'}, method='POST')
            with opener.open(request) as response:
                return json.load(response)
        def get_json(path):
            with opener.open(base+path) as response:return json.load(response)
        def wait_task(tid):
            for _ in range(100):
                state=get_json('/api/uploads/tasks/'+tid)
                if state['status'] in ('success','failed'):return state
                time.sleep(.05)
            raise AssertionError('任务未完成')
        def upload_bytes(bid,filename,content):
            request=urllib.request.Request(base+'/api/uploads/batches/'+bid+'/files?filename='+filename,data=content,headers={'Content-Type':'application/octet-stream'},method='POST')
            with opener.open(request) as response:return json.load(response)
        for restart in range(2):
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read().decode())
                    try:
                        with opener.open(base + '/api/health', timeout=1) as response:
                            assert json.load(response)['database'] == 'ok'
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('服务启动超时')
                if restart==0:
                    temporary=(Path(directory)/'data'/'admin-initial.txt').read_text().splitlines()[1].split('：',1)[1]
                    logged=post_json('/api/auth/login',{'username':'admin','password':temporary})
                    opener.addheaders=[('X-CSRF-Token',logged['csrf_token'])]
                    post_json('/api/auth/password',{'current_password':temporary,'new_password':'Isolated-smoke-password-2026!'})
                logged=post_json('/api/auth/login',{'username':'admin','password':'Isolated-smoke-password-2026!'})
                opener.addheaders=[('X-CSRF-Token',logged['csrf_token'])]
                with opener.open(base + '/api/system') as response:
                    ids.append(json.load(response)['installation_id'])
                with opener.open(base + '/api/catalog/summary') as response:
                    catalog = json.load(response)
                    assert catalog['counts']['ports'] == 296 and catalog['quotes'] == (0 if restart==0 else 1)
                with opener.open(base + '/api/catalog/ports?q=ROTTERDAM') as response:
                    catalog_ids.append(json.load(response)['items'][0]['id'])
                if restart == 0:
                    request = urllib.request.Request(base + '/api/catalog/carriers', data=json.dumps({'name':'isolated-smoke-carrier'}).encode(), headers={'Content-Type':'application/json'}, method='POST')
                    with opener.open(request) as response:
                        carrier_id = json.load(response)['id']
                    supplier = post_json('/api/catalog/suppliers', {'name':'isolated-smoke-supplier','kind':'agent'})
                    series = post_json('/api/catalog/source_series', {'name':'isolated-smoke-series','supplier_id':supplier['id']})
                    with opener.open(base + '/api/manual-drafts/defaults') as response:
                        values = json.load(response)['data']
                    with opener.open(base + '/api/catalog/ports?q=ROTTERDAM') as response:
                        destination = json.load(response)['items'][0]
                    values.update(destination_port_id=destination['id'], route_id=destination['route_ids'][0], carrier_id=carrier_id,
                                  source_series_id=series['id'], service_key='isolated-smoke-direct',service_kind='direct',currency='USD',fee_basis='CY-CY',
                                  valid_from='2026-11-01',valid_to='2026-11-30',validity_basis='ETD',origin_confirmed=True,
                                  prices={'20GP':'0.10','40HQ':'0.20'},manual_note='isolated smoke test only')
                    saved = post_json('/api/manual-drafts', values)
                    assert saved['ready_for_review']
                    draft_id = saved['id']
                    from backend.app.excel_format import HEADERS,write_workbook
                    from datetime import date
                    from decimal import Decimal
                    content=write_workbook([{'name':'欧洲航线','metadata':['isolated smoke test only'],'headers':HEADERS,
                        'rows':[['欧洲基本港','NETHERLANDS','荷兰','ROTTERDAM','鹿特丹','isolated-smoke-carrier',Decimal('1.25'),None,None,'USD','',None,'','isolated-smoke-supplier · isolated-smoke-series','',date(2026,11,1),date(2026,11,30),'CY-CY']]}])
                    request=urllib.request.Request(base+'/api/excel/imports/preview?filename=smoke.xlsx',data=content,headers={'Content-Type':'application/octet-stream'},method='POST')
                    with opener.open(request) as response:
                        imported=json.load(response)
                    import_id=imported['id']
                    assert imported['counts']['importable']==1
                    assert post_json('/api/excel/imports/'+import_id+'/commit',{'row_ids':[imported['rows'][0]['id']]})['created']==1
                    upload_batch=post_json('/api/uploads/batches',{})['id']
                    fixed=upload_bytes(upload_batch,'again.xlsx',content)
                    assert fixed['duplicate'] # Existing task-5 original is reused, not copied.
                    fixed_task=post_json('/api/uploads/files/'+fixed['id']+'/extract',{})['id']
                    fixed_done=wait_task(fixed_task)
                    assert fixed_done['status']=='success' and fixed_done['result']['import_id']==import_id
                    from PIL import Image
                    from io import BytesIO
                    png=BytesIO();Image.new('RGB',(30,20),'blue').save(png,format='PNG')
                    img=upload_bytes(upload_batch,'image.png',png.getvalue())
                    image_task=post_json('/api/uploads/files/'+img['id']+'/extract',{})['id']
                    assert wait_task(image_task)['error_code']=='AI_NOT_CONFIGURED'
                    post_json('/api/uploads/files/'+img['id']+'/extract',{})
                    assert wait_task(image_task)['attempts']==2
                    with opener.open(base+'/api/uploads/files/'+img['id']+'/original') as response:
                        assert response.read()==png.getvalue()
                else:
                    with opener.open(base + '/api/catalog/carriers') as response:
                        assert json.load(response)['items'][0]['id'] == carrier_id
                    with opener.open(base + '/api/manual-drafts') as response:
                        rows=json.load(response)['items']
                        assert draft_id in {row['id'] for row in rows}
                        assert any(row['origin_kind']=='excel' and row['evidence'] for row in rows)
                    with opener.open(base+'/api/excel/imports/'+import_id) as response:
                        assert json.load(response)['status']=='committed'
                    recovered=wait_task(fixed_task)
                    assert recovered['status']=='success' and recovered['attempts']==3
                    assert any(a['error_code']=='INTERRUPTED' for a in recovered['attempt_history'])
                    assert wait_task(image_task)['attempts']==2
                assert post_json('/api/quotes/search', {'date_scope':'all'})['total'] == (0 if restart==0 else 1)
                preview = post_json('/api/manual-drafts/search', {'date_scope':'all','quantities':{'20GP':3,'40HQ':1}})
                if restart==0:
                    assert preview['total'] == 1 and preview['items'][0]['subtotal'] == '0.50'
                    bid=saved['batch_id']
                    with opener.open(base+'/api/review/batches/'+bid) as response:
                        token=json.load(response)['preview']['token']
                    sid=post_json('/api/review/batches/'+bid+'/submit',{'token':token})['submission_id']
                    decision={'operation_key':'smoke-operation-task6','decision':'approved','reason':'隔离验收'}
                    assert not post_json('/api/review/submissions/'+sid+'/decision',decision)['repeated']
                    assert post_json('/api/review/submissions/'+sid+'/decision',decision)['repeated']
                else:
                    assert preview['total']==0
                    formal=post_json('/api/quotes/search',{'date_scope':'all','quantities':{'20GP':3,'40HQ':1}})
                    assert formal['items'][0]['subtotal']=='0.50'
                    with opener.open(base+'/api/quotes/'+formal['items'][0]['quote_id']+'/history') as response:
                        assert len(json.load(response)['items'])==1
                with opener.open(base + '/') as response:
                    html = response.read().decode()
                    assert '<div id="app"></div>' in html
                import re
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
                assert assets
                for asset in assets:
                    with opener.open(base + asset) as response:
                        assert response.status == 200 and response.read()
                print(f'HTTP启动／数据库／首页及静态资源：PASS（第{restart + 1}次）')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stderr.close()
            if restart==0:
                # Deterministic crash-state fixture in this disposable DB only.
                # Next real process must recover and fence the interrupted attempt.
                import sqlite3
                from uuid import uuid4
                token=uuid4().hex
                with sqlite3.connect(Path(directory)/'data'/'asw.sqlite3') as db:
                    db.execute("UPDATE extraction_tasks SET status='processing',attempts=2,run_token=? WHERE id=?",(token,fixed_task))
                    db.execute("INSERT INTO extraction_attempts(id,task_id,attempt,status) VALUES (?,?,2,'processing')",(token,fixed_task))
        assert ids[0] == ids[1]
        assert catalog_ids[0] == catalog_ids[1]
        print('真实原件下载／固定模板提取／无AI失败重试／模拟处理中断后的真实重启恢复：PASS')
        print('跨进程重启持久化：PASS')
        print('基础目录及维护资料跨进程持久化：PASS')
        print('草稿对比／提交／审批入库／重复审批幂等／重启后正式查询和版本历史：PASS')
        print('Excel真实上传、草稿提交、来源与批次跨进程重启：PASS')


if __name__ == '__main__':
    run()

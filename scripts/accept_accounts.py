"""Real HTTP account + LAN-interface + complete backup/restore acceptance in temp data."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--lan-ip',default='127.0.0.1');args=parser.parse_args()
PASSWORD='Isolated-accept-password-2026!'

def client(base):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def request(path,data=None,method=None,raw=None):
  body=raw if raw is not None else json.dumps(data).encode() if data is not None else None
  r=urllib.request.Request(base+path,data=body,headers={'Content-Type':'application/octet-stream' if raw is not None else 'application/json'},method=method)
  try:
   with opener.open(r,timeout=30) as response:
    content=response.read();return response.status,json.loads(content) if 'application/json' in response.headers.get('content-type','') else content
  except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
 return opener,request

def login(opener,request,name,password):
 status,value=request('/api/auth/login',{'username':name,'password':password});assert status==200,value
 opener.addheaders=[('X-CSRF-Token',value['csrf_token'])]

with tempfile.TemporaryDirectory(prefix='asw-accounts-') as folder:
 root=Path(folder);data=root/'data'
 with socket.socket() as sock:sock.bind((args.lan_ip,0));port=sock.getsockname()[1]
 base=f'http://{args.lan_ip}:{port}'
 archived=None;fid=None
 for restored in (False,True):
  env={**os.environ,'ASW_AI_ENABLED':'false','ASW_DATA_DIR':str(data),'ASW_LOG_DIR':str(root/'logs'),'ASW_HOST':args.lan_ip,'ASW_PORT':str(port),'ASW_ALLOWED_HOSTS':f'127.0.0.1,localhost,{args.lan_ip}'}
  process=subprocess.Popen(['bash','scripts/start.sh'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  admin,request=client(base)
  try:
   for _ in range(100):
    if process.poll() is not None:raise RuntimeError(process.stderr.read().decode(errors='replace'))
    try:
     if request('/api/health')[0]==200:break
    except OSError:pass
    time.sleep(.1)
   else:raise RuntimeError('启动超时')
   assert request('/api/system')[0]==401
   if not restored:
    temporary=(data/'admin-initial.txt').read_text().splitlines()[1].split('：',1)[1]
    login(admin,request,'admin',temporary)
    assert request('/api/system')[0]==403
    assert request('/api/auth/password',{'current_password':temporary,'new_password':PASSWORD})[0]==200
   login(admin,request,'admin',PASSWORD)
   if not restored:
    _,user=request('/api/auth/users',{'username':'business','role':'business'})
    business,biz=client(base);login(business,biz,'business',user['temporary_password'])
    assert biz('/api/auth/password',{'current_password':user['temporary_password'],'new_password':PASSWORD})[0]==200;login(business,biz,'business',PASSWORD)
    assert biz('/api/backups')[0]==403
    assert biz('/api/catalog/carriers',{'name':'forbidden'})[0]==403
    assert biz('/api/quotes/search',{})[0]==200
    assert request('/api/auth/users/'+user['id'],{'role':'business','active':False},'PUT')[0]==200
    assert biz('/api/system')[0]==401
    from backend.tests.test_uploads import image
    _,batch=request('/api/uploads/batches',{});content=image();code,file=request('/api/uploads/batches/'+batch['id']+'/files?filename=test.png',raw=content);assert code==201,file;fid=file['id']
    _,record=request('/api/backups',{});assert record['file_count']==1,record
    assert request('/api/backups/'+record['name'])[0]==200
    archived=data/'backups'/record['name']
   else:
    assert request('/api/uploads/files/'+fid+'/original')[1]==content
    assert len(request('/api/auth/users')[1]['items'])==2
    print('恢复后原件字节、账号及重新登录：PASS')
   print(('恢复实例' if restored else '原始实例')+'经配置的局域网接口HTTP登录／权限：PASS')
  finally:
   process.terminate()
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:process.kill();process.wait()
   process.stderr.close()
  if not restored:
   # Run the actual restore command, then start an independent server on restored data.
   target=root/'restored'
   subprocess.run([sys.executable,'scripts/manage.py','restore',str(archived),str(target)],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
   data=target
 print('真实完整备份／CLI恢复到空目录／独立HTTP实例：PASS')

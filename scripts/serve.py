"""One app/worker, an explicit LAN address and optional stable loopback access."""
import argparse,errno,ipaddress,socket,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
parser=argparse.ArgumentParser();parser.add_argument('--host',required=True);parser.add_argument('--port',required=True,type=int);parser.add_argument('--loopback',action='store_true');parser.add_argument('--cert');parser.add_argument('--key');args=parser.parse_args()
address=ipaddress.ip_address(args.host)
if address.is_unspecified:raise SystemExit('请指定本机或内网IP，不能监听所有网卡')
hosts=[args.host]
if args.loopback and args.host!='127.0.0.1':hosts.insert(0,'127.0.0.1')
sockets=[]
try:
 for host in hosts:
  sock=socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,socket.SOCK_STREAM)
  sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  try:sock.bind((host,args.port))
  except OSError as exc:
   sock.close()
   if args.loopback and host!='127.0.0.1' and exc.errno==errno.EADDRNOTAVAIL:
    print('指定内网IP已变化，本机地址仍可用：http://127.0.0.1:'+str(args.port),flush=True);continue
   raise
  sockets.append(sock)
 config=uvicorn.Config('backend.app.main:create_app',factory=True,host=args.host,port=args.port,proxy_headers=False,ssl_certfile=args.cert,ssl_keyfile=args.key)
 uvicorn.Server(config).run(sockets=sockets)
finally:
 for sock in sockets:sock.close()

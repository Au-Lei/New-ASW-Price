"""Read-only network hints. Does not alter firewall, router or listen settings."""
import ipaddress
import socket
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import load_settings
settings=load_settings()
addresses=set()
try:
 for address in socket.getaddrinfo(socket.gethostname(),None,family=socket.AF_INET):
  value=address[4][0]
  if ipaddress.ip_address(value).is_private and not ipaddress.ip_address(value).is_loopback:addresses.add(value)
except OSError:pass
print('候选内网地址：'+(', '.join(sorted(addresses)) or '未自动发现，请在系统网络设置中查看当前IPv4地址'))
print('局域网模式在.env设置 ASW_HOST=实际内网IP，并将同一IP加入ASW_ALLOWED_HOSTS后重启。')
print('同事使用 http(s)://内网IP:端口 登录，各自使用本人账号。不要共享data目录或数据库。')
print('HTTP不加密账号与报价传输；公司正式局域网使用建议配置受信任HTTPS证书。')

"""Run manually on the host: enter the API key without echo or shell history."""
import getpass
import json
import os
from pathlib import Path
import secrets

root=Path(__file__).resolve().parents[1]
key=getpass.getpass('请输入 DeepSeek API Key（不会回显）：').strip()
if not key or len(key)>4096 or any(ord(c)<32 for c in key):raise SystemExit('密钥为空或格式无效，未保存')
file=root/'.env.ai';temporary=root/('.env.ai-'+secrets.token_hex(8))
fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as output:output.write('DEEPSEEK_API_KEY='+json.dumps(key)+'\n')
os.replace(temporary,file)
print('已安全保存到项目 .env.ai；密钥未显示。请在聊天中只回复“已配置”，不要发送密钥。')

#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ] || [ ! -f frontend/dist/index.html ]; then
  echo '环境未准备好，请先运行 bash scripts/setup.sh'; exit 1
fi
# Read .env through dotenv, never source or evaluate its contents as shell code.
asw_start_config=$(.venv/bin/python - <<'PY'
from dotenv import load_dotenv
import os,ipaddress
load_dotenv('.env')
host=os.getenv('ASW_HOST','127.0.0.1');port=os.getenv('ASW_PORT','8000')
try:ipaddress.ip_address(host)
except ValueError:raise SystemExit('ASW_HOST须为监听IP地址')
if not port.isdigit() or not 1<=int(port)<=65535:raise SystemExit('ASW_PORT无效')
for value in (host,port,os.getenv('ASW_TLS_CERT',''),os.getenv('ASW_TLS_KEY','')):
 if '\n' in value or '\r' in value:raise SystemExit('启动参数包含无效换行')
 print(value)
PY
)
ASW_BIND_ADDRESS=$(printf '%s\n' "$asw_start_config" | sed -n '1p')
ASW_BIND_PORT=$(printf '%s\n' "$asw_start_config" | sed -n '2p')
ASW_CERT_PATH=$(printf '%s\n' "$asw_start_config" | sed -n '3p')
ASW_KEY_PATH=$(printf '%s\n' "$asw_start_config" | sed -n '4p')
ASW_URL_SCHEME=http
if [ -n "$ASW_CERT_PATH" ] || [ -n "$ASW_KEY_PATH" ]; then
  [ -f "$ASW_CERT_PATH" ] && [ -f "$ASW_KEY_PATH" ] || { echo 'HTTPS证书和私钥必须同时配置且存在'; exit 1; }
  export ASW_COOKIE_SECURE=true
  ASW_URL_SCHEME=https
fi
echo "监听 ${ASW_BIND_ADDRESS}:${ASW_BIND_PORT}。访问 ${ASW_URL_SCHEME}://${ASW_BIND_ADDRESS}:${ASW_BIND_PORT}，按 Ctrl+C 停止。"
echo '初始管理员临时凭据仅保存在主机 data/admin-initial.txt，首次修改后移除。'
ASW_LOOPBACK_ACCESS=$(.venv/bin/python -c "from dotenv import load_dotenv; import os; load_dotenv('.env'); print(os.getenv('ASW_LOOPBACK_ACCESS','false').lower())")
if [ "$ASW_LOOPBACK_ACCESS" = 'true' ]; then
  echo "本机固定地址：${ASW_URL_SCHEME}://127.0.0.1:${ASW_BIND_PORT}"
  if [ -n "$ASW_CERT_PATH" ]; then
    exec .venv/bin/python scripts/serve.py --host "$ASW_BIND_ADDRESS" --port "$ASW_BIND_PORT" --loopback --cert "$ASW_CERT_PATH" --key "$ASW_KEY_PATH"
  fi
  exec .venv/bin/python scripts/serve.py --host "$ASW_BIND_ADDRESS" --port "$ASW_BIND_PORT" --loopback
fi
if [ -n "$ASW_CERT_PATH" ]; then
  exec .venv/bin/python -m uvicorn backend.app.main:create_app --factory --host "$ASW_BIND_ADDRESS" --port "$ASW_BIND_PORT" --no-proxy-headers --ssl-certfile "$ASW_CERT_PATH" --ssl-keyfile "$ASW_KEY_PATH"
fi
exec .venv/bin/python -m uvicorn backend.app.main:create_app --factory --host "$ASW_BIND_ADDRESS" --port "$ASW_BIND_PORT" --no-proxy-headers

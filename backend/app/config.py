import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    log_dir: Path
    timezone: str
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_files: int = 10
    pdf_max_pages: int = 50
    session_hours: int = 8
    cookie_secure: bool = False
    allowed_hosts: tuple[str,...] = ('localhost','127.0.0.1','[::1]','testserver')

    ai_enabled: bool = False
    ai_model: str = 'deepseek-flash'
    ai_api_key: str = field(default='',repr=False)
    ai_key_file: Path = ROOT / '.env.ai'
    ai_timeout_seconds: int = 90
    ai_total_seconds: int = 600
    ai_max_calls: int = 20

    def __post_init__(self):
        if self.ai_model!='deepseek-flash':raise ValueError('当前仅接入已确认的deepseek-flash模型')
        if not 5<=self.ai_timeout_seconds<=180 or not 30<=self.ai_total_seconds<=900 or not 1<=self.ai_max_calls<=30:raise ValueError('AI超时或调用次数配置超限')
        if not 1 <= self.session_hours <= 24:raise ValueError('登录有效期须为1～24小时')
        if not 1 <= self.upload_max_bytes <= 100 * 1024 * 1024 or not 1 <= self.upload_max_files <= 100 or not 1 <= self.pdf_max_pages <= 500:
            raise ValueError('上传限制配置超出支持范围')


def load_settings() -> Settings:
    load_dotenv(ROOT / '.env', override=False)
    def directory(key: str, default: str) -> Path:
        path = Path(os.environ.get(key, default)).expanduser()
        return path if path.is_absolute() else ROOT / path
    timezone = os.environ.get('ASW_TIMEZONE', 'Asia/Shanghai')
    ZoneInfo(timezone)
    return Settings(directory('ASW_DATA_DIR', 'data'), directory('ASW_LOG_DIR', 'logs'), timezone, int(os.environ.get('ASW_UPLOAD_MAX_MB','20')) * 1024 * 1024, int(os.environ.get('ASW_UPLOAD_MAX_FILES','10')), int(os.environ.get('ASW_PDF_MAX_PAGES','50')), int(os.environ.get('ASW_SESSION_HOURS','8')), os.environ.get('ASW_COOKIE_SECURE','false').lower()=='true', tuple(x.strip() for x in os.environ.get('ASW_ALLOWED_HOSTS','localhost,127.0.0.1,[::1]').split(',') if x.strip()), ai_enabled=os.environ.get('ASW_AI_ENABLED','false').lower()=='true', ai_model=os.environ.get('ASW_AI_MODEL','deepseek-flash'), ai_api_key=os.environ.get('DEEPSEEK_API_KEY',''), ai_timeout_seconds=int(os.environ.get('ASW_AI_TIMEOUT_SECONDS','90')), ai_total_seconds=int(os.environ.get('ASW_AI_TOTAL_SECONDS','600')), ai_max_calls=int(os.environ.get('ASW_AI_MAX_CALLS','20')))

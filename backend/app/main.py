import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .config import ROOT, Settings, load_settings
from .database import connect, initialize
from .catalog_api import catalog_router
from .quotation import quotation_router
from .excel_api import excel_router
from .review import review_router
from .uploads import ExtractionWorker, uploads_router
from .ai_api import ai_router
from .backups import backup_router
from .auth import auth_router, authorize, principal_id, bootstrap
from starlette.middleware.trustedhost import TrustedHostMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logger = logging.getLogger('asw')
    logger.setLevel(logging.INFO)
    database = settings.data_dir / 'asw.sqlite3'
    worker = ExtractionWorker(database,settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(settings.log_dir / 'app.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        try:
            initialize(database)
            (settings.data_dir / 'uploads').mkdir(exist_ok=True)
            bootstrap(database,settings)
            worker.start()
            logger.info('startup completed')
            yield
        finally:
            worker.stop()
            logger.info('shutdown')
            logger.removeHandler(handler)
            handler.close()

    app = FastAPI(title='海运报价管理系统', version='1.0.0', lifespan=lifespan)
    app.include_router(catalog_router(database))
    app.include_router(quotation_router(database,settings))
    app.include_router(excel_router(database,settings))
    app.include_router(review_router(database))
    app.include_router(uploads_router(database,settings,worker))
    app.include_router(ai_router(database,settings))
    app.state.extraction_worker = worker
    app.state.database_path = database
    app.state.settings = settings
    app.include_router(auth_router(database,settings))
    app.include_router(backup_router(database,settings))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.allowed_hosts))

    @app.middleware('http')
    async def log_request(request: Request, call_next):
        request.state.request_id = uuid4().hex
        token = None
        try:
            uid = authorize(request,database)
            token = principal_id.set(uid)
            response = await call_next(request)
        except HTTPException as exc:
            response = error(request,exc.status_code,str(exc.detail))
        finally:
            if token is not None:principal_id.reset(token)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Request-ID'] = request.state.request_id
        logger.info('%s %s status=%s request_id=%s', request.method, request.url.path, response.status_code, request.state.request_id)
        return response

    def error(request: Request, status: int, message: str):
        return JSONResponse(status_code=status, content={'error': {'message': message, 'request_id': getattr(request.state, 'request_id', '')}})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error(request, exc.status_code, '接口或页面不存在' if exc.status_code == 404 else str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error(request, 422, '请求参数格式不正确')

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.exception('request failed request_id=%s', getattr(request.state, 'request_id', ''))
        return error(request, 500, '服务暂时无法处理请求，请查看日志或稍后重试')

    @app.get('/api/health')
    def health():
        with connect(database) as db:
            assert db.execute('SELECT 1').fetchone()[0] == 1
            version = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        return {'status': 'ok', 'database': 'ok', 'schema_version': version}

    @app.get('/api/system')
    def system():
        with connect(database) as db:
            installation = db.execute("SELECT value FROM application_meta WHERE key='installation_id'").fetchone()[0]
        return {'name': '青岛港国际海运报价管理系统', 'version': '1.0.0', 'stage': '第一版本地试用（AI仅虚构或脱敏样本）', 'timezone': settings.timezone, 'date': datetime.now(ZoneInfo(settings.timezone)).date().isoformat(), 'installation_id': installation}

    dist = ROOT / 'frontend' / 'dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='web')
    return app

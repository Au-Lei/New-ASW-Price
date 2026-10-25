"""Durable local uploads and extraction queue; only fixed Excel parsing is enabled."""
from io import BytesIO
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from uuid import uuid4
import warnings

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image
from pypdf import PdfReader

from .database import connect
from .auth import system_actor
from .ai_extract import AIError,run_ai,public_status
from .excel_api import ImportOptions, UnrecognizedTemplate, parse_rows
from .excel_format import read_xlsx, HEADERS, column
from .review import actor, audit, batch, dump, EDITABLE
from .pod_excel import parse_pod,can_parse as can_parse_pod,NotPOD

MIMES={'.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
Image.MAX_IMAGE_PIXELS=40_000_000

class InvalidFile(ValueError): pass

def fixed_template(sheets):
    return any([sheet['cells'].get(f'{column(c)}{r}','').strip() for c in range(1,19)]==HEADERS and not any(f'{column(c)}{r}' in sheet['formulas'] for c in range(1,19)) for sheet in sheets for r in range(1,21))

def validate_file(content,filename,settings):
    ext=Path(filename).suffix.lower()
    if ext=='.xls': raise InvalidFile('旧版xls请先另存为xlsx')
    if ext not in MIMES:raise InvalidFile('仅支持xlsx、pdf、jpg、jpeg、png文件')
    if not content:raise InvalidFile('文件为空，请重新选择')
    if len(content)>settings.upload_max_bytes:raise InvalidFile('文件大小超过允许上限')
    try:
        if ext=='.xlsx':
            if not content.startswith(b'PK\x03\x04'):raise InvalidFile('文件内容不是xlsx，可能已加密或扩展名不符；请另存为未加密xlsx')
            try:sheets=read_xlsx(content,settings.upload_max_bytes)
            except ValueError as exc:raise InvalidFile(str(exc)) from exc
            return {'kind':'xlsx','fixed_template':fixed_template(sheets),'local_parseable':fixed_template(sheets) or can_parse_pod(sheets),'worksheets':[s['name'] for s in sheets],'formula_cells':sum(len(s['formulas']) for s in sheets)}
        if ext=='.pdf':
            if not content.startswith(b'%PDF-'):raise InvalidFile('文件内容不是PDF，与扩展名不符')
            reader=PdfReader(BytesIO(content),strict=True)
            if reader.is_encrypted:raise InvalidFile('不支持加密PDF，请提供未加密原件')
            count=len(reader.pages)
            if not 1<=count<=settings.pdf_max_pages:raise InvalidFile(f'PDF页数须为1～{settings.pdf_max_pages}页')
            for page in reader.pages:
                if float(page.mediabox.width)<=0 or float(page.mediabox.height)<=0:raise InvalidFile('PDF页面尺寸无效')
            return {'kind':'pdf','pages':count}
        expected='PNG' if ext=='.png' else 'JPEG'
        if not (content.startswith(b'\x89PNG\r\n\x1a\n') if expected=='PNG' else content.startswith(b'\xff\xd8\xff')):raise InvalidFile('图片内容与扩展名不符')
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format!=expected:raise InvalidFile('图片类型与扩展名不符')
                if getattr(image,'n_frames',1)!=1:raise InvalidFile('暂不支持多帧图片，请拆分成单张图片')
                width,height=image.size
                if width*height>40_000_000:raise InvalidFile('图片超过4000万像素，请缩小后上传')
                image.verify()
            with Image.open(BytesIO(content)) as image:image.load()
        return {'kind':'image','width':width,'height':height,'format':expected}
    except InvalidFile:raise
    except Exception as exc:
        raise InvalidFile('文件损坏、类型不符或结构不支持，请重新导出原文件后上传') from exc

def file_path(settings,row):
    root=(settings.data_dir/'uploads').resolve();key=row['storage_key']
    if not re.fullmatch(r'[0-9a-f]{32}\.(xlsx|pdf|png|jpe?g)',key):raise HTTPException(422,'原件存储标识无效')
    path=root/key
    if path.is_symlink() or path.resolve().parent!=root or not path.is_file():raise HTTPException(404,'原件已缺失，请联系管理员恢复备份')
    return path

def authorized(db,bid,edit=False):
    who=actor(db);b=batch(db,bid,edit)
    # Task 9 will replace the server-bound local actor with authenticated sessions.
    if who['role']!='admin' and b['owner_id']!=who['id']:raise HTTPException(403,'只能访问本人的上传原件及提取任务')
    return who,b

def task_data(db,tid,include_result=True):
    t=db.execute('SELECT t.*,f.original_name,f.batch_id FROM extraction_tasks t JOIN uploaded_files f ON f.id=t.file_id WHERE t.id=?',(tid,)).fetchone()
    if not t:raise HTTPException(404,'提取任务不存在')
    authorized(db,t['batch_id'])
    value=dict(t);value.pop('run_token',None)
    value['result']=json.loads(value.pop('raw_result_json') or 'null')
    if not include_result:
        value['result']={k:value['result'][k] for k in ('kind','import_id','ai_import_id','counts','model') if k in value['result']} if value['result'] else None
        return value
    value['attempt_history']=[{**dict(r),'result':json.loads(r['result_json'] or 'null')} for r in db.execute('SELECT * FROM extraction_attempts WHERE task_id=? ORDER BY attempt DESC',(tid,))]
    for a in value['attempt_history']:a.pop('result_json',None)
    return value

class ExtractionWorker:
    def __init__(self,database,settings):
        self.database,self.settings=database,settings
        self.stop_event=threading.Event();self.wake=threading.Event();self.thread=None
    def recover(self):
        with connect(self.database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT * FROM extraction_tasks WHERE status='processing'").fetchall()
            for row in rows:
                db.execute("UPDATE ai_calls SET status='failed',error_code='AI_INTERRUPTED',finished_at=CURRENT_TIMESTAMP WHERE attempt_id IN (SELECT id FROM extraction_attempts WHERE task_id=?) AND status='processing'",(row['id'],))
                db.execute("UPDATE extraction_attempts SET status='failed',finished_at=CURRENT_TIMESTAMP,error_code='INTERRUPTED',error='服务中断，已重新排队' WHERE task_id=? AND status='processing'",(row['id'],))
                db.execute("UPDATE extraction_tasks SET status='waiting',run_token=NULL,error_code='INTERRUPTED',error='服务中断，等待恢复提取',updated_at=CURRENT_TIMESTAMP WHERE id=?",(row['id'],))
                audit(db,system_actor(db),'recover_extraction','extraction_task',row['id'])
    def start(self):
        self.recover();self.thread=threading.Thread(target=self.loop,name='asw-extraction',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=5)
    def loop(self):
        while not self.stop_event.is_set():
            try:
                if self.run_once():continue
            except Exception:
                logging.getLogger('asw').exception('extraction worker iteration failed')
            self.wake.wait(.5);self.wake.clear()
    def run_once(self):
        with connect(self.database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            task=db.execute("SELECT * FROM extraction_tasks WHERE status='waiting' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not task:return False
            token=uuid4().hex;attempt=task['attempts']+1
            db.execute("UPDATE extraction_tasks SET status='processing',attempts=?,run_token=?,error=NULL,error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(attempt,token,task['id']))
            db.execute("INSERT INTO extraction_attempts(id,task_id,attempt,status) VALUES (?,?,?,'processing')",(token,task['id'],attempt))
            f=dict(db.execute('SELECT * FROM uploaded_files WHERE id=?',(task['file_id'],)).fetchone())
            audit(db,system_actor(db),'start_extraction','extraction_task',task['id'],after={'attempt':attempt})
        result=None;error=None;code=None
        try:
            path=file_path(self.settings,f);content=path.read_bytes()
            if len(content)!=f['size_bytes'] or hashlib.sha256(content).hexdigest()!=f['sha256']:raise InvalidFile('原件校验失败，文件与上传记录不一致')
            meta=validate_file(content,f['original_name'],self.settings)
            if meta['kind']=='xlsx':
                try:
                    with connect(self.database) as db:parsed=parse_rows(db,content,ImportOptions(),self.settings.upload_max_bytes)
                except UnrecognizedTemplate:
                    try:
                        with connect(self.database) as db:result=parse_pod(db,content,self.settings.upload_max_bytes)
                    except NotPOD:result=run_ai(self.database,self.settings,f,meta,content,task['id'],token,lambda:self.stop_event.is_set() and threading.current_thread() is self.thread)
                else:result={'kind':'fixed_excel','filename':f['original_name'],**parsed}
            else:
                result=run_ai(self.database,self.settings,f,meta,content,task['id'],token,lambda:self.stop_event.is_set() and threading.current_thread() is self.thread)
        except AIError as exc:
            code,error=exc.code,exc.message
            logging.getLogger('asw').warning('AI extraction failed task=%s code=%s',task['id'],code)
        except Exception as exc:
            code='FILE_UNAVAILABLE' if isinstance(exc,HTTPException) else 'INVALID_FILE' if isinstance(exc,(InvalidFile,ValueError)) else 'EXTRACTION_FAILED'
            error=str(exc.detail) if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,(InvalidFile,ValueError)) else '提取发生错误，请重试；仍失败请查看日志'
            logging.getLogger('asw').exception('extraction failed task=%s',task['id'])
        try:
            with connect(self.database) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
                now=db.execute('SELECT * FROM extraction_tasks WHERE id=?',(task['id'],)).fetchone()
                if now['run_token']!=token or now['status']!='processing':return True
                if result:
                    b=batch(db,f['batch_id'])
                    if b['status'] not in EDITABLE:code='BATCH_LOCKED';error='批次已提交或审批，请先撤回再重试';result=None
                if result and result.get('kind') in ('ai','structured_excel'):
                    result['filename']=f['original_name']
                    iid=uuid4().hex
                    db.execute('INSERT INTO ai_imports(id,task_id,file_id,batch_id,preview_json) VALUES (?,?,?,?,?)',(iid,task['id'],f['id'],f['batch_id'],dump(result)))
                    result['ai_import_id']=iid
                if result and result.get('kind')=='fixed_excel':
                    imported=db.execute('SELECT id,preview_json FROM excel_imports WHERE sha256=?',(f['sha256'],)).fetchone()
                    iid=imported['id'] if imported else uuid4().hex
                    if not imported:
                        db.execute('INSERT INTO excel_imports(id,batch_id,file_id,sha256,options_json,preview_json) VALUES (?,?,?,?,?,?)',(iid,f['batch_id'],f['id'],f['sha256'],ImportOptions().model_dump_json(),dump(result)))
                    if imported:result={'kind':'fixed_excel',**json.loads(imported['preview_json'])}
                    result['import_id']=iid
                status='waiting' if code=='AI_INTERRUPTED' and self.stop_event.is_set() else 'failed' if error else 'success'
                db.execute('UPDATE extraction_tasks SET status=?,raw_result_json=?,error=?,error_code=?,run_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,dump(result) if result else None,error,code,task['id']))
                db.execute('UPDATE extraction_attempts SET status=?,result_json=?,error=?,error_code=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',('failed' if status=='waiting' else status,dump(result) if result else None,error,code,token))
                audit(db,system_actor(db),'finish_extraction','extraction_task',task['id'],after={'status':status,'attempt':attempt,'error_code':code})
        except Exception:
            logging.getLogger('asw').exception('extraction result transaction failed task=%s',task['id'])
            with connect(self.database) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
                now=db.execute('SELECT run_token FROM extraction_tasks WHERE id=?',(task['id'],)).fetchone()
                if now and now['run_token']==token:
                    db.execute("UPDATE extraction_tasks SET status='failed',error_code='PERSIST_FAILED',error='提取结果保存失败，请重试',raw_result_json=NULL,run_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(task['id'],))
                    db.execute("UPDATE extraction_attempts SET status='failed',error_code='PERSIST_FAILED',error='提取结果保存失败，请重试',finished_at=CURRENT_TIMESTAMP WHERE id=?",(token,))
        return True

def uploads_router(database,settings,worker):
    router=APIRouter(prefix='/api/uploads',tags=['原件与提取任务'])
    @router.get('/limits')
    def limits():return {'max_file_bytes':settings.upload_max_bytes,'max_batch_files':settings.upload_max_files,'max_pdf_pages':settings.pdf_max_pages,'max_image_pixels':40_000_000,'extensions':list(MIMES),'ai_configured':public_status(settings)['ready'],'ai':public_status(settings)}
    @router.post('/batches',status_code=201)
    def create_batch():
        with connect(database) as db:
            db.row_factory=sqlite3.Row;who=actor(db);bid=uuid4().hex
            db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(bid,who['id']));audit(db,who,'create_upload','upload_batch',bid)
            return {'id':bid}
    @router.get('/batches')
    def batches():
        with connect(database) as db:
            db.row_factory=sqlite3.Row;who=actor(db)
            rows=db.execute("SELECT b.*,count(f.id) file_count FROM upload_batches b JOIN uploaded_files f ON f.batch_id=b.id WHERE (?='admin' OR b.owner_id=?) GROUP BY b.id ORDER BY b.rowid DESC",(who['role'],who['id'])).fetchall()
            return {'items':[dict(b) for b in rows]}
    @router.get('/batches/{bid}')
    def detail(bid:str):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;_,b=authorized(db,bid)
            rows=[]
            for f in db.execute('SELECT * FROM uploaded_files WHERE batch_id=? ORDER BY created_at,rowid',(bid,)).fetchall():
                value=dict(f);value.pop('storage_key');value['ai_consent']=json.loads(value.pop('ai_consent_json') or 'null');value['metadata']=json.loads(value.pop('metadata_json'))
                if value['metadata']['kind']=='xlsx' and 'local_parseable' not in value['metadata']:
                    try:
                        parsed=read_xlsx(file_path(settings,f).read_bytes(),settings.upload_max_bytes)
                        value['metadata']['fixed_template']=fixed_template(parsed)
                        value['metadata']['local_parseable']=fixed_template(parsed) or can_parse_pod(parsed)
                    except (ValueError,HTTPException):pass
                t=db.execute('SELECT id FROM extraction_tasks WHERE file_id=?',(f['id'],)).fetchone()
                value['task']=task_data(db,t['id'],False) if t else None;rows.append(value)
            return {'batch':dict(b),'files':rows}
    def save(bid,filename,content):
        metadata=validate_file(content,filename,settings);digest=hashlib.sha256(content).hexdigest()
        saved=None
        try:
            with connect(database) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who,_=authorized(db,bid,True)
                old=db.execute('SELECT f.*,b.owner_id FROM uploaded_files f JOIN upload_batches b ON b.id=f.batch_id WHERE f.sha256=? ORDER BY f.rowid LIMIT 1',(digest,)).fetchone()
                if old and (who['role']=='admin' or old['owner_id']==who['id']):return {'id':old['id'],'batch_id':old['batch_id'],'duplicate':True,'original_name':old['original_name']}
                count=db.execute('SELECT count(*) FROM uploaded_files WHERE batch_id=?',(bid,)).fetchone()[0]
                if count>=settings.upload_max_files:raise HTTPException(422,f'每批最多{settings.upload_max_files}个文件')
                fid=uuid4().hex;storage=fid+Path(filename).suffix.lower();root=settings.data_dir/'uploads';root.mkdir(parents=True,exist_ok=True);saved=root/storage
                with saved.open('xb') as output:output.write(content)
                db.execute('INSERT INTO uploaded_files(id,batch_id,original_name,storage_key,sha256,media_type,size_bytes,metadata_json) VALUES (?,?,?,?,?,?,?,?)',(fid,bid,filename,storage,digest,MIMES[Path(filename).suffix.lower()],len(content),dump(metadata)))
                db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(bid,));audit(db,who,'upload_file','uploaded_file',fid,after={'filename':filename,'sha256':digest,'size_bytes':len(content)})
                response={'id':fid,'batch_id':bid,'duplicate':False,'metadata':metadata,'original_name':filename}
            return response
        except Exception:
            if saved:saved.unlink(missing_ok=True)
            raise
    @router.post('/batches/{bid}/files',status_code=201)
    async def upload(bid:str,request:Request,filename:str=Query(...,min_length=1,max_length=255)):
        filename=filename.replace('\\','/').split('/')[-1]
        if not filename or any(ord(c)<32 for c in filename):raise HTTPException(422,'文件名无效')
        with connect(database) as db:
            db.row_factory=sqlite3.Row;authorized(db,bid,True)
        content=bytearray()
        async for chunk in request.stream():
            if len(content)+len(chunk)>settings.upload_max_bytes:raise HTTPException(413,f'单文件不得超过{settings.upload_max_bytes//1024//1024}MB')
            content.extend(chunk)
        try:return await run_in_threadpool(save,bid,filename,bytes(content))
        except InvalidFile as exc:raise HTTPException(422,str(exc)) from exc
    @router.get('/files/{fid}/original')
    def original(fid:str):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;f=db.execute('SELECT * FROM uploaded_files WHERE id=?',(fid,)).fetchone()
            if not f:raise HTTPException(404,'原件不存在')
            who,_=authorized(db,f['batch_id']);path=file_path(settings,f)
            # Do not inline untrusted documents; browser preview uses image object URLs only.
            audit(db,who,'download_original','uploaded_file',fid)
            return FileResponse(path,media_type=f['media_type'],filename=f['original_name'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
    @router.post('/files/{fid}/reextract')
    def reextract(fid:str):
        # New immutable original/version and batch; never replace an edited preview.
        target=None
        try:
            with connect(database) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT * FROM uploaded_files WHERE id=?',(fid,)).fetchone()
                if not old:raise HTTPException(404,'原件不存在')
                who,_=authorized(db,old['batch_id'])
                content=file_path(settings,old).read_bytes()
                if len(content)!=old['size_bytes'] or hashlib.sha256(content).hexdigest()!=old['sha256']:raise HTTPException(422,'原件校验不符，未重新提取')
                metadata=validate_file(content,old['original_name'],settings)
                if metadata.get('fixed_template'):raise HTTPException(422,'固定模板可直接编辑当前结果；无需重新识别')
                bid,new_id=uuid4().hex,uuid4().hex;storage=new_id+Path(old['original_name']).suffix.lower();target=settings.data_dir/'uploads'/storage
                with target.open('xb') as out:out.write(content)
                metadata['reextract_of']=fid
                db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(bid,who['id']))
                db.execute('INSERT INTO uploaded_files(id,batch_id,original_name,storage_key,sha256,media_type,size_bytes,metadata_json) VALUES (?,?,?,?,?,?,?,?)',(new_id,bid,old['original_name'],storage,old['sha256'],old['media_type'],len(content),dump(metadata)))
                audit(db,who,'reextract_version','uploaded_file',new_id,after={'original_file_id':fid,'batch_id':bid})
            return {'id':new_id,'batch_id':bid,'original_file_id':fid}
        except Exception:
            if target:target.unlink(missing_ok=True)
            raise
    @router.post('/files/{fid}/extract')
    def extract(fid:str):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');f=db.execute('SELECT * FROM uploaded_files WHERE id=?',(fid,)).fetchone()
            if not f:raise HTTPException(404,'原件不存在')
            who,_=authorized(db,f['batch_id']);old=db.execute('SELECT * FROM extraction_tasks WHERE file_id=?',(fid,)).fetchone()
            if old and old['status'] in ('waiting','processing','success'):return {'id':old['id'],'status':old['status'],'reused':True}
            authorized(db,f['batch_id'],True)
            if old:
                tid=old['id'];db.execute("UPDATE extraction_tasks SET status='waiting',error=NULL,error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(tid,))
            else:
                tid=uuid4().hex;db.execute("INSERT INTO extraction_tasks(id,file_id,status) VALUES (?,?,'waiting')",(tid,fid))
            audit(db,who,'retry_extraction' if old else 'queue_extraction','extraction_task',tid)
        worker.wake.set()
        return {'id':tid,'status':'waiting','reused':False}
    @router.get('/tasks/{tid}')
    def task(tid:str):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;return task_data(db,tid)
    return router

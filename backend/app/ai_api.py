"""Sample-only consent, immutable AI previews and explicit draft creation."""
import json
import sqlite3
from datetime import datetime,timezone
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .database import connect
from .review import actor,audit,batch,dump
from .uploads import authorized
from .quotation import Draft,validation
from .ai_extract import public_status,consent_signature,ENDPOINT,MODEL

class Consent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    classification:Literal['synthetic','redacted','none']
    confirmed:bool
class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    row_ids:list[str]=Field(min_length=1,max_length=500)

def ai_router(database,settings):
    router=APIRouter(prefix='/api/ai',tags=['AI报价提取'])
    @router.get('/status')
    def status():return public_status(settings)
    @router.post('/files/{fid}/consent')
    def consent(fid:str,value:Consent):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');file=db.execute('SELECT * FROM uploaded_files WHERE id=?',(fid,)).fetchone()
            if not file:raise HTTPException(404,'原件不存在')
            who,_=authorized(db,file['batch_id'],True)
            if value.classification!='none' and not value.confirmed:raise HTTPException(422,'须确认文件为虚构或已脱敏样本及发送目的地')
            old=json.loads(file['ai_consent_json'] or 'null')
            info={**consent_signature(file),'classification':value.classification,'actor_id':who['id'],'confirmed_at':datetime.now(timezone.utc).isoformat()} if value.classification!='none' else None
            db.execute('UPDATE uploaded_files SET ai_consent_json=? WHERE id=?',(dump(info) if info else None,fid));audit(db,who,'confirm_ai_sample' if info else 'revoke_ai_sample','uploaded_file',fid,old,info)
        return {'ok':True,'consent':info}
    def detail(db,iid):
        db.row_factory=sqlite3.Row;item=db.execute('SELECT * FROM ai_imports WHERE id=?',(iid,)).fetchone()
        if not item:raise HTTPException(404,'AI提取结果不存在')
        authorized(db,item['batch_id'])
        return {**json.loads(item['preview_json']),'id':item['id'],'file_id':item['file_id'],'filename':db.execute('SELECT original_name FROM uploaded_files WHERE id=?',(item['file_id'],)).fetchone()[0],'batch_id':item['batch_id'],'task_id':item['task_id'],'status':item['status'],'selection':json.loads(item['selection_json'] or '[]')}
    @router.get('/imports/{iid}')
    def get_import(iid:str):
        with connect(database) as db:return detail(db,iid)
    @router.post('/imports/{iid}/commit')
    def commit(iid:str,value:Selection):
        if len(value.row_ids)!=len(set(value.row_ids)):raise HTTPException(422,'不能重复选择同一行')
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');item=detail(db,iid);who,_=authorized(db,item['batch_id'])
            if item['status']=='committed':
                if set(value.row_ids)!=set(item['selection']):raise HTTPException(409,'已生成草稿，不能变更原选择；请在草稿中编辑')
                return {'created':len(item['selection']),'already_committed':True}
            authorized(db,item['batch_id'],True)
            rows={r['id']:r for r in item['rows']}
            if any(r not in rows for r in value.row_ids):raise HTTPException(422,'所选AI报价行不存在')
            for rid in value.row_ids:
                row=rows[rid];data=Draft.model_validate(row['data']).model_dump();issues,_=validation(db,data)
                raw={'kind':'ai','original':row['raw'],'mapping_notes':row['mapping_notes'],'ai_import_id':iid,'model':item['model'],'prompt_version':item['prompt_version']}
                db.execute('INSERT INTO draft_rows(id,batch_id,task_id,raw_json,edited_json,validation_json,updated_by) VALUES (?,?,?,?,?,?,?)',(rid,item['batch_id'],item['task_id'],dump(raw),dump(data),dump(issues),who['id']))
                for source in row['sources']:
                    db.execute('INSERT INTO source_evidence(id,file_id,task_id,draft_row_id,worksheet,cell_range,page_number,image_region_json,manual_note) VALUES (?,?,?,?,?,?,?,?,?)',(uuid4().hex,item['file_id'],item['task_id'],rid,source['worksheet'],source['cell_range'],source['page'],dump(source['region']) if source['region'] else None,source['excerpt']))
            db.execute("UPDATE ai_imports SET status='committed',selection_json=?,committed_at=CURRENT_TIMESTAMP WHERE id=?",(dump(value.row_ids),iid))
            db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(item['batch_id'],));audit(db,who,'ai_create_drafts','ai_import',iid,after={'rows':value.row_ids})
        return {'created':len(value.row_ids),'already_committed':False}
    @router.get('/tasks/{tid}/calls')
    def calls(tid:str):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;file=db.execute('SELECT f.* FROM uploaded_files f JOIN extraction_tasks t ON t.file_id=f.id WHERE t.id=?',(tid,)).fetchone()
            if not file:raise HTTPException(404,'任务不存在')
            authorized(db,file['batch_id'])
            return {'items':[{**{k:r[k] for k in ('id','ordinal','status','error_code','model','prompt_version','created_at')},'location':json.loads(r['location_json']),'response':json.loads(r['response_json'] or 'null')} for r in db.execute('SELECT c.* FROM ai_calls c JOIN extraction_attempts a ON a.id=c.attempt_id WHERE a.task_id=? ORDER BY c.rowid',(tid,))]}
    return router

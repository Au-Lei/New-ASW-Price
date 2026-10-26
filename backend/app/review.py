"""Transactional quotation review. Local identity is server bound until task 9 login."""
import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from .database import connect
from .quotation import Draft, BOXES, validation, decorate, dictionaries

EDITABLE = ('draft','rejected','withdrawn')
IDENTITY = ('origin_port_id','destination_port_id','carrier_id','source_series_id','service_key','service_kind','path_text','fee_basis','currency','conditions')
FIELDS = tuple(k for k in Draft.model_fields if k not in ('prices','zero_confirmed','manual_note'))
def dump(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def fingerprint(value): return hashlib.sha256(dump(value).encode()).hexdigest()
def actor(db):
    from .auth import current_actor
    return current_actor(db)
def audit(db,who,action,kind,identity,before=None,after=None):
    db.execute('INSERT INTO audit_events(id,actor_id,actor_label,action,entity_type,entity_id,before_json,after_json) VALUES (?,?,?,?,?,?,?,?)',
               (uuid4().hex,who['id'],who['username'],action,kind,identity,dump(before) if before is not None else None,dump(after) if after is not None else None))
def principal_present():
    from .auth import principal_id
    return principal_id.get() is not None
def batch(db,identity,editable=False):
    row=db.execute('SELECT * FROM upload_batches WHERE id=?',(identity,)).fetchone()
    if not row: raise HTTPException(404,'批次不存在')
    if principal_present() and actor(db)['role']!='admin' and row['owner_id']!=actor(db)['id']: raise HTTPException(403,'只能修改本人批次')
    if editable and row['status'] not in EDITABLE: raise HTTPException(409,'批次已锁定，请先撤回或等待审批')
    return row

def current(db):
    result=[]
    for v in db.execute('SELECT v.* FROM quote_versions v JOIN quotes q ON q.id=v.quote_id WHERE v.is_current=1 AND q.active=1').fetchall():
        data={k:v[k] if v[k] is not None else '' for k in FIELDS}
        data['transit_days']=str(data['transit_days'] or '')
        for k in ('origin_confirmed','conditions_confirmed'): data[k]=bool(data[k])
        prices=db.execute('SELECT * FROM quote_prices WHERE version_id=?',(v['id'],)).fetchall()
        data.update(prices={p['container_type']:f"{p['amount_minor']/100:.2f}" for p in prices},zero_confirmed=[p['container_type'] for p in prices if p['zero_confirmed']],manual_note='历史报价依据')
        result.append({'quote_id':v['quote_id'],'version':v['version'],'version_id':v['id'],'data':data,'evidence':[dict(e) for e in db.execute('SELECT e.*,f.original_name FROM source_evidence e LEFT JOIN uploaded_files f ON f.id=e.file_id WHERE e.quote_version_id=?',(v['id'],))]})
    return result

def identity(data): return tuple(' '.join(str(data[k]).split()).casefold() for k in IDENTITY)

def identifiable(data):
    # Blank ports remain publishable, but must not silently replace another quote.
    return bool(data.get('origin_port_id') and data.get('destination_port_id'))
def overlap(a,b): return bool(a['valid_from'] and a['valid_to'] and b['valid_from'] and b['valid_to']) and a['valid_from']<=b['valid_to'] and b['valid_from']<=a['valid_to']
def comparable(data):
    result={k:v for k,v in data.items() if k not in ('manual_note','zero_confirmed')}
    result['prices']={k:f'{int(Decimal(v)*100)}' for k,v in data['prices'].items() if v not in ('',None)}
    return result

def preview(db,batch_id):
    b=batch(db,batch_id)
    published=current(db); lookup=dictionaries(db); items=[]; seen=[]
    rows=db.execute('SELECT * FROM draft_rows WHERE batch_id=? ORDER BY id',(batch_id,)).fetchall()
    for r in rows:
        if not r['included']: continue
        data=json.loads(r['edited_json']); actions=json.loads(r['field_actions_json']); problems=[]
        candidates=[v for v in published if identity(v['data'])==identity(data)]
        target=next((v for v in published if v['quote_id']==r['target_quote_id']),None) if r['target_quote_id'] else (candidates[0] if len(candidates)==1 and identifiable(data) else None)
        if not r['target_quote_id'] and len(candidates)>1 and identifiable(data):
            problems.append('存在多笔同身份历史报价，请明确选择修订目标，不能自动新增或覆盖')
        if r['target_quote_id'] and (not target or target['version']!=r['expected_version']): problems.append('目标报价已变更或停用，请重新选择目标版本')
        if target:
            old=target['data']
            for k in FIELDS:
                action=actions.get(k)
                if action=='keep' or (not action and data[k] in ('',None,'未说明')): data[k]=old[k]
                elif action=='clear': data[k]=''
            for box in BOXES:
                action=actions.get('prices.'+box)
                if action=='keep' or (not action and data['prices'].get(box) in ('',None)):
                    data['prices'][box]=old['prices'].get(box)
                    if box in old['zero_confirmed'] and box not in data['zero_confirmed']: data['zero_confirmed'].append(box)
                elif action=='clear': data['prices'].pop(box,None)
        issues,_=validation(db,data,allow_incomplete=True); problems += [i['message'] for i in issues]
        if not issues:
            collisions=[v for v in published if identity(v['data'])==identity(data) and overlap(v['data'],data) and (not target or v['quote_id']!=target['quote_id'])]
            if collisions: problems.append('有效期与已有同口径报价重叠，请明确修订目标或调整有效期')
        changes={k:{'before':target['data'].get(k),'after':data.get(k)} for k in data if target and k not in ('manual_note','zero_confirmed') and comparable(target['data']).get(k)!=comparable(data).get(k)} if not issues else {}
        kind='modified' if target and changes else 'unchanged' if target else 'new'
        duplicate_of=None
        if not problems:
            for prior in seen:
                if (target and prior['target'] and target['quote_id']==prior['target']['quote_id']) or (identity(prior['data'])==identity(data) and identifiable(data)):
                    if comparable(prior['data'])==comparable(data):
                        duplicate_of=prior['duplicate_of'] or prior['row_id'];kind='duplicate'
                    else:
                        problems.append('批次内存在相互矛盾的报价，请移回草稿或调整')
                        prior['problems'].append('批次内存在相互矛盾的报价，请移回草稿或调整')
        evidence=[dict(e) for e in db.execute('SELECT e.*,f.original_name FROM source_evidence e LEFT JOIN uploaded_files f ON f.id=e.file_id WHERE e.draft_row_id=?',(r['id'],))]
        item={'row_id':r['id'],'revision':r['revision'],'kind':kind,'data':data,'display':decorate(data,lookup),'target':target,'changes':changes,'problems':problems,'duplicate_of':duplicate_of,'evidence':evidence}
        items.append(item);seen.append(item)
    # Deduplication must retain every original source for the published row.
    by_id={item['row_id']:item for item in items}
    for item in items:
        if item['duplicate_of']:
            canonical=by_id[item['duplicate_of']]
            existing={e['id'] for e in canonical['evidence']}
            canonical['evidence'].extend(e for e in item['evidence'] if e['id'] not in existing)
    value={'batch_id':batch_id,'revision':b['revision'],'items':items}
    value['token']=fingerprint(value)
    value['can_submit']=bool(items) and not any(i['problems'] for i in items) and any(i['kind'] in ('new','modified') for i in items)
    return value

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class Resolution(Input):
    revision:int=Field(ge=1)
    included:bool=True
    target_quote_id:str|None=None
    expected_version:int|None=None
    field_actions:dict[str,str]=Field(default_factory=dict)
class Submit(Input): token:str
class Decision(Input):
    operation_key:str=Field(min_length=8,max_length=160)
    decision:str
    reason:str=Field('',max_length=2000)
class Reason(Input): reason:str=Field(min_length=1,max_length=2000)

class MoveRow(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    batch_id:str=Field(min_length=1,max_length=160)
    revision:int=Field(ge=1)

def review_router(database):
    router=APIRouter(prefix='/api',tags=['报价审批'])
    def connection():
        return connect(database)
    @router.get('/review/batches')
    def batches():
        with connection() as db:
            db.row_factory=sqlite3.Row
            return {'operator':{k:v for k,v in actor(db).items() if k!='password_hash'},'items':[dict(r) for r in db.execute("SELECT b.*, (SELECT count(*) FROM draft_rows d WHERE d.batch_id=b.id) row_count FROM upload_batches b WHERE EXISTS(SELECT 1 FROM draft_rows d WHERE d.batch_id=b.id) AND (?='admin' OR b.owner_id=?) ORDER BY b.rowid DESC",(actor(db)['role'],actor(db)['id']))]}
    @router.get('/review/batches/{bid}')
    def detail(bid:str):
        with connection() as db:
            db.row_factory=sqlite3.Row;b=batch(db,bid)
            submissions=[dict(s) for s in db.execute('SELECT s.*,a.decision,a.reason,a.actor_id FROM submissions s LEFT JOIN approval_records a ON a.submission_id=s.id WHERE s.batch_id=? ORDER BY s.rowid DESC',(bid,))]
            for s in submissions:s['snapshot']=json.loads(s.pop('snapshot_json'))
            rows=[dict(r) for r in db.execute('SELECT id,revision,included,target_quote_id,expected_version,field_actions_json FROM draft_rows WHERE batch_id=?',(bid,))]
            return {'batch':dict(b),'preview':submissions[0]['snapshot'] if b['status'] in ('pending','approved') and submissions else preview(db,bid),'submissions':submissions,'rows':rows,'targets':current(db)}
    @router.post('/review/rows/{rid}/move')
    def move_row(rid:str,value:MoveRow):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db)
            row=db.execute('SELECT * FROM draft_rows WHERE id=?',(rid,)).fetchone()
            if not row:raise HTTPException(404,'草稿不存在')
            batch(db,row['batch_id'],True);batch(db,value.batch_id,True)
            if row['revision']!=value.revision:raise HTTPException(409,'草稿已更新，请重新打开')
            if row['batch_id']==value.batch_id:return {'revision':row['revision']}
            db.execute('UPDATE draft_rows SET batch_id=?,revision=revision+1,updated_by=? WHERE id=?',(value.batch_id,who['id'],rid))
            db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id IN (?,?)',(row['batch_id'],value.batch_id))
            audit(db,who,'move_draft','draft_row',rid,{'batch_id':row['batch_id']},{'batch_id':value.batch_id})
            return {'revision':row['revision']+1}
    @router.get('/review/batches/{bid}/export')
    def export_changes(bid:str):
        from datetime import date
        from .excel_format import HEADERS,write_workbook
        with connection() as db:
            db.row_factory=sqlite3.Row;b=batch(db,bid)
            submitted=db.execute('SELECT snapshot_json FROM submissions WHERE batch_id=? ORDER BY rowid DESC LIMIT 1',(bid,)).fetchone()
            value=json.loads(submitted[0]) if submitted and b['status'] in ('pending','approved') else preview(db,bid)
            rows=[]
            def date_cell(text):
                try:return date.fromisoformat(text) if text else ''
                except ValueError:return text
            def price_cell(text):
                try:
                    value=Decimal(text)
                    return value if value.is_finite() else text
                except InvalidOperation:return text
            def cells(data,display):
                port=display.get('destination',{});country=port.get('country',{})
                return [display.get('route',{}).get('name',''),country.get('name_en',''),country.get('name_cn',''),port.get('name_en',''),port.get('name_cn',''),display.get('carrier',{}).get('name',''),*[price_cell(data['prices'][box]) if box in data['prices'] else None for box in BOXES],data['currency'],data['sailing'],int(data['transit_days']) if str(data['transit_days']).isdigit() else data['transit_days'],data['path_text'],display.get('supplier',{}).get('name','')+' / '+display.get('series',{}).get('name',''),data['remark'],date_cell(data['valid_from']),date_cell(data['valid_to']),data['fee_basis']]
            lookup=dictionaries(db)
            for item in value['items']:
                if item['kind'] not in ('new','modified'):continue
                data=item['data'];display=item['display']
                old=item.get('target');before=cells(old['data'],decorate(old['data'],lookup)) if old else ['']*len(HEADERS)
                rows.append([{'new':'新增','modified':'修改'}[item['kind']],*cells(data,display),*before,dump(item['changes'])])
            if not rows:raise HTTPException(422,'没有可导出的变更')
            content=write_workbook([{'name':'报价变更明细','metadata':['内部成本报价；黄色修改／绿色新增的对应明细','原报价与本次报价分别保留；本文件不会执行单元格文字中的公式'],'headers':['变更类型',*HEADERS,*['原'+h for h in HEADERS],'逐字段变化'],'rows':rows}])
        return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="quotation-changes.xlsx"','Cache-Control':'no-store'})
    @router.put('/review/rows/{rid}')
    def resolve(rid:str,value:Resolution):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db)
            r=db.execute('SELECT * FROM draft_rows WHERE id=?',(rid,)).fetchone()
            if not r:raise HTTPException(404,'草稿不存在')
            batch(db,r['batch_id'],True)
            if r['revision']!=value.revision:raise HTTPException(409,'草稿已变更，请刷新')
            if any(k not in (*FIELDS,*(f'prices.{b}' for b in BOXES)) or v not in ('keep','set','clear') for k,v in value.field_actions.items()):raise HTTPException(422,'字段处理方式无效')
            if bool(value.target_quote_id)!=(value.expected_version is not None):raise HTTPException(422,'修订目标必须指定版本')
            db.execute('UPDATE draft_rows SET included=?,target_quote_id=?,expected_version=?,field_actions_json=?,revision=revision+1,updated_by=? WHERE id=?',(int(value.included),value.target_quote_id,value.expected_version,dump(value.field_actions),who['id'],rid))
            db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(r['batch_id'],));audit(db,who,'resolve','draft_row',rid,dict(r),value.model_dump())
            return {'ok':True}
    @router.post('/review/batches/{bid}/submit')
    def submit(bid:str,value:Submit):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db);b=batch(db,bid,True);p=preview(db,bid)
            if p['token']!=value.token:raise HTTPException(409,'对比结果已过期，请重新对比')
            if not p['can_submit']:raise HTTPException(422,'存在冲突、缺项或没有可发布变更')
            # Excluded rows become a separate editable batch; pending scope is frozen.
            excluded=db.execute('SELECT id FROM draft_rows WHERE batch_id=? AND included=0',(bid,)).fetchall()
            if excluded:
                new=uuid4().hex;db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(new,who['id']))
                db.execute('UPDATE draft_rows SET batch_id=?,included=1 WHERE batch_id=? AND included=0',(new,bid))
                audit(db,who,'split_draft','upload_batch',bid,after={'draft_batch_id':new,'rows':[r['id'] for r in excluded]})
            sid=uuid4().hex
            db.execute('INSERT INTO submissions(id,batch_id,batch_revision,snapshot_json,submitted_by) VALUES (?,?,?,?,?)',(sid,bid,b['revision'],dump(p),who['id']))
            db.execute("UPDATE upload_batches SET status='pending',owner_id=coalesce(owner_id,?) WHERE id=?",(who['id'],bid));audit(db,who,'submit','submission',sid,after=p)
            return {'submission_id':sid}
    @router.post('/review/batches/{bid}/withdraw')
    def withdraw(bid:str):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db);b=batch(db,bid)
            if b['status']!='pending':raise HTTPException(409,'仅待审批批次可撤回')
            if who['role']!='admin' and b['owner_id']!=who['id']:raise HTTPException(403,'只能撤回本人批次')
            db.execute("UPDATE upload_batches SET status='withdrawn',revision=revision+1 WHERE id=?",(bid,));audit(db,who,'withdraw','upload_batch',bid)
            return {'ok':True}
    @router.post('/review/submissions/{sid}/decision')
    def decide(sid:str,value:Decision):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db)
            if who['role']!='admin':raise HTTPException(403,'仅管理员可审批')
            if value.decision not in ('approved','rejected') or (value.decision=='rejected' and not value.reason.strip()):raise HTTPException(422,'审批决定无效或缺少驳回理由')
            existing=db.execute('SELECT * FROM approval_records WHERE operation_key=?',(value.operation_key,)).fetchone()
            if existing:
                if existing['submission_id']!=sid or existing['decision']!=value.decision or existing['reason']!=value.reason:raise HTTPException(409,'操作编号已用于另一审批')
                return {'ok':True,'repeated':True}
            s=db.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()
            if not s:raise HTTPException(404,'提交不存在')
            b=batch(db,s['batch_id']);latest=db.execute('SELECT id FROM submissions WHERE batch_id=? ORDER BY rowid DESC LIMIT 1',(b['id'],)).fetchone()
            if b['status']!='pending' or latest['id']!=sid:raise HTTPException(409,'该提交已处理或撤回')
            snapshot=json.loads(s['snapshot_json'])
            if value.decision=='approved':
                if preview(db,b['id'])['token']!=snapshot['token']:raise HTTPException(409,'报价或基础资料已变更，请撤回、重新对比并提交')
                for item in snapshot['items']:
                    if item['kind'] not in ('new','modified'):continue
                    data=item['data'];target=item['target'];qid=target['quote_id'] if target else uuid4().hex;vid=uuid4().hex
                    if target:db.execute('UPDATE quote_versions SET is_current=0 WHERE id=?',(target['version_id'],))
                    else:db.execute('INSERT INTO quotes(id) VALUES (?)',(qid,))
                    record={k:(data[k] or None) if k in ('origin_port_id','destination_port_id','route_id','carrier_id','source_series_id') else data[k] for k in FIELDS};record['transit_days']=int(data['transit_days']) if data['transit_days'] else None
                    record.update(display_json=dump({k:item['display'][k] for k in ('origin','destination','carrier','route','series','country','region','supplier')}),id=vid,quote_id=qid,version=target['version']+1 if target else 1,previous_version_id=target['version_id'] if target else None,created_by=s['submitted_by'],approved_by=who['id'],submitted_at=s['submitted_at'],approved_at=db.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0])
                    db.execute('INSERT INTO quote_versions('+','.join(record)+') VALUES ('+','.join('?' for _ in record)+')',tuple(record.values()))
                    _,amounts=validation(db,data,allow_incomplete=True)
                    for box,amount in amounts.items():db.execute('INSERT INTO quote_prices VALUES (?,?,?,?)',(vid,box,amount,int(box in data['zero_confirmed'])))
                    for e in item['evidence']:
                        keys=('file_id','task_id','worksheet','cell_range','page_number','image_region_json','manual_note')
                        db.execute('INSERT INTO source_evidence(id,quote_version_id,'+','.join(keys)+') VALUES ('+','.join('?' for _ in range(9))+')',(uuid4().hex,vid,*(e[k] for k in keys)))
                    audit(db,who,'publish_version','quote',qid,target,{'version_id':vid,'data':data,'display':item['display']})
            db.execute('INSERT INTO approval_records(id,submission_id,operation_key,decision,reason,actor_id) VALUES (?,?,?,?,?,?)',(uuid4().hex,sid,value.operation_key,value.decision,value.reason,who['id']))
            db.execute('UPDATE upload_batches SET status=?,revision=revision+1 WHERE id=?',(value.decision,b['id']));audit(db,who,value.decision,'submission',sid,after=value.model_dump())
            return {'ok':True,'repeated':False}
    @router.post('/quotes/{qid}/revision')
    def revision(qid:str):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db)
            old=next((v for v in current(db) if v['quote_id']==qid),None)
            if not old:raise HTTPException(404,'有效报价不存在')
            bid,rid=uuid4().hex,uuid4().hex;data=old['data'];data['manual_note']='修订报价 '+qid+'，请补充本次依据'
            db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(bid,who['id']))
            db.execute('INSERT INTO draft_rows(id,batch_id,target_quote_id,expected_version,raw_json,edited_json) VALUES (?,?,?,?,?,?)',(rid,bid,qid,old['version'],dump({'kind':'manual','initial':data}),dump(data)))
            audit(db,who,'create_revision','draft_row',rid,after=old)
            return {'batch_id':bid,'row_id':rid}
    @router.get('/quotes/{qid}/history')
    def history(qid:str):
        with connection() as db:
            db.row_factory=sqlite3.Row
            versions=[dict(v) for v in db.execute('SELECT * FROM quote_versions WHERE quote_id=? ORDER BY version DESC',(qid,))]
            for v in versions:
                v['prices']=[dict(p) for p in db.execute('SELECT * FROM quote_prices WHERE version_id=?',(v['id'],))]
                v['evidence']=[dict(e) for e in db.execute('SELECT e.*,f.original_name FROM source_evidence e LEFT JOIN uploaded_files f ON f.id=e.file_id WHERE e.quote_version_id=?',(v['id'],))]
            return {'items':versions,'events':[dict(e) for e in db.execute("SELECT * FROM audit_events WHERE entity_type='quote' AND entity_id=? ORDER BY rowid DESC",(qid,))]}
    @router.post('/quotes/{qid}/disable')
    def disable(qid:str,value:Reason):
        with connection() as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=actor(db)
            if who['role']!='admin':raise HTTPException(403,'仅管理员可停用')
            if not value.reason.strip():raise HTTPException(422,'请填写停用原因')
            if not db.execute('SELECT 1 FROM quotes WHERE id=?',(qid,)).fetchone():raise HTTPException(404,'报价不存在')
            db.execute('UPDATE quotes SET active=0,disabled_reason=? WHERE id=?',(value.reason,qid));audit(db,who,'disable','quote',qid,after=value.model_dump())
            return {'ok':True}
    return router

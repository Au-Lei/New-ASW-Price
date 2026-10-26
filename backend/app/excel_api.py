import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .catalog_import import normalize,SEED
from .database import connect
from .excel_format import HEADERS, EXTRAS, MAX_FILE, column, column_number, read_xlsx, write_workbook
from .quotation import Draft, Search, search_records, validation
from .quote_enrichment import equal_40
from .port_groups import port_key,grouped,merge_ranges


class UnrecognizedTemplate(ValueError):
    pass


class ImportOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    origin_port_id: str=Field('',max_length=160)
    source_series_id: str=Field('',max_length=160)
    currency: str=Field('',max_length=10)
    validity_basis: Literal['','ETD','other','unknown']=''
    service_kind: Literal['','direct','transshipment','unknown']=''
    service_key: str=Field('',max_length=160)


class Commit(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    row_ids: list[str]=Field(min_length=1,max_length=10000)


def parse_rows(db,content,options,max_file=MAX_FILE):
    sheets=read_xlsx(content,max_file)
    db.row_factory=sqlite3.Row
    ports=[dict(row) for row in db.execute('SELECT p.*,c.name_en AS country_en,c.name_cn AS country_cn FROM ports p JOIN countries c ON c.id=p.country_id WHERE p.active=1 AND c.active=1')]
    aliases=[dict(row) for row in db.execute('SELECT port_id,name FROM port_aliases WHERE active=1 AND confirmed=1')]
    carriers=[dict(row) for row in db.execute('SELECT * FROM carriers WHERE active=1')]
    carrier_aliases=[dict(row) for row in db.execute('SELECT carrier_id,name FROM carrier_aliases WHERE active=1')]
    series=[dict(row) for row in db.execute('SELECT s.*,p.name AS supplier_name FROM source_series s JOIN suppliers p ON p.id=s.supplier_id WHERE s.active=1 AND p.active=1')]
    routes=[dict(row) for row in db.execute('SELECT r.*,g.name AS region_name FROM routes r JOIN regions g ON g.id=r.region_id WHERE r.active=1 AND g.active=1')]
    memberships={(r[0],r[1]) for r in db.execute('SELECT port_id,route_id FROM port_routes')}
    default=options.origin_port_id or db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
    rows,skipped,sheet_errors=[],0,[]
    def one(candidates):return candidates[0]['id'] if len(candidates)==1 else ''
    def port_matches(text,cn='',country_en='',country_cn=''):
        candidates=[]
        for p in ports:
            names=[p['name_en'],p['name_cn'],p['port_code'] or '']+[a['name'] for a in aliases if a['port_id']==p['id']]
            if text and normalize(text) not in {normalize(n) for n in names if n}:continue
            if cn and normalize(cn) not in {normalize(n) for n in names if n}:continue
            if country_en and normalize(country_en)!=normalize(p['country_en']):continue
            if country_cn and normalize(country_cn)!=normalize(p['country_cn']):continue
            candidates.append(p)
        return candidates if text or cn else []
    for sheet in sheets:
        cells=sheet['cells']
        header=next((r for r in range(1,21) if [cells.get(f'{column(c)}{r}','').strip() for c in range(1,19)]==HEADERS),None)
        if header is None or any(f'{column(c)}{header}' in sheet['formulas'] for c in range(1,19)):
            sheet_errors.append({'sheet':sheet['name'],'message':'未找到完整且顺序一致的18列表头，整表未导入'})
            continue
        last_col=max([len(HEADERS)]+[column_number(key.rstrip('0123456789')) for key in cells if int(key[len(key.rstrip('0123456789')):])==header])
        last_col=min(last_col,60)
        titles={column(c):cells.get(f'{column(c)}{header}','').strip() for c in range(1,last_col+1)}
        labels=[name for name in titles.values() if name]
        if len(labels)!=len(set(labels)):
            sheet_errors.append({'sheet':sheet['name'],'message':'存在重复表头，整表未导入'})
            continue
        # Only actual vertical merges are expanded; horizontal merges are presentation.
        expanded=dict(cells)
        expanded_formulas=set(sheet['formulas'])
        for c1,r1,c2,r2 in sheet['merges']:
            if c1==c2 and r1>header:
                for r in range(r1+1,r2+1):
                    expanded.setdefault(f'{c1}{r}',cells.get(f'{c1}{r1}',''))
                    if not expanded.get(f'{c1}{r}'):
                        expanded[f'{c1}{r}']=cells.get(f'{c1}{r1}','')
                    if f'{c1}{r1}' in sheet['formulas']:expanded_formulas.add(f'{c1}{r}')
        max_row=max((int(''.join(filter(str.isdigit,key))) for key in cells),default=header)
        country_en=country_cn=previous_line=''
        for rownum in range(header+1,max_row+1):
            original={name:cells.get(f'{col}{rownum}','') for col,name in titles.items() if name}
            raw={name:expanded.get(f'{col}{rownum}','') for col,name in titles.items() if name}
            # Country continuation applies only to contiguous destination rows within the same line.
            if not raw.get('英文港口名') and not raw.get('中文港口名'):
                country_en=country_cn=''
            if raw.get('航线','')!=previous_line:
                country_en=country_cn='';previous_line=raw.get('航线','')
            if raw.get('国家英文名') or raw.get('国家中文名'):
                country_en,country_cn=raw.get('国家英文名',''),raw.get('国家中文名','')
            effective={**raw,'国家英文名':country_en,'国家中文名':country_cn}
            formulas=[f'{col}{rownum}' for col,title in titles.items() if title and f'{col}{rownum}' in expanded_formulas]
            if not any(str(raw.get(box,'')).strip() for box in ('20GP','40GP','40HQ')) and not formulas:
                if raw.get('英文港口名') or raw.get('中文港口名'):skipped+=1
                continue
            data=Draft(origin_port_id=default).model_dump()
            data.update(prices={box:raw.get(box,'') for box in ('20GP','40GP','40HQ')},
                        currency=raw.get('货币','').strip() or options.currency,
                        valid_from=raw.get('有效期起','').strip(),valid_to=raw.get('有效期止','').strip(),
                        sailing=raw.get('船期',''),transit_days=raw.get('航程天数','').strip(),path_text=raw.get('途径路径',''),
                        remark=raw.get('备注（注意事项）',''),fee_basis=raw.get('费用口径',''),
                        service_key=raw.get('服务标识','').strip() or options.service_key,
                        manual_note=f'Excel原件：{sheet["name"]} 第{rownum}行；请对照原报价核对')
            kind=raw.get('服务类型','').strip()
            data['service_kind']={'直达':'direct','中转':'transshipment','待确认':'unknown','direct':'direct','transshipment':'transshipment'}.get(kind,options.service_kind if not kind else 'unknown')
            basis=raw.get('有效期依据','').strip()
            data['validity_basis']='ETD' if basis in ('ETD','ETD开船日') else options.validity_basis if not basis else 'unknown'
            data['validity_original']=basis
            for label,field in [('已含费用','included_fees'),('另计费用','extra_fees'),('待确认费用','unknown_fees'),('适用条件','conditions')]:
                if raw.get(label):data[field]=raw[label]
            data['prices'],price_notes=equal_40(data['prices'])
            mapping=list(price_notes)
            if raw.get('起运港','').strip():
                data['origin_port_id']=one(port_matches(raw['起运港'].strip()))
                if not data['origin_port_id']:mapping.append('起运港未唯一匹配，请人工选择')
            data['destination_port_id']=one(port_matches(raw.get('英文港口名','').strip(),raw.get('中文港口名','').strip(),country_en,country_cn))
            if not data['destination_port_id']:mapping.append('目的港／国家未唯一匹配，请人工选择，原文已保留')
            carrier_text=raw.get('船公司','').strip()
            data['carrier_id']=one([c for c in carriers if normalize(carrier_text) in {normalize(c['name']),*[normalize(a['name']) for a in carrier_aliases if a['carrier_id']==c['id']]}]) if carrier_text else ''
            if not data['carrier_id']:mapping.append('船公司未匹配，请先维护基础资料或人工选择')
            source=raw.get('价格来源','').strip()
            if raw.get('报价供应商') and raw.get('协议／报价系列'):
                source=raw['报价供应商']+' · '+raw['协议／报价系列']
            data['source_series_id']=one([s for s in series if normalize(source) in {normalize(s['name']),normalize(s['supplier_name']+' · '+s['name'])}]) if source else options.source_series_id
            if not data['source_series_id']:mapping.append('报价来源未唯一匹配，请选择供应商的协议／报价系列')
            route_candidates=[r for r in routes if normalize(r['name'])==normalize(raw.get('航线','')) and (data['destination_port_id'],r['id']) in memberships]
            if len(route_candidates)>1:route_candidates=[r for r in route_candidates if r['region_name']==sheet['name']]
            data['route_id']=one(route_candidates)
            fatal=[f'公式单元格不导入：{", ".join(formulas)}'] if formulas else []
            try:
                data=Draft.model_validate(data).model_dump()
                issues,_=validation(db,data)
            except ValidationError:
                fatal.append('字段类型或长度超出系统限制，请修正原文件后重传')
                issues=[]
            rows.append({'id':uuid4().hex,'sheet':sheet['name'],'row':rownum,'cell_range':f'A{rownum}:{column(last_col)}{rownum}','raw':original,'effective':effective,'data':data,
                         'issues':issues,'mapping_notes':mapping,'fatal_errors':fatal,'importable':not fatal})
            if len(rows)>10000:raise ValueError('单次导入最多10000条报价')
    if len(sheet_errors)==len(sheets):raise UnrecognizedTemplate('所有工作表均不符合18列报价模板，请检查表头及列顺序')
    return {'rows':rows,'skipped_empty_prices':skipped,'sheet_errors':sheet_errors,
            'counts':{'total':len(rows),'importable':sum(r['importable'] for r in rows),'failed':sum(not r['importable'] for r in rows),
                      'pending':sum(r['importable'] and bool(r['issues']) for r in rows)}}


def export_sheets(result,search,generated):
    groups={}
    quantities=bool(search.quantities)
    headers=HEADERS+EXTRAS+(['20GP箱量','40GP箱量','40HQ箱量','海运费小计','小计说明'] if quantities else [])
    first=result['items'][0]
    label=lambda item: ' / '.join(filter(None,(item.get('name_cn'),item.get('name_en'),item.get('name'))))
    origin='全部起运港' if result['origin_port_id']=='' else label(first['origin'])
    description=f'起运港：{origin}；目的港：{label(first["destination"]) if search.destination_port_id else "全部"}；船公司：{label(first["carrier"]) if search.carrier_id else "全部"}'
    for item in result['items']:
        name=item['region'].get('name') or '其他航线'
        if name not in groups:
            groups[name]={'name':name,'title':name+'内部海运成本报价表','headers':headers,
                          'metadata':[description,f'参考日期：{result["reference_date"]}；有效期范围：{"全部有效期" if search.date_scope=="all" else "参考日适用"}；含边界当日',
                                      f'生成时间：{generated}；共{result["total"]}条，按查询条件全量导出，不受分页影响',
                                      '仅供内部使用；保留成本来源；海运费小计不含另计／待确认费用，报价不代表舱位确认'],'rows':[], 'port_groups':[]}
        price=lambda box:Decimal(item['prices'][box]) if box in item['prices'] else None
        row=[item['route'].get('name',''),item['country'].get('name_en',''),item['country'].get('name_cn',''),item['destination'].get('name_en',''),item['destination'].get('name_cn',''),item['carrier'].get('name',''),
             price('20GP'),price('40GP'),price('40HQ'),item['currency'],item.get('sailing',''),int(item['transit_days']) if item.get('transit_days') else None,item.get('path_text',''),
             item['supplier'].get('name','')+' · '+item['series'].get('name',''),item.get('remark',''),date.fromisoformat(item['valid_from']) if item['valid_from'] else None,date.fromisoformat(item['valid_to']) if item['valid_to'] else None,item['fee_basis'],
             item['origin'].get('name_en',''),item['validity_basis'],item['included_fees'],item['extra_fees'],item['unknown_fees'],item.get('conditions',''),item['service_key'],
             '直达' if item['service_kind']=='direct' else '中转' if item['service_kind']=='transshipment' else '',item['supplier'].get('name',''),item['series'].get('name',''),item.get('quote_id',item['id']),item['version'],item['date_status']]
        if quantities:
            row += [search.quantities.get(box) for box in ('20GP','40GP','40HQ')]+[Decimal(item['subtotal']) if item['subtotal'] is not None else None,
                    '缺少所选箱型价格：'+','.join(item['missing_boxes']) if item['missing_boxes'] else item['subtotal_note']]
        groups[name]['rows'].append(row)
        groups[name]['port_groups'].append(port_key(item))
    for sheet in groups.values():
        sheet['rows'],sheet['port_groups']=grouped(sheet['rows'],sheet['port_groups'])
        sheet['merge_ranges']=merge_ranges(sheet['port_groups'],8)
    preferred=['美洲航线','欧洲航线','美加航线','亚洲航线','非洲航线','中东印巴航线']
    return sorted(groups.values(),key=lambda g:(preferred.index(g['name']) if g['name'] in preferred else 99,g['name']))


class ExportSearch(Search):
    # Existing integrations can retain their detailed format; the main UI chooses explicitly.
    export_layout: Literal['detailed','template','simple'] = 'detailed'


def export_content(sheets,result,search):
    if search.export_layout=='detailed':
        return write_workbook(sheets)
    if search.export_layout=='simple':
        rows=[row[:len(HEADERS)] for sheet in sheets for row in sheet['rows']]
        keys=[key for sheet in sheets for key in sheet['port_groups']]
        rows,keys=grouped(rows,keys)
        return write_workbook([{'name':'查询报价','title':'海运报价查询结果','headers':HEADERS,
                               'metadata':[f"共{result['total']}条报价；按查询条件导出",'内部报价；有效期及适用条件以原报价为准'], 'rows':rows,'merge_ranges':merge_ranges(keys,8)}])
    from .template_export import write_template
    return write_template(sheets)


def excel_router(database,settings):
    router=APIRouter(prefix='/api/excel',tags=['Excel导入导出'])
    @router.get('/template')
    def template():
        dataset=json.loads(SEED.read_text())
        groups={}
        for item in dataset['ports']:
            name=item['region']
            groups.setdefault(name,{'name':name,'headers':HEADERS+EXTRAS,'metadata':[
                '填写箱价的行才会作为报价预览；空价格目录行不会生成报价。',
                '船公司与价格来源须匹配基础资料；来源填写“供应商 · 协议／报价系列”。',
                '日期请使用Excel日期或YYYY-MM-DD文字；不支持公式、宏和外部链接。',
                '补充列用于明确服务、有效期依据和费用范围；导入后仍须校对、审批。'],'rows':[]})
            groups[name]['rows'].append([item['route'],item['country_en'],item['country_cn'],item['name_en'],item['name_cn']]+[None]*(len(HEADERS+EXTRAS)-5))
        return Response(write_workbook(list(groups.values())),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('海运报价导入模板.xlsx'),'Cache-Control':'no-store'})
    def response_data(db,identity):
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM excel_imports WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'导入批次不存在')
        from .uploads import authorized
        authorized(db,row['batch_id'])
        return {'id':row['id'],'file_id':row['file_id'],'batch_id':row['batch_id'],'status':row['status'],'created_at':row['created_at'],
                'options':json.loads(row['options_json']),'selection':json.loads(row['selection_json'] or '[]'),**json.loads(row['preview_json'])}

    @router.get('/imports')
    def imports():
        with connect(database) as db:
            db.row_factory=sqlite3.Row
            from .auth import current_actor
            who=current_actor(db)
            rows=db.execute("SELECT i.id,i.status,i.created_at,i.selection_json,f.original_name FROM excel_imports i JOIN uploaded_files f ON f.id=i.file_id JOIN upload_batches b ON b.id=i.batch_id WHERE (?='admin' OR b.owner_id=?) ORDER BY i.created_at DESC,i.rowid DESC",(who['role'],who['id'])).fetchall()
            return {'items':[{**dict(row),'committed_count':len(json.loads(row['selection_json'] or '[]'))} for row in rows]}

    @router.get('/imports/{identity}')
    def import_detail(identity:str):
        with connect(database) as db:return response_data(db,identity)

    def prepare(content,filename,options):
        digest=hashlib.sha256(content).hexdigest()
        with connect(database) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id FROM excel_imports WHERE sha256=?',(digest,)).fetchone()
            if old:return {**response_data(db,old['id']),'duplicate':True}
            parsed=parse_rows(db,content,options)
            identity,batch_id,file_id=uuid4().hex,uuid4().hex,uuid4().hex
            storage=file_id+('.xls' if content.startswith(b'\xd0\xcf\x11\xe0') else '.xlsx')
            uploads=settings.data_dir/'uploads';uploads.mkdir(parents=True,exist_ok=True)
            saved=uploads/storage
            try:
                with saved.open('xb') as file:file.write(content)
                from .auth import current_actor
                who=current_actor(db)
                db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(batch_id,who['id']))
                db.execute('INSERT INTO uploaded_files(id,batch_id,original_name,storage_key,sha256,media_type,size_bytes) VALUES (?,?,?,?,?,?,?)',
                           (file_id,batch_id,filename,storage,digest,'application/vnd.ms-excel' if storage.endswith('.xls') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',len(content)))
                db.execute('INSERT INTO excel_imports(id,batch_id,file_id,sha256,options_json,preview_json) VALUES (?,?,?,?,?,?)',
                           (identity,batch_id,file_id,digest,options.model_dump_json(),json.dumps({'filename':filename,**parsed},ensure_ascii=False)))
                result=response_data(db,identity)
                db.commit()
                return result
            except Exception:
                saved.unlink(missing_ok=True)
                raise

    @router.post('/imports/preview')
    async def preview(request:Request,filename:str=Query(...,min_length=1,max_length=255),options_json:str=Query('{}',max_length=3000)):
        filename=filename.replace('\\','/').split('/')[-1]
        if not filename.lower().endswith(('.xlsx','.xls','.xsl')):raise HTTPException(422,'仅支持xls或xlsx；xsl仅兼容误命名的Excel')
        try:options=ImportOptions.model_validate_json(options_json)
        except ValidationError:raise HTTPException(422,'导入补充条件格式不正确')
        content=bytearray()
        async for chunk in request.stream():
            if len(content)+len(chunk)>MAX_FILE:raise HTTPException(413,'Excel文件不能超过20MB')
            content.extend(chunk)
        try:return await run_in_threadpool(prepare,bytes(content),filename,options)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc

    @router.post('/imports/{identity}/commit')
    def commit(identity:str,selection:Commit):
        if len(selection.row_ids)!=len(set(selection.row_ids)):raise HTTPException(422,'不能重复选择同一行')
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM excel_imports WHERE id=?',(identity,)).fetchone()
            if not row:raise HTTPException(404,'导入批次不存在')
            from .uploads import authorized
            authorized(db,row['batch_id'])
            if row['status']=='committed':
                if set(json.loads(row['selection_json']))!=set(selection.row_ids):raise HTTPException(409,'批次已提交草稿，不能更改导入行；请在草稿中编辑')
                return {'id':identity,'created':len(selection.row_ids),'already_committed':True}
            from .uploads import authorized
            authorized(db,row['batch_id'],True)
            candidates={r['id']:r for r in json.loads(row['preview_json'])['rows']}
            selected=[candidates.get(key) for key in selection.row_ids]
            if any(not r or not r['importable'] for r in selected):raise HTTPException(422,'选中行不存在或含不可导入错误')
            extraction=db.execute("SELECT id FROM extraction_tasks WHERE file_id=? AND status='success'",(row['file_id'],)).fetchone()
            task_id=extraction['id'] if extraction else None
            for item in selected:
                data=Draft.model_validate(item['data']).model_dump()
                issues,_=validation(db,data)
                raw={'kind':'excel','original':item['raw'],'effective':item['effective'],'source_sheet':item['sheet'],'source_row':item['row'],'import_id':identity}
                db.execute('INSERT INTO draft_rows(id,batch_id,task_id,raw_json,edited_json,validation_json) VALUES (?,?,?,?,?,?)',
                           (item['id'],row['batch_id'],task_id,json.dumps(raw,ensure_ascii=False),json.dumps(data,ensure_ascii=False),json.dumps(issues,ensure_ascii=False)))
                db.execute('INSERT INTO source_evidence(id,file_id,task_id,draft_row_id,worksheet,cell_range) VALUES (?,?,?,?,?,?)',
                           (uuid4().hex,row['file_id'],task_id,item['id'],item['sheet'],item['cell_range']))
            db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(row['batch_id'],))
            db.execute("UPDATE excel_imports SET status='committed',selection_json=?,committed_at=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(selection.row_ids),identity))
            db.execute('INSERT INTO audit_events(id,actor_id,actor_label,action,entity_type,entity_id,after_json) VALUES (?,?,?,?,?,?,?)',
                       (uuid4().hex,authorized(db,row['batch_id'])[0]['id'],authorized(db,row['batch_id'])[0]['username'],'import_drafts','excel_import',identity,json.dumps({'rows':selection.row_ids})))
        return {'id':identity,'created':len(selection.row_ids),'already_committed':False}

    @router.post('/export')
    def export(search:ExportSearch):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            result=search_records(db,search,settings.timezone,paginate=False)
            if not result['items']:raise HTTPException(422,'没有可导出的正式报价，请先查询或调整筛选条件')
            generated=datetime.now(ZoneInfo(settings.timezone)).strftime('%Y-%m-%d %H:%M:%S')+' '+settings.timezone
            sheets=export_sheets(result,search,generated)
        content=export_content(sheets,result,search)
        filename='内部海运报价_'+result['reference_date']+'.xlsx'
        return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'X-Export-Count':str(result['total']),'Cache-Control':'no-store'})

    return router

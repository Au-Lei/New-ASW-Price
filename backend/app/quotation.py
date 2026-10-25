"""Manual drafts and shared, read-only quotation search. Publishing belongs to task 6."""
import json
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .database import connect

BOXES = ('20GP', '40GP', '40HQ')
CURRENCIES = ('USD','CNY','EUR','GBP','JPY','HKD','SGD','AUD','CAD','CHF','NZD','KRW','AED','INR')
MAX_AMOUNT = Decimal('9999999999.99')
PriceText = Annotated[str, StringConstraints(strict=True,max_length=32)]


def today_in_zone(timezone):
    return datetime.now(ZoneInfo(timezone)).date()


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    origin_port_id: str = Field('', max_length=160)
    destination_port_id: str = Field('', max_length=160)
    route_id: str = Field('', max_length=160)
    carrier_id: str = Field('', max_length=160)
    source_series_id: str = Field('', max_length=160)
    service_key: str = Field('', max_length=160)
    service_kind: Literal['', 'direct','transshipment','unknown'] = ''
    path_text: str = Field('', max_length=2000)
    fee_basis: str = Field('', max_length=160)
    currency: str = Field('', max_length=10)
    sailing: str = Field('', max_length=300)
    transit_days: str = Field('', max_length=20)
    remark: str = Field('', max_length=4000)
    valid_from: str = Field('', max_length=30)
    valid_to: str = Field('', max_length=30)
    validity_basis: Literal['','ETD','other','unknown'] = ''
    validity_original: str = Field('', max_length=1000)
    included_fees: str = Field('未说明', max_length=2000)
    extra_fees: str = Field('未说明', max_length=2000)
    unknown_fees: str = Field('未说明', max_length=2000)
    conditions: str = Field('', max_length=2000)
    conditions_confirmed: bool = False
    origin_confirmed: bool = False
    prices: dict[Literal['20GP','40GP','40HQ'], PriceText | None] = Field(default_factory=dict, max_length=3)
    zero_confirmed: list[Literal['20GP','40GP','40HQ']] = Field(default_factory=list, max_length=3)
    manual_note: str = Field('', max_length=2000)


def validation(db, data, allow_incomplete=False):
    issues = []
    def problem(field, message):
        issues.append({'field': field, 'message': message})
    def active(table, identity):
        return db.execute(f'SELECT 1 FROM {table} WHERE id=? AND active=1', (identity,)).fetchone()
    for field, table, label in [('origin_port_id','ports','起运港'), ('destination_port_id','ports','目的港'),
                                ('route_id','routes','航线'), ('carrier_id','carriers','船公司'), ('source_series_id','source_series','协议／报价系列')]:
        if not active(table, data[field]):
            problem(field, f'请选择启用的{label}')
    for field in ['origin_port_id','destination_port_id']:
        if data[field] and db.execute('SELECT 1 FROM ports p JOIN countries c ON c.id=p.country_id WHERE p.id=? AND c.active=0', (data[field],)).fetchone():
            problem(field, '港口所属国家／地区已停用')
    if db.execute('SELECT 1 FROM routes r JOIN regions g ON g.id=r.region_id WHERE r.id=? AND g.active=0', (data['route_id'],)).fetchone():
        problem('route_id', '航线所属区域已停用')
    if db.execute('SELECT 1 FROM source_series s JOIN suppliers p ON p.id=s.supplier_id WHERE s.id=? AND p.active=0', (data['source_series_id'],)).fetchone():
        problem('source_series_id','报价供应商已停用')
    if data['route_id'] and data['destination_port_id'] and not db.execute('SELECT 1 FROM port_routes WHERE port_id=? AND route_id=?', (data['destination_port_id'],data['route_id'])).fetchone():
        problem('route_id','航线不属于所选目的港，请先维护基础资料或重新选择')
    for field, label in [('service_key','服务标识'), ('fee_basis','费用口径'), ('manual_note','手工报价依据')]:
        if not data[field].strip():
            problem(field, f'请填写{label}')
    if not data['origin_confirmed']:
        problem('origin_confirmed','请核对并确认起运港')
    if data['service_kind'] not in ('direct','transshipment'):
        problem('service_kind','请确认直达或中转服务')
    if data['validity_basis'] != 'ETD':
        problem('validity_basis','首版仅支持按ETD适用的报价，其他依据请保留草稿')
    if data['conditions'].strip() and not data['conditions_confirmed']:
        problem('conditions_confirmed','请人工确认适用条件')
    if data['currency'] not in CURRENCIES:
        problem('currency','请选择支持的标准币种')
    parsed_dates = {}
    for field in ('valid_from','valid_to'):
        value = data[field]
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError()
            parsed_dates[field] = date.fromisoformat(value)
        except ValueError:
            problem(field, '请填写完整有效日期（YYYY-MM-DD）')
    if len(parsed_dates) == 2 and parsed_dates['valid_from'] > parsed_dates['valid_to']:
        problem('valid_to','有效期止不能早于有效期起')
    if data['transit_days'] and (not re.fullmatch(r'[1-9]\d{0,4}', data['transit_days'])):
        problem('transit_days','航程天数须为正整数，区间说明请放入备注')
    amounts = {}
    for box in BOXES:
        text = data['prices'].get(box)
        if text is None or text.strip() == '':
            continue
        text = text.strip()
        try:
            amount = Decimal(text)
            if not re.fullmatch(r'\d+(?:\.\d{1,2})?', text) or not amount.is_finite() or amount > MAX_AMOUNT:
                raise InvalidOperation()
            amounts[box] = int(amount * 100)
            if amount == 0 and box not in data['zero_confirmed']:
                problem('prices.' + box, f'{box}为0元，须人工确认零运费')
        except InvalidOperation:
            problem('prices.' + box, f'{box}价格须为非负数字，最多两位小数且不超过{MAX_AMOUNT}')
    if not any(value is not None and value.strip() for value in data['prices'].values()):
        problem('prices','至少填写一种箱型价格；空白不代表零元')
    if allow_incomplete:
        # Empty values are permitted; malformed supplied values still need correction.
        issues = [i for i in issues if (
            i['field'] not in ('origin_confirmed','conditions_confirmed')
            and not (i['field'] in ('service_kind','validity_basis') and data[i['field']] in ('','unknown'))
            and not (i['field'] == 'prices' and not amounts)
            and not (i['field'] in data and (data[i['field']] is None or isinstance(data[i['field']],str) and not data[i['field']].strip()))
        )]
    return issues, amounts


def dictionaries(db):
    db.row_factory = sqlite3.Row
    tables = ('ports','countries','routes','regions','carriers','source_series','suppliers')
    return {table: {row['id']: dict(row) for row in db.execute(f'SELECT * FROM {table}')} for table in tables}


def decorate(data, lookup):
    result = dict(data)
    for field, key, table in [('origin_port_id','origin','ports'), ('destination_port_id','destination','ports'), ('carrier_id','carrier','carriers'),
                               ('route_id','route','routes'), ('source_series_id','series','source_series')]:
        result[key] = lookup[table].get(data.get(field), {})
    country_id = result['destination'].get('country_id')
    result['country'] = lookup['countries'].get(country_id, {})
    for key in ('origin','destination'):
        result[key] = {**result[key], 'country':lookup['countries'].get(result[key].get('country_id'),{})}
    result['supplier'] = lookup['suppliers'].get(result['series'].get('supplier_id'), {})
    result['region'] = lookup['regions'].get(result['route'].get('region_id'), {})
    return result


def manual_rows(db):
    db.row_factory = sqlite3.Row
    from .auth import current_actor
    who=current_actor(db)
    return db.execute("SELECT d.*, b.status AS batch_status FROM draft_rows d JOIN upload_batches b ON b.id=d.batch_id WHERE json_extract(d.raw_json,'$.kind') IN ('manual','excel','ai') AND (?='admin' OR b.owner_id=?) ORDER BY d.rowid DESC",(who['role'],who['id'])).fetchall()


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    origin_port_id: str | None = None  # None = default Qingdao; empty = all origins.
    destination_port_id: str = ''
    carrier_id: str = ''
    destination_text: str = Field('', max_length=250)
    container_type: Literal['','20GP','40GP','40HQ'] = ''
    etd: str = ''
    date_scope: Literal['applicable','all'] = 'applicable'
    quantities: dict[Literal['20GP','40GP','40HQ'], int] = Field(default_factory=dict, max_length=3)
    sort_by: Literal['destination','carrier','valid_to','transit_days'] = 'destination'
    descending: bool = False
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


def search_records(db, search: Search, timezone: str, preview=False, paginate=True):
    reference = today_in_zone(timezone)
    if search.etd:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', search.etd):
                raise ValueError()
            reference = date.fromisoformat(search.etd)
        except ValueError:
            raise HTTPException(422,'ETD日期无效')
    for count in search.quantities.values():
        if isinstance(count, bool) or count < 1 or count > 100000:
            raise HTTPException(422,'箱量须为1～100000的整数；未选箱型请移除该项')
    lookup = dictionaries(db)
    origin = search.origin_port_id
    if origin is None:
        origin = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
    for value, table in [(origin,'ports'), (search.destination_port_id,'ports'), (search.carrier_id,'carriers')]:
        if value and value not in lookup[table]:
            raise HTTPException(422,'筛选资料不存在，请刷新后重新选择')
    records, excluded = [], 0
    if preview:
        for row in manual_rows(db):
            if not row['included'] or row['batch_status'] not in ('draft','rejected','withdrawn'):
                continue
            data = json.loads(row['edited_json'])
            issues, amounts = validation(db, data)
            if issues:
                excluded += 1
                continue
            records.append({**data, 'id': row['id'], 'version':row['revision'], 'amounts':amounts, 'preview':True})
    else:
        for row in db.execute('SELECT v.* FROM quote_versions v JOIN quotes q ON q.id=v.quote_id WHERE q.active=1 AND v.is_current=1'):
            item = dict(row)
            item['amounts'] = {p['container_type']:p['amount_minor'] for p in db.execute('SELECT * FROM quote_prices WHERE version_id=?', (item['id'],))}
            item['preview'] = False
            records.append(item)
    results = []
    for item in records:
        destination=lookup['ports'].get(item['destination_port_id'],{})
        text=search.destination_text.strip().casefold()
        if text and not any(text in str(destination.get(k,'')).casefold() for k in ('name_en','name_cn')):continue
        if search.container_type and search.container_type not in item['amounts']:continue
        if (origin and item['origin_port_id'] != origin) or (search.destination_port_id and item['destination_port_id'] != search.destination_port_id) or (search.carrier_id and item['carrier_id'] != search.carrier_id):
            continue
        first = date.fromisoformat(item['valid_from']) if item['valid_from'] else None
        last = date.fromisoformat(item['valid_to']) if item['valid_to'] else None
        if search.date_scope == 'applicable' and ((first and reference < first) or (last and reference > last)):
            continue
        status = '未生效' if first and reference < first else '已过期' if last and reference > last else '有效期未完整' if not first or not last else '即将到期' if (last - reference).days <= 10 else '有效'
        missing = [box for box in search.quantities if box not in item['amounts']]
        total = sum(item['amounts'][box] * count for box, count in search.quantities.items()) if search.quantities and not missing else None
        results.append({**decorate(item,lookup), **json.loads(item.get('display_json','{}')), 'prices':{box:f'{Decimal(amount)/100:.2f}' for box,amount in item['amounts'].items()},
                        'date_status':status, 'missing_boxes':missing, 'subtotal':f'{Decimal(total)/100:.2f}' if total is not None else None,
                        'subtotal_note':'海运费小计；费用范围以已含／另计／待确认说明为准，不代表全包总价',
                        'condition_note':'适用条件需确认' if item.get('conditions','').strip() else '',
                        'quantities':search.quantities})
    def sort_key(item):
        field = search.sort_by
        value = (item['destination'].get('name_en','') if field == 'destination' else item['carrier'].get('name','') if field == 'carrier' else item.get(field))
        if field == 'transit_days':
            return (value in (None,''), int(value) if value not in (None,'') else 0)
        return (value is None, value or '')
    results.sort(key=lambda row: row['id'])
    results.sort(key=sort_key, reverse=search.descending)
    total = len(results)
    status_counts = {state:sum(r['date_status']==state for r in results) for state in ('未生效','有效','即将到期','已过期')}
    start = (search.page - 1) * search.page_size
    applied = search.model_dump()
    applied['etd'] = reference.isoformat()
    return {'items':results[start:start + search.page_size] if paginate else results, 'total':total, 'page':search.page, 'page_size':search.page_size,
            'applied_filters':applied,
            'reference_date':reference.isoformat(), 'origin_port_id':origin, 'source':'draft_preview' if preview else 'published',
            'excluded_incomplete_drafts':excluded, 'status_counts':status_counts,
            'notice':'草稿试查：未审批，不可作为正式报价' if preview else '仅展示已审批、未停用的当前报价版本；报价不代表舱位确认'}


def quotation_router(database, settings):
    router = APIRouter(prefix='/api', tags=['人工报价与查询'])

    @router.get('/manual-drafts/defaults')
    def defaults():
        with connect(database) as db:
            origin = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
        return {'data':Draft(origin_port_id=origin).model_dump(), 'currencies':CURRENCIES}

    def draft_response(db, row):
        data = json.loads(row['edited_json'])
        issues, _ = validation(db,data)
        return {'id':row['id'],'batch_id':row['batch_id'],'revision':row['revision'],'batch_status':row['batch_status'],
                'data':data,'issues':issues,'ready_for_review':not issues,'updated_at':row['updated_at'],
                'origin_kind':json.loads(row['raw_json']).get('kind','manual'),
                'source_order':json.loads(row['raw_json']).get('source_order',json.loads(row['raw_json']).get('original',{}).get('source_order')), 
                'original_import':json.loads(row['raw_json']).get('original'),
                'enrichment':json.loads(row['raw_json']).get('original',{}).get('enrichment') if isinstance(json.loads(row['raw_json']).get('original'),dict) else None,
                'mapping_notes':json.loads(row['raw_json']).get('mapping_notes',[]),
                'evidence':[dict(r) for r in db.execute('SELECT e.file_id,e.worksheet,e.cell_range,e.page_number,e.image_region_json,e.manual_note,f.original_name FROM source_evidence e LEFT JOIN uploaded_files f ON f.id=e.file_id WHERE e.draft_row_id=?',(row['id'],))]}

    @router.get('/manual-drafts')
    def list_drafts():
        with connect(database) as db:
            rows = [draft_response(db,row) for row in manual_rows(db)]
        return {'items':rows,'total':len(rows)}

    def save(data, identity=None, expected_revision=None):
        values = data.model_dump()
        with connect(database) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            from .review import actor
            who = actor(db)
            if not identity and 'origin_port_id' not in data.model_fields_set:
                values['origin_port_id'] = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
            previous = next((r for r in manual_rows(db) if r['id']==identity), None) if identity else None
            if identity and previous is None:
                raise HTTPException(404,'人工草稿不存在')
            if previous and (previous['revision'] != expected_revision or previous['batch_status'] not in ('draft','rejected','withdrawn')):
                raise HTTPException(409,'草稿已被修改或锁定，请刷新后重新编辑')
            if previous and who['role'] != 'admin':
                owner = db.execute('SELECT owner_id FROM upload_batches WHERE id=?',(previous['batch_id'],)).fetchone()[0]
                if owner != who['id']: raise HTTPException(403,'只能编辑本人草稿')
            issues, _ = validation(db, values)
            serialized = json.dumps(values, ensure_ascii=False)
            if previous:
                batch_id = previous['batch_id']
                db.execute('UPDATE draft_rows SET edited_json=?,validation_json=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (serialized,json.dumps(issues,ensure_ascii=False),identity))
                db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?', (batch_id,))
            else:
                identity, batch_id = uuid4().hex, uuid4().hex
                db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(batch_id,who['id']))
                db.execute('INSERT INTO draft_rows(id,batch_id,raw_json,edited_json,validation_json) VALUES (?,?,?,?,?)',
                           (identity,batch_id,json.dumps({'kind':'manual','initial':values},ensure_ascii=False),serialized,json.dumps(issues,ensure_ascii=False)))
            evidence = db.execute('SELECT id FROM source_evidence WHERE draft_row_id=? AND file_id IS NULL',(identity,)).fetchone()
            if values['manual_note'].strip():
                if evidence:
                    db.execute('UPDATE source_evidence SET manual_note=? WHERE id=?',(values['manual_note'],evidence['id']))
                else:
                    db.execute('INSERT INTO source_evidence(id,draft_row_id,manual_note) VALUES (?,?,?)',(uuid4().hex,identity,values['manual_note']))
            elif evidence:
                db.execute('DELETE FROM source_evidence WHERE id=?',(evidence['id'],))
            db.execute('INSERT INTO audit_events(id,actor_id,actor_label,action,entity_type,entity_id,before_json,after_json) VALUES (?,?,?,?,?,?,?,?)',
                       (uuid4().hex,who['id'],who['username'],'update' if previous else 'create','manual_draft',identity,previous['edited_json'] if previous else None,serialized))
            row = next(r for r in manual_rows(db) if r['id']==identity)
            return draft_response(db,row)

    @router.post('/manual-drafts', status_code=201)
    def create_draft(data: Draft):
        return save(data)

    @router.put('/manual-drafts/{identity}')
    def update_draft(identity: str, data: Draft, expected_revision: int = Query(...,ge=1)):
        return save(data,identity,expected_revision)

    @router.post('/quotes/search')
    def published_search(search: Search):
        with connect(database) as db:
            db.row_factory = sqlite3.Row
            return search_records(db,search,settings.timezone)

    @router.post('/manual-drafts/search')
    def preview_search(search: Search):
        with connect(database) as db:
            db.row_factory = sqlite3.Row
            return search_records(db,search,settings.timezone,preview=True)

    return router

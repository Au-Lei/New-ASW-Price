import json
import sqlite3
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, Field

from .catalog_import import normalize
from .database import connect

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=250)]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    active: bool = True


class Named(Record):
    name: Name


class Country(Record):
    name_en: Name
    name_cn: Text = ''


class Route(Named):
    region_id: Name


class Port(Country):
    country_id: Name
    terminal: Text = ''
    port_code: Annotated[str, StringConstraints(pattern=r'^[A-Z]{2}[A-Z0-9]{3}$')] | None = None
    route_ids: list[Name] = Field(default_factory=list, max_length=50)


class Alias(Named):
    port_id: Name
    confirmed: bool = False


class CarrierAlias(Named):
    carrier_id: Name


class Supplier(Named):
    kind: Literal['carrier', 'agent', 'other']


class Series(Named):
    supplier_id: Name


MODELS = {'countries': Country, 'regions': Named, 'routes': Route, 'ports': Port,
          'port_aliases': Alias, 'carriers': Named, 'carrier_aliases': CarrierAlias,
          'suppliers': Supplier, 'source_series': Series}


def catalog_router(database):
    router = APIRouter(prefix='/api/catalog', tags=['基础资料'])

    def table_for(kind):
        if kind not in MODELS:
            raise HTTPException(404, '资料类型不存在')
        return kind

    def records(db, kind):
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute(f'SELECT * FROM {table_for(kind)} ORDER BY rowid')]
        for row in rows:
            row.pop('name_key', None)
            row['active'] = bool(row['active'])
            if kind == 'ports':
                row['route_ids'] = [r[0] for r in db.execute('SELECT route_id FROM port_routes WHERE port_id=? ORDER BY route_id', (row['id'],))]
                row['aliases'] = [dict(r) for r in db.execute('SELECT id,name,confirmed,active FROM port_aliases WHERE port_id=?', (row['id'],))]
            if kind == 'carriers':
                row['aliases'] = [dict(r) for r in db.execute('SELECT id,name,active FROM carrier_aliases WHERE carrier_id=?', (row['id'],))]
        return rows

    @router.get('/summary')
    def summary():
        with connect(database) as db:
            counts = {kind: db.execute(f'SELECT COUNT(*) FROM {kind}').fetchone()[0] for kind in MODELS}
            imports = [dict(zip(('source_name', 'source_sha256', 'rows_count', 'imported_at'), row)) for row in db.execute('SELECT source_name,source_sha256,rows_count,imported_at FROM catalog_imports')]
            default = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
            quotes = db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]
        return {'counts': counts, 'imports': imports, 'default_origin_port_id': default, 'quotes': quotes}

    @router.get('/{kind}')
    def list_records(kind: str, q: str = Query('', max_length=160), include_inactive: bool = True):
        table_for(kind)
        with connect(database) as db:
            rows = records(db, kind)
        term = normalize(q)
        if not include_inactive:
            rows = [row for row in rows if row['active']]
        if term:
            def matches(row):
                texts = [row.get(key, '') or '' for key in ('name', 'name_en', 'name_cn', 'port_code', 'terminal')]
                texts += [alias['name'] for alias in row.get('aliases', []) if alias['active'] and alias.get('confirmed', True)]
                return any(term in normalize(text) for text in texts)
            rows = [row for row in rows if matches(row)]
        return {'items': rows, 'total': len(rows), 'requires_selection': len(rows) > 1 and bool(term)}

    def save(kind, payload, record_id=None, expected_revision=None):
        table_for(kind)
        try:
            data = MODELS[kind].model_validate(payload).model_dump()
        except ValidationError as exc:
            fields = '、'.join(dict.fromkeys(str(e['loc'][0]) for e in exc.errors()))
            raise HTTPException(422, f'字段缺失或格式不正确：{fields}') from exc
        routes = data.pop('route_ids', None)
        if routes is not None and len(set(routes)) != len(routes):
            raise HTTPException(422, '航线不能重复选择')
        if kind not in ('regions', 'routes'):
            data['name_key'] = normalize(data.get('name_en', data.get('name', '')))
        if kind == 'ports':
            data['terminal_key'] = normalize(data['terminal'])
        try:
            with connect(database) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN IMMEDIATE')
                from .auth import current_actor
                if current_actor(db)['role']!='admin':raise HTTPException(403,'仅管理员可维护基础资料')
                before = dict(db.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone() or {}) if record_id else None
                if record_id and not before:
                    raise HTTPException(404, '资料不存在')
                if record_id and before['revision'] != expected_revision:
                    raise HTTPException(409, '资料已被修改，请刷新后重新编辑')
                if kind == 'ports' and not data['active']:
                    default = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
                    if record_id == default:
                        raise HTTPException(422, '不能停用默认起运港青岛')
                references = {'country_id': 'countries', 'region_id': 'regions', 'port_id': 'ports', 'carrier_id': 'carriers', 'supplier_id': 'suppliers'}
                for key, table in references.items():
                    if key in data:
                        ref = db.execute(f'SELECT active FROM {table} WHERE id=?', (data[key],)).fetchone()
                        # Unchanged inactive references can be retained while disabling/editing history.
                        if not ref or (not ref['active'] and (not before or before[key] != data[key])):
                            raise HTTPException(422, '关联资料不存在或已停用')
                for route_id in routes or []:
                    if not db.execute('SELECT 1 FROM routes WHERE id=? AND active=1', (route_id,)).fetchone():
                        existing = record_id and db.execute('SELECT 1 FROM port_routes WHERE port_id=? AND route_id=?', (record_id, route_id)).fetchone()
                        if not existing:
                            raise HTTPException(422, '所选航线不存在或已停用')
                if record_id:
                    db.execute(f'UPDATE {kind} SET ' + ','.join(f'{key}=?' for key in data) + ',revision=revision+1 WHERE id=?', (*data.values(), record_id))
                else:
                    record_id = uuid4().hex
                    db.execute(f'INSERT INTO {kind}(id,' + ','.join(data) + ') VALUES (' + ','.join('?' for _ in range(len(data) + 1)) + ')', (record_id, *data.values()))
                if routes is not None:
                    existing_routes = {row[0] for row in db.execute('SELECT route_id FROM port_routes WHERE port_id=?', (record_id,))}
                    for route_id in existing_routes - set(routes):
                        db.execute('DELETE FROM port_routes WHERE port_id=? AND route_id=?', (record_id, route_id))
                    for route_id in set(routes) - existing_routes:
                        db.execute('INSERT INTO port_routes VALUES (?,?)', (record_id, route_id))
                after = dict(db.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone())
                if routes is not None:
                    if before is not None:
                        before['route_ids'] = sorted(existing_routes)
                    after['route_ids'] = sorted(routes)
                from .auth import current_actor
                who=current_actor(db)
                db.execute('INSERT INTO audit_events(id,actor_id,actor_label,action,entity_type,entity_id,before_json,after_json) VALUES (?,?,?,?,?,?,?,?)',
                           (uuid4().hex,who['id'],who['username'], 'update' if before else 'create', kind, record_id,
                            json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False)))
                after.pop('name_key', None)
                return after
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '资料重复或关联正在使用，无法保存；请检查名称、国家、码头及引用关系') from exc

    @router.post('/{kind}', status_code=201)
    def create_record(kind: str, payload: dict = Body(...)):
        return save(kind, payload)

    @router.put('/{kind}/{record_id}')
    def update_record(kind: str, record_id: str, payload: dict = Body(...), expected_revision: int = Query(..., ge=1)):
        return save(kind, payload, record_id, expected_revision)

    @router.get('/ports/{port_id}/evidence')
    def evidence(port_id: str):
        with connect(database) as db:
            if not db.execute('SELECT 1 FROM ports WHERE id=?', (port_id,)).fetchone():
                raise HTTPException(404, '港口不存在')
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('SELECT i.source_name,i.source_sha256,e.worksheet,e.row_number,e.original_json FROM catalog_evidence e JOIN catalog_imports i ON i.id=e.import_id WHERE e.port_id=?', (port_id,))]
        return {'items': rows}

    return router

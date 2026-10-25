from backend.tests.auth_helpers import login_admin
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from backend.app.catalog_import import SEED, import_directory, parse_template
from backend.app.config import Settings
from backend.app.database import connect, initialize
from backend.app.main import create_app


@pytest.fixture
def environment(tmp_path):
    settings = Settings(tmp_path / 'data', tmp_path / 'logs', 'Asia/Shanghai')
    with TestClient(create_app(settings)) as client:
        login_admin(client)
        yield client, settings.data_dir / 'asw.sqlite3'


def create(client, resource, **data):
    response = client.post(f'/api/catalog/{resource}', json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_import_counts_inheritance_and_no_quotes(environment):
    client, path = environment
    summary = client.get('/api/catalog/summary').json()
    assert summary['quotes'] == 0
    assert summary['counts']['regions'] == 6
    assert summary['counts']['routes'] == 34
    assert summary['counts']['ports'] == 296  # 295 template locations plus Qingdao origin.
    assert summary['imports'][0]['rows_count'] == 295
    countries = {r['id']:r for r in client.get('/api/catalog/countries').json()['items']}
    for name, country in [('BREMERHAVEN','GERMANY'), ('LONG BEACH,CA','UNITED STATES'), ('KARACHI','PAKISTAN'), ('SIBU','MALAYSIA')]:
        rows = client.get('/api/catalog/ports', params={'q':name}).json()['items']
        assert len(rows) == 1
        assert countries[rows[0]['country_id']]['name_en'] == country
        assert rows[0]['route_ids']
        assert rows[0]['port_code'] is None
    victoria = client.get('/api/catalog/ports', params={'q':'维多利亚'}).json()
    assert victoria['total'] == 2 and victoria['requires_selection']
    assert len({r['country_id'] for r in victoria['items']}) == 2
    kelang = client.get('/api/catalog/ports', params={'q':'PORT KELANG'}).json()['items']
    assert len(kelang) == 2 and kelang[0]['id'] != kelang[1]['id']
    with connect(path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        for table in ['quotes','quote_versions','quote_prices','draft_rows','uploaded_files','approval_records']:
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        before = db.execute('SELECT COUNT(*) FROM ports').fetchone()[0]
        assert import_directory(db, json.loads(SEED.read_text()))['status'] == 'already_imported'
        assert db.execute('SELECT COUNT(*) FROM ports').fetchone()[0] == before


def test_upgrade_v1_keeps_existing_data_and_stable_ids(tmp_path):
    path = tmp_path / 'db.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        db.execute('INSERT INTO schema_migrations(version) VALUES (1)')
        db.execute('CREATE TABLE application_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO application_meta VALUES ('installation_id','existing-installation')")
    initialize(path)
    with connect(path) as db:
        ids = db.execute('SELECT id FROM ports ORDER BY id').fetchall()
        assert db.execute("SELECT value FROM application_meta WHERE key='installation_id'").fetchone()[0] == 'existing-installation'
    initialize(path)
    with connect(path) as db:
        assert db.execute('SELECT id FROM ports ORDER BY id').fetchall() == ids
        assert db.execute('SELECT COUNT(*) FROM catalog_imports').fetchone()[0] == 1


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    import backend.app.database as database
    path = tmp_path / 'db.sqlite'
    initialize(path)
    current_version=database.MIGRATIONS[-1][0]
    monkeypatch.setattr(database, 'MIGRATIONS', [*database.MIGRATIONS, (current_version+1, 'CREATE TABLE should_rollback(id INTEGER); INVALID SQL')])
    with pytest.raises(sqlite3.OperationalError):
        initialize(path)
    with connect(path) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='should_rollback'").fetchone()
        assert db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == current_version


def test_confirmed_alias_search_and_ambiguous_candidates(environment):
    client, _ = environment
    ports = client.get('/api/catalog/ports', params={'q':'维多利亚'}).json()['items']
    a = create(client, 'port_aliases', port_id=ports[0]['id'], name='  Shared alias  ', confirmed=False)
    assert client.get('/api/catalog/ports?q=shared%20alias').json()['total'] == 0
    response = client.put(f"/api/catalog/port_aliases/{a['id']}?expected_revision=1", json={'port_id':ports[0]['id'],'name':'Shared alias','confirmed':True})
    assert response.status_code == 200
    create(client, 'port_aliases', port_id=ports[1]['id'], name='SHARED ALIAS', confirmed=True)
    response = client.get('/api/catalog/ports?q=shared%20alias').json()
    assert response['requires_selection'] and response['total'] == 2
    assert client.get('/api/catalog/ports?q=鹿特丹').json()['items'][0]['id'] == client.get('/api/catalog/ports?q=rotterdam').json()['items'][0]['id']


def test_carrier_supplier_series_independent_and_duplicates(environment):
    client, _ = environment
    carrier = create(client, 'carriers', name='CMA CGM')
    create(client, 'carrier_aliases', carrier_id=carrier['id'], name='达飞')
    assert client.get('/api/catalog/carriers?q=达飞').json()['items'][0]['id'] == carrier['id']
    assert client.post('/api/catalog/carriers', json={'name':'  cma   cgm  '}).status_code == 409
    supplier = create(client, 'suppliers', name='测试代理', kind='agent')
    series = create(client, 'source_series', supplier_id=supplier['id'], name='欧洲协议A')
    assert series['supplier_id'] == supplier['id']
    assert client.post('/api/catalog/source_series', json={'supplier_id':supplier['id'],'name':'欧洲协议A'}).status_code == 409
    assert client.post('/api/catalog/source_series', json={'supplier_id':carrier['id'],'name':'无效关联'}).status_code == 422


def test_maintenance_optimistic_lock_disable_and_audit(environment):
    client, path = environment
    carrier = create(client, 'carriers', name='测试船公司')
    url = f"/api/catalog/carriers/{carrier['id']}?expected_revision=1"
    assert client.put(url, json={'name':'新标准名称','active':False}).status_code == 200
    assert client.put(url, json={'name':'过期修改'}).status_code == 409
    assert client.get('/api/catalog/carriers?include_inactive=false').json()['total'] == 0
    assert client.get('/api/catalog/carriers').json()['items'][0]['id'] == carrier['id']
    with connect(path) as db:
        event = db.execute("SELECT before_json,after_json FROM audit_events WHERE action='update'").fetchone()
        assert json.loads(event[0])['name'] == '测试船公司'
        assert json.loads(event[1])['name'] == '新标准名称'
    default = client.get('/api/catalog/summary').json()['default_origin_port_id']
    port = next(p for p in client.get('/api/catalog/ports').json()['items'] if p['id'] == default)
    payload = {k:port[k] for k in ('country_id','name_en','name_cn','terminal','route_ids')}
    assert client.put(f'/api/catalog/ports/{default}?expected_revision=1', json={**payload,'active':False}).status_code == 422


def test_country_port_routes_and_evidence(environment):
    client, _ = environment
    region = create(client, 'regions', name='测试区域')
    route = create(client, 'routes', region_id=region['id'], name='测试航线')
    country = create(client, 'countries', name_en='TEST COUNTRY', name_cn='测试国家')
    port = create(client, 'ports', country_id=country['id'], name_en='SAME PORT', name_cn='测试港', terminal='Terminal A', route_ids=[route['id']])
    assert client.post('/api/catalog/ports', json={'country_id':country['id'],'name_en':'same port','terminal':'terminal a'}).status_code == 409
    other = create(client, 'ports', country_id=country['id'], name_en='SAME PORT', terminal='Terminal B')
    assert other['id'] != port['id']
    assert client.get('/api/catalog/ports?q=SAME%20PORT').json()['total'] == 2
    assert client.post('/api/catalog/ports', json={'country_id':country['id'],'name_en':'Bad','route_ids':['missing']}).status_code == 422
    assert client.post('/api/catalog/ports', json={'country_id':country['id'],'name_en':'Bad','port_code':'XXXXX1'}).status_code == 422
    existing = client.get('/api/catalog/ports?q=鹿特丹').json()['items'][0]
    evidence = client.get(f"/api/catalog/ports/{existing['id']}/evidence").json()['items'][0]
    assert evidence['worksheet'] == '欧洲航线' and evidence['row_number'] == 3


@pytest.mark.parametrize('kind,payload', [('carriers',{'name':'  '}), ('suppliers',{'name':'X','kind':'invalid'}), ('ports',{'name_en':'Missing country'}), ('port_aliases',{'name':'X','port_id':'missing','confirmed':True})])
def test_invalid_data_is_rejected(environment, kind, payload):
    client, _ = environment
    assert client.post('/api/catalog/' + kind, json=payload).status_code == 422


def test_import_preserves_manual_edits_on_restart(environment):
    client, path = environment
    port = client.get('/api/catalog/ports?q=鹿特丹').json()['items'][0]
    payload = {k:port[k] for k in ('country_id','name_en','name_cn','terminal','route_ids')}
    payload['name_cn'] = '鹿特丹（人工核对）'
    assert client.put(f"/api/catalog/ports/{port['id']}?expected_revision=1", json=payload).status_code == 200
    initialize(path)
    assert client.get('/api/catalog/ports?q=鹿特丹').json()['items'][0]['name_cn'] == '鹿特丹（人工核对）'


def test_parser_group_boundaries_and_merge_cells(tmp_path):
    # Minimal OOXML fixture catches cross-group country leakage independently of the real template.
    def cell(ref, text):
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
    rows = '<row r="2">' + ''.join(cell(f'{c}2', t) for c,t in zip('ABCDE',['航线','国家英文名','国家中文名','英文港口名','中文港口名'])) + '</row>'
    rows += '<row r="3">' + cell('A3','航线A') + cell('B3','COUNTRY A') + cell('C3','国A') + cell('D3','PORT A') + '</row>'
    rows += '<row r="4">' + cell('D4','PORT B') + '</row><row r="5"></row>'
    rows += '<row r="6">' + cell('A6','航线B') + cell('D6','PORT C') + '</row>'
    path = tmp_path / 'fixture.xlsx'
    def write(body):
        with ZipFile(path,'w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试区域" r:id="a"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="a" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + body + '</sheetData><mergeCells><mergeCell ref="A3:A4"/></mergeCells></worksheet>')
    write(rows)
    with pytest.raises(ValueError, match='禁止跨分组猜测'):
        parse_template(path)
    write(rows[:rows.index('<row r="6">')])
    data = parse_template(path)
    assert len(data['ports']) == 2
    assert data['ports'][1]['route'] == '航线A' and data['ports'][1]['country_en'] == 'COUNTRY A'


def test_quote_schema_relations_dates_prices_and_draft_separation(environment):
    client, path = environment
    port = client.get('/api/catalog/ports?q=鹿特丹').json()['items'][0]
    origin = client.get('/api/catalog/summary').json()['default_origin_port_id']
    carrier = create(client, 'carriers', name='隔离测试船公司')
    supplier = create(client, 'suppliers', name='隔离测试供应商', kind='agent')
    series = create(client, 'source_series', supplier_id=supplier['id'], name='隔离测试系列')
    with connect(path) as db:
        db.execute("INSERT INTO users VALUES ('test-user','test-user','not-a-real-password-hash','admin',1,0)")
        db.execute("INSERT INTO quotes(id) VALUES ('quote-test')")
        base = dict(id='v1',quote_id='quote-test',version=1,origin_port_id=origin,destination_port_id=port['id'],route_id=port['route_ids'][0],
                    carrier_id=carrier['id'],source_series_id=series['id'],service_key='direct-test',service_kind='direct',fee_basis='CY-CY',currency='USD',
                    valid_from='2026-11-01',valid_to='2026-11-30',validity_basis='ETD',conditions_confirmed=1,origin_confirmed=1,
                    created_by='test-user',approved_by='test-user',submitted_at='2026-10-25T10:00:00+08:00',approved_at='2026-10-25T11:00:00+08:00')
        def insert_version(**changes):
            values = {**base, **changes}
            db.execute('INSERT INTO quote_versions(' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values()))
        for changes in [dict(valid_to='2026-10-01'), dict(valid_from='2026-02-30'), dict(carrier_id='missing'), dict(validity_basis='UNKNOWN'), dict(currency='usd')]:
            with pytest.raises(sqlite3.IntegrityError):
                insert_version(**changes)
        insert_version()
        with pytest.raises(sqlite3.IntegrityError):
            insert_version(id='v2',version=2)  # Cannot have two current revisions.
        for amount, zero, box in [(-1,0,'20GP'), (0,0,'20GP'), (10.25,0,'20GP'), (100,0,'45HQ')]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute('INSERT INTO quote_prices VALUES (?,?,?,?)', ('v1',box,amount,zero))
        db.execute("INSERT INTO quote_prices VALUES ('v1','20GP',100025,0)")
        db.execute("INSERT INTO quote_prices VALUES ('v1','40HQ',0,1)")
        assert db.execute('SELECT amount_minor FROM quote_prices WHERE container_type=\'20GP\'').fetchone()[0] == 100025
        db.execute("INSERT INTO upload_batches(id,owner_id) VALUES ('batch','test-user')")
        db.execute("INSERT INTO draft_rows(id,batch_id,raw_json,edited_json) VALUES ('incomplete','batch','{}','{}')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO draft_rows(id,batch_id,raw_json,edited_json) VALUES ('bad','batch','not json','{}')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quotes SET active=0 WHERE id='quote-test'")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []

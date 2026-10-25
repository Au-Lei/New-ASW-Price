"""Read the supplied fixed-layout port directory; never execute workbook content."""
import hashlib
import json
import posixpath
import re
import unicodedata
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5, uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
SEED = Path(__file__).parents[1] / 'resources' / 'port-directory.json'


def normalize(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).split()).upper()


def stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, 'asw:' + kind + ':' + json.dumps(parts, ensure_ascii=False)))


def parse_template(path: Path) -> dict:
    content = path.read_bytes()
    with ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si', NS)]
        rels = {rel.attrib['Id']: rel.attrib['Target'] for rel in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        workbook = ET.fromstring(z.read('xl/workbook.xml'))
        records, skipped, sheet_counts = [], [], {}
        for sheet in workbook.findall('x:sheets/x:sheet', NS):
            name = sheet.attrib['name']
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            if not member.startswith('xl/worksheets/'):
                raise ValueError('模板工作表路径不正确')
            root = ET.fromstring(z.read(member))
            cells = {}
            for cell in root.findall('x:sheetData/x:row/x:c', NS):
                if cell.find('x:f', NS) is not None:
                    raise ValueError(f'{name}!{cell.attrib["r"]} 含公式，请提供纯基础资料模板')
                value = cell.find('x:v', NS)
                text = value.text or '' if value is not None else ''
                if cell.attrib.get('t') == 's':
                    text = shared[int(text)]
                elif cell.attrib.get('t') == 'inlineStr':
                    text = ''.join(cell.find('x:is', NS).itertext())
                cells[cell.attrib['r']] = text.strip()
            if [cells.get(c + '2') for c in 'ABCDE'] != ['航线', '国家英文名', '国家中文名', '英文港口名', '中文港口名']:
                raise ValueError(f'{name} 表头不符合目录模板')
            # Expand only declared vertical A/B/C merges, not blank rows arbitrarily.
            for merge in root.findall('x:mergeCells/x:mergeCell', NS):
                match = re.fullmatch(r'([ABC])(\d+):\1(\d+)', merge.attrib['ref'])
                if match:
                    col, first, last = match.groups()
                    for row in range(int(first) + 1, int(last) + 1):
                        cells.setdefault(f'{col}{row}', '')
                        if not cells[f'{col}{row}']:
                            cells[f'{col}{row}'] = cells.get(f'{col}{first}', '')
            country_en = country_cn = last_line = ''
            sheet_counts[name] = 0
            max_row = max(int(re.search(r'\d+', key)[0]) for key in cells)
            for row in range(3, max_row + 1):
                values = {c: cells.get(f'{c}{row}', '') for c in 'ABCDE'}
                if not values['D']:
                    country_en = country_cn = ''
                    if values['E']:
                        skipped.append({'sheet': name, 'row': row, 'reason': '缺英文港口名'})
                    continue
                line = values['A']
                if line != last_line:
                    country_en = country_cn = ''
                    last_line = line
                if values['B']:
                    country_en, country_cn = values['B'], values['C']
                if not line or not country_en:
                    raise ValueError(f'{name}!D{row} 缺航线或国家，禁止跨分组猜测')
                records.append({'region': name, 'route': line, 'country_en': country_en, 'country_cn': country_cn,
                                'name_en': values['D'], 'name_cn': values['E'], 'terminal': '', 'sheet': name, 'row': row})
                sheet_counts[name] += 1
    return {'format_version': 1, 'source_name': path.name, 'source_sha256': hashlib.sha256(content).hexdigest(),
            'sheet_counts': sheet_counts, 'skipped': skipped, 'ports': records}


def import_directory(db, dataset: dict) -> dict:
    fingerprint = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = db.execute('SELECT rows_count FROM catalog_imports WHERE fingerprint=?', (fingerprint,)).fetchone()
    if existing:
        return {'status': 'already_imported', 'rows': existing[0]}
    import_id = uuid4().hex
    db.execute('INSERT INTO catalog_imports(id,fingerprint,source_name,source_sha256,rows_count) VALUES (?,?,?,?,?)',
               (import_id, fingerprint, dataset['source_name'], dataset['source_sha256'], len(dataset['ports'])))
    for record in dataset['ports']:
        country_id = stable_id('country', normalize(record['country_en']))
        region_id = stable_id('region', record['region'])
        route_id = stable_id('route', region_id, record['route'])
        port_id = stable_id('port', country_id, normalize(record['name_en']), normalize(record.get('terminal', '')))
        db.execute('INSERT OR IGNORE INTO countries(id,name_en,name_cn,name_key) VALUES (?,?,?,?)', (country_id, record['country_en'], record['country_cn'], normalize(record['country_en'])))
        db.execute('INSERT OR IGNORE INTO regions(id,name) VALUES (?,?)', (region_id, record['region']))
        db.execute('INSERT OR IGNORE INTO routes(id,region_id,name) VALUES (?,?,?)', (route_id, region_id, record['route']))
        db.execute('INSERT OR IGNORE INTO ports(id,country_id,name_en,name_cn,name_key,terminal,terminal_key) VALUES (?,?,?,?,?,?,?)',
                   (port_id, country_id, record['name_en'], record['name_cn'], normalize(record['name_en']), record.get('terminal', ''), normalize(record.get('terminal', ''))))
        db.execute('INSERT OR IGNORE INTO port_routes VALUES (?,?)', (port_id, route_id))
        db.execute('INSERT INTO catalog_evidence VALUES (?,?,?,?,?,?)',
                   (import_id, port_id, route_id, record['sheet'], record['row'], json.dumps(record, ensure_ascii=False)))
    return {'status': 'imported', 'rows': len(dataset['ports']), 'sheet_counts': dataset['sheet_counts']}


def seed_catalog(db) -> None:
    dataset = json.loads(SEED.read_text())
    import_directory(db, dataset)
    # Explicit origin default, without inventing any internationally assigned code.
    china = stable_id('country', 'CHINA')
    origin = stable_id('port', china, 'QINGDAO', '')
    db.execute('INSERT OR IGNORE INTO countries(id,name_en,name_cn,name_key) VALUES (?,?,?,?)', (china, 'CHINA', '中国', 'CHINA'))
    db.execute('INSERT OR IGNORE INTO ports(id,country_id,name_en,name_cn,name_key) VALUES (?,?,?,?,?)', (origin, china, 'QINGDAO', '青岛', 'QINGDAO'))
    db.execute("INSERT OR IGNORE INTO application_meta VALUES ('default_origin_port_id', ?)", (origin,))
    # Qingdao/青岛 names are canonical. Additional aliases are added explicitly in the UI.

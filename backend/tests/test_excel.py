from backend.tests.auth_helpers import login_admin
import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as E

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import connect,initialize
from backend.app.excel_format import column, HEADERS,EXTRAS,MAIN,NS,read_xlsx,write_workbook
from backend.app.main import create_app
from backend.tests.quote_fixtures import sample_data,insert_published


@pytest.fixture
def env(tmp_path,monkeypatch):
    import backend.app.quotation as quotation
    monkeypatch.setattr(quotation,'today_in_zone',lambda timezone:date(2026,11,15))
    settings=Settings(tmp_path/'data',tmp_path/'logs','Asia/Shanghai')
    with TestClient(create_app(settings)) as client:
        login_admin(client)
        path=settings.data_dir/'asw.sqlite3'
        yield client,path,sample_data(path),settings


def sheet(name='欧洲航线',rows=None,headers=None):
    row=['欧洲基本港','NETHERLANDS','荷兰','ROTTERDAM','鹿特丹','测试船公司a',Decimal('1000.25'),None,Decimal('1900'),'USD','周三',32,'青岛-鹿特丹','测试代理a · 测试协议a','=literal text',date(2026,11,1),date(2026,11,30),'CY-CY']
    return {'name':name,'metadata':['报价来源测试','参考日期测试','内部成本','日期与价格使用原值'],'headers':headers or HEADERS,'rows':rows if rows is not None else [row]}


def preview(client,content,options=None,filename='测试报价.xlsx'):
    return client.post('/api/excel/imports/preview',params={'filename':filename,'options_json':json.dumps(options or {},ensure_ascii=False)},content=content,headers={'Content-Type':'application/octet-stream'})


def mutate(content,member,change):
    target=BytesIO()
    with ZipFile(BytesIO(content)) as source,ZipFile(target,'w',ZIP_DEFLATED) as out:
        for name in source.namelist():
            data=source.read(name)
            if name==member:
                root=E.fromstring(data);change(root);data=E.tostring(root,encoding='utf-8',xml_declaration=True)
            out.writestr(name,data)
    return target.getvalue()


def test_template_download_skips_empty_directory(env):
    client,path,_,_=env
    response=client.get('/api/excel/template')
    assert response.status_code==200
    assert len(read_xlsx(response.content))==6
    result=preview(client,response.content).json()
    assert result['counts']['total']==0 and result['skipped_empty_prices']==295
    assert result['rows']==[]
    assert client.post(f"/api/excel/imports/{result['id']}/commit",json={'row_ids':[]}).status_code==422
    with connect(path) as db:assert db.execute('SELECT COUNT(*) FROM draft_rows').fetchone()[0]==0


def test_import_preview_commit_edit_provenance_idempotency(env):
    client,path,_,settings=env
    content=write_workbook([sheet()])
    result=preview(client,content,{'validity_basis':'ETD','service_kind':'direct','service_key':'经人工明确的服务'}).json()
    assert result['counts']=={'total':1,'importable':1,'failed':0,'pending':1}
    row=result['rows'][0]
    assert row['data']['prices']['20GP']=='1000.25'
    assert row['data']['valid_from']=='2026-11-01' and row['data']['valid_to']=='2026-11-30'
    assert row['data']['carrier_id']=='carrier-a' and row['data']['source_series_id']=='series-a'
    assert row['data']['remark']=='=literal text'
    assert [i['field'] for i in row['issues']]==['origin_confirmed']
    selected={'row_ids':[row['id']]}
    committed=client.post(f"/api/excel/imports/{result['id']}/commit",json=selected)
    assert committed.status_code==200 and committed.json()['created']==1
    assert client.post(f"/api/excel/imports/{result['id']}/commit",json=selected).json()['already_committed']
    duplicate=preview(client,content,{'service_key':'不应覆盖原预览'}).json()
    assert duplicate['duplicate'] and duplicate['id']==result['id'] and duplicate['status']=='committed'
    assert duplicate['options']['service_key']=='经人工明确的服务'
    saved=client.get('/api/manual-drafts').json()['items'][0]
    assert saved['origin_kind']=='excel' and saved['original_import']['英文港口名']=='ROTTERDAM'
    assert saved['evidence'][0]['original_name']=='测试报价.xlsx' and saved['evidence'][0]['cell_range']=='A8:R8'
    updated={**saved['data'],'origin_confirmed':True}
    assert client.put(f"/api/manual-drafts/{saved['id']}?expected_revision=1",json=updated).json()['ready_for_review']
    assert client.post('/api/quotes/search',json={}).json()['total']==0
    assert client.post('/api/manual-drafts/search',json={}).json()['total']==1
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM draft_rows').fetchone()[0]==1
        key=db.execute('SELECT storage_key FROM uploaded_files').fetchone()[0]
        assert (settings.data_dir/'uploads'/key).read_bytes()==content
        assert db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]==0
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    initialize(path)
    assert client.get(f"/api/excel/imports/{result['id']}").json()['status']=='committed'


def test_formula_row_failed_and_bad_sheet_does_not_hide_valid_rows(env):
    client,path,_,_=env
    good=sheet();second=list(good['rows'][0]);second[6]=Decimal('1200');good['rows'].append(second)
    content=write_workbook([good,sheet('表头不符',headers=['错误表头']+HEADERS[1:])])
    def formula(root):
        node=root.find("x:sheetData/x:row/x:c[@r='G9']",NS)
        E.SubElement(node,'{'+MAIN+'}f').text='999+999'
    content=mutate(content,'xl/worksheets/sheet1.xml',formula)
    result=preview(client,content).json()
    assert result['counts']['importable']==1 and result['counts']['failed']==1
    assert result['sheet_errors'][0]['sheet']=='表头不符'
    assert result['rows'][1]['fatal_errors'] and not result['rows'][1]['importable']
    url=f"/api/excel/imports/{result['id']}/commit"
    assert client.post(url,json={'row_ids':[r['id'] for r in result['rows']]}).status_code==422
    with connect(path) as db:assert db.execute('SELECT COUNT(*) FROM draft_rows').fetchone()[0]==0
    assert client.post(url,json={'row_ids':[result['rows'][0]['id']]}).status_code==200
    assert client.post(url,json={'row_ids':[result['rows'][1]['id']]}).status_code==409


@pytest.mark.parametrize('value,field',[('非数字','prices.20GP'),('-10','prices.20GP'),('1.001','prices.20GP'),('0','prices.20GP')])
def test_invalid_prices_are_pending_not_guessed(env,value,field):
    client,_,_,_=env
    source=sheet();source['rows'][0][6]=value
    result=preview(client,write_workbook([source])).json()
    assert result['rows'][0]['importable']
    assert any(i['field']==field for i in result['rows'][0]['issues'])
    assert result['rows'][0]['data']['prices']['20GP']==value


def test_unmatched_names_and_unknown_date_basis_keep_original(env):
    client,_,_,_=env
    source=sheet(headers=HEADERS+['有效期依据']);row=source['rows'][0]
    row[3]='UNKNOWN';row[4]='未知港';row[5]='未知船司';row[13]='未知代理';row.append('按进港日')
    result=preview(client,write_workbook([source]),{'validity_basis':'ETD'}).json()['rows'][0]
    assert result['data']['destination_port_id']=='' and result['data']['carrier_id']=='' and result['data']['source_series_id']==''
    assert result['data']['validity_basis']=='unknown' and result['data']['validity_original']=='按进港日'
    assert len(result['mapping_notes'])>=3 and result['raw']['英文港口名']=='UNKNOWN'


def test_export_all_rows_types_filters_currency_zero_and_literal_text(env):
    client,path,data,_=env
    insert_published(path,{**data,'remark':'=HYPERLINK("https://example.invalid")'},'usd')
    insert_published(path,{**data,'currency':'EUR','source_series_id':'series-b','carrier_id':'carrier-b'},'eur',prices={'20GP':0})
    query={'etd':'2026-11-15','quantities':{'20GP':2,'40HQ':1},'page':2,'page_size':1}
    displayed=client.post('/api/quotes/search',json=query).json()
    assert displayed['total']==2 and len(displayed['items'])==1
    response=client.post('/api/excel/export',json=query)
    assert response.status_code==200 and response.headers['x-export-count']=='2'
    assert 'attachment;' in response.headers['content-disposition']
    sheets=read_xlsx(response.content)
    assert len(sheets)==1 and sheets[0]['name']=='欧洲航线'
    cells=sheets[0]['cells']
    assert [cells[f'{chr(65+c)}7'] for c in range(18)]==HEADERS
    assert cells['J8']=='EUR' and Decimal(cells['G8'])==0
    assert cells.get('I8','')==''
    assert cells['P8']=='2026-11-01' and cells['Q9']=='2026-11-30'
    assert cells['O9'].startswith('=HYPERLINK') and not sheets[0]['formulas']
    assert cells['AJ8'].startswith('缺少所选箱型价格')
    with ZipFile(BytesIO(response.content)) as z:
        root=E.fromstring(z.read('xl/worksheets/sheet1.xml'))
        assert root.find("x:sheetData/x:row/x:c[@r='G8']",NS).get('t') is None
        assert root.find("x:sheetData/x:row/x:c[@r='P8']",NS).get('s')=='4'
        assert root.find("x:sheetData/x:row/x:c[@r='O9']",NS).get('t')=='inlineStr'
    filtered=client.post('/api/excel/export',json={**query,'carrier_id':'carrier-a'})
    assert filtered.headers['x-export-count']=='1'


def test_export_grouping_new_regions_and_empty_results(env):
    client,path,data,_=env
    assert client.post('/api/excel/export',json={}).status_code==422
    insert_published(path,data,'europe')
    tokyo=client.get('/api/catalog/ports?q=TOKYO').json()['items'][0]
    insert_published(path,{**data,'destination_port_id':tokyo['id'],'route_id':tokyo['route_ids'][0]},'asia')
    response=client.post('/api/excel/export',json={})
    assert [s['name'] for s in read_xlsx(response.content)]==['欧洲航线','亚洲航线']
    assert client.post('/api/excel/export',json={'etd':'2026-12-01'}).status_code==422
    assert client.post('/api/excel/export',json={'etd':'2026-12-01','date_scope':'all'}).headers['x-export-count']=='2'


def test_reimport_export_keeps_currency_fees_dates_and_blank_prices(env):
    client,path,data,_=env
    insert_published(path,data,'roundtrip',prices={'20GP':100025})
    exported=client.post('/api/excel/export',json={}).content
    result=preview(client,exported).json()['rows'][0]
    assert result['data']['currency']=='USD' and result['data']['prices']['20GP']=='1000.25'
    assert result['data']['prices']['40HQ']==''
    assert result['data']['extra_fees']==data['extra_fees']
    assert result['data']['service_kind']=='direct' and result['data']['validity_basis']=='ETD'


@pytest.mark.parametrize('filename,content',[('old.xls',b'old excel'),('bad.xlsx',b'not zip')])
def test_file_validation(env,filename,content):
    client,_,_,_=env
    assert preview(client,content,filename=filename).status_code==422


def test_macro_external_links_zip_size_and_bad_header(env):
    client,_,_,_=env
    content=write_workbook([sheet()])
    for entry in ('xl/vbaProject.bin','xl/externalLinks/externalLink1.xml'):
        output=BytesIO()
        with ZipFile(BytesIO(content)) as source,ZipFile(output,'w',ZIP_DEFLATED) as target:
            for name in source.namelist():target.writestr(name,source.read(name))
            target.writestr(entry,'test')
        assert preview(client,output.getvalue()).status_code==422
    assert preview(client,write_workbook([sheet(headers=['wrong']+HEADERS[1:])])).status_code==422
    assert preview(client,b'x'*(20*1024*1024+1)).status_code==413


def test_1904_and_numeric_notation_dates(env):
    client,_,_,_=env
    content=write_workbook([sheet()])
    def epoch(root):root.find('x:workbookPr',NS).set('date1904','1')
    content=mutate(content,'xl/workbook.xml',epoch)
    def serials(root):
        for address in ('P8','Q8'):
            value=root.find(f"x:sheetData/x:row/x:c[@r='{address}']/x:v",NS)
            value.text=str(int(value.text)-1462)
        root.find("x:sheetData/x:row/x:c[@r='G8']/x:v",NS).text='1.00025E+3'
    content=mutate(content,'xl/worksheets/sheet1.xml',serials)
    row=preview(client,content).json()['rows'][0]
    assert row['data']['valid_from']=='2026-11-01' and row['data']['valid_to']=='2026-11-30'
    assert Decimal(row['data']['prices']['20GP'])==Decimal('1000.25')


def test_merge_inheritance_has_original_cells_and_stops_at_new_group(env):
    client,_,_,_=env
    source=sheet();first=list(source['rows'][0]);second=list(first);second[0]=second[1]=second[2]=''
    third=list(first);third[0]='不同分组';third[1]=third[2]=''
    content=write_workbook([sheet(rows=[first,second,third])])
    def merge(root):
        nodes=root.find('x:mergeCells',NS)
        E.SubElement(nodes,'{'+MAIN+'}mergeCell',ref='A8:A9');nodes.set('count',str(len(nodes)))
    rows=preview(client,mutate(content,'xl/worksheets/sheet1.xml',merge)).json()['rows']
    assert rows[1]['raw']['航线']=='' and rows[1]['effective']['航线']=='欧洲基本港'
    assert rows[1]['effective']['国家英文名']=='NETHERLANDS'
    assert rows[2]['effective']['国家英文名']=='' and rows[2]['data']['route_id']==''


def test_export_all_origins_and_unknown_region_sheet_names(env):
    client,path,data,_=env
    insert_published(path,data,'qingdao')
    tokyo=client.get('/api/catalog/ports?q=TOKYO').json()['items'][0]
    insert_published(path,{**data,'origin_port_id':tokyo['id']},'tokyo')
    with connect(path) as db:db.execute("UPDATE regions SET name='新区域/名称' WHERE name='欧洲航线'")
    response=client.post('/api/excel/export',json={'origin_port_id':''})
    assert response.headers['x-export-count']=='2'
    exported=read_xlsx(response.content)
    assert exported[0]['name']=='新区域_名称'
    assert set(exported[0]['cells'][f'S{r}'] for r in (8,9))=={'QINGDAO','TOKYO'}
    assert '全部起运港' in exported[0]['cells']['A2']


def test_export_rejects_draft_source_and_roundtrip_is_never_published(env):
    client,path,data,_=env
    draft=client.post('/api/manual-drafts',json=data)
    assert draft.status_code==201
    assert client.post('/api/excel/export',json={}).status_code==422
    assert client.post('/api/excel/export',json={'source':'draft_preview'}).status_code==422


def test_failed_source_write_does_not_leave_database_batch(env,monkeypatch):
    from pathlib import Path
    client,path,_,_=env
    original=Path.open
    def fail(file,*args,**kwargs):
        if args and args[0]=='xb':raise OSError('simulated storage failure')
        return original(file,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail)
    with pytest.raises(OSError):preview(client,write_workbook([sheet()]))
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM excel_imports').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM uploaded_files').fetchone()[0]==0


def test_partial_commit_database_failure_rolls_back_every_row(env):
    import sqlite3
    client,path,_,_=env
    source=sheet();source['rows'].append(list(source['rows'][0]))
    result=preview(client,write_workbook([source])).json()
    blocked=result['rows'][1]['id']
    with connect(path) as db:
        db.execute(f"CREATE TRIGGER fail_second BEFORE INSERT ON draft_rows WHEN NEW.id='{blocked}' BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        client.post(f"/api/excel/imports/{result['id']}/commit",json={'row_ids':[r['id'] for r in result['rows']]})
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM draft_rows').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM source_evidence').fetchone()[0]==0
        assert db.execute('SELECT status FROM excel_imports').fetchone()[0]=='preview'


def test_ui_template_export_all_and_simple_filtered_one_sheet(env):
    client,path,data,_=env
    insert_published(path,{**data,'valid_from':'2026-10-18','valid_to':'2026-10-24','remark':'=literal'},'old')
    tokyo=client.get('/api/catalog/ports?q=TOKYO').json()['items'][0]
    insert_published(path,{**data,'destination_port_id':tokyo['id'],'route_id':tokyo['route_ids'][0]},'asia-new')
    q={'origin_port_id':'','date_scope':'all','page':2,'page_size':1}
    response=client.post('/api/excel/export',json={**q,'export_layout':'template'})
    assert response.status_code==200,response.text
    assert response.headers['x-export-count']=='2'
    sheets=read_xlsx(response.content)
    assert [s['name'] for s in sheets]==['美洲航线','欧洲航线','美加航线','亚洲航线','非洲航线','中东印巴航线']
    assert [sheets[1]['cells'][column(i)+'2'] for i in range(1,19)]==HEADERS
    assert sheets[1]['cells']['D3']=='ROTTERDAM'
    assert sheets[1]['cells']['P3']=='2026-10-18'
    assert sheets[1]['cells']['O3']=='=literal' and not sheets[1]['formulas']
    assert sheets[3]['cells']['D3']=='TOKYO'
    assert all(not s['cells'].get('D4') for s in sheets) # no original template catalog rows
    with ZipFile(BytesIO(response.content)) as z:
        root=E.fromstring(z.read('xl/worksheets/sheet2.xml'))
        assert root.find('x:sheetViews/x:sheetView/x:pane',NS).get('topLeftCell')=='F3'
        assert len(root.find('x:cols',NS))==18
    simple=client.post('/api/excel/export',json={**q,'export_layout':'simple'})
    assert simple.headers['x-export-count']=='2'
    combined=read_xlsx(simple.content)
    assert len(combined)==1 and combined[0]['name']=='查询报价'
    assert [combined[0]['cells'][column(i)+'7'] for i in range(1,19)]==HEADERS
    assert {combined[0]['cells']['D8'],combined[0]['cells']['D9']}=={'ROTTERDAM','TOKYO'}
    assert combined[0]['cells'].get('S7') is None
    filtered=client.post('/api/excel/export',json={**q,'destination_text':'鹿特','export_layout':'simple'})
    assert filtered.headers['x-export-count']=='1'
    assert read_xlsx(filtered.content)[0]['cells']['D8']=='ROTTERDAM'


def test_full_template_separates_origins_and_merges_route_groups(env):
    client,path,data,_=env
    tokyo=client.get('/api/catalog/ports?q=TOKYO').json()['items'][0]
    for n in range(3):
        insert_published(path,{**data,'origin_port_id':tokyo['id']},'tokyo-origin-'+str(n))
    response=client.post('/api/excel/export',json={'origin_port_id':'','date_scope':'all','export_layout':'template'})
    assert response.status_code==200,response.text
    sheets=read_xlsx(response.content)
    assert len(sheets)==7 and sheets[-1]['name']=='TOKYO-欧洲航线'
    assert 'TOKYO' in sheets[-1]['cells']['A1']
    assert [sheets[-1]['cells']['D'+str(i)] for i in range(3,6)]==['ROTTERDAM']*3
    with ZipFile(BytesIO(response.content)) as z:
        root=E.fromstring(z.read('xl/worksheets/sheet7.xml'))
        assert 'A3:A5' in [m.get('ref') for m in root.find('x:mergeCells',NS)]

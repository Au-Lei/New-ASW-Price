"""Task-five disposable browser and workbook QA data; never loaded by app startup."""
from datetime import date
from decimal import Decimal
from backend.tests.auth_helpers import login_admin
from pathlib import Path
from io import BytesIO
import sys
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as E

from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.database import initialize
from backend.app.excel_format import HEADERS,MAIN,NS,write_workbook
from backend.app.main import create_app
from backend.tests.quote_fixtures import sample_data,insert_published


def create_fixture(folder):
    folder=Path(folder).resolve()
    if not str(folder).startswith('/private/tmp/asw-task5-') or (folder/'asw.sqlite3').exists():
        raise ValueError('只允许尚无数据库的/private/tmp/asw-task5-*验收目录')
    database=folder/'asw.sqlite3';initialize(database);data=sample_data(database)
    insert_published(database,data,'qa-europe')
    insert_published(database,{**data,'currency':'EUR','source_series_id':'series-b','carrier_id':'carrier-b'},'qa-other',prices={'20GP':0})
    headers=HEADERS+['有效期依据','服务标识','服务类型']
    row=['欧洲基本港','NETHERLANDS','荷兰','ROTTERDAM','鹿特丹','测试船公司a',Decimal('1000.25'),None,Decimal('1900'),'USD','周三',32,'青岛-鹿特丹','测试代理a · 测试协议a','仅用于开发验收',date(2026,11,1),date(2026,11,30),'CY-CY','ETD','测试直达服务','直达']
    second=list(row);second[3]='UNKNOWN';second[4]='待匹配港口';second[6]='待询'
    third=list(row)
    content=write_workbook([{'name':'欧洲航线','metadata':['虚构报价，仅用于开发验收','含可导入行、待补齐行及公式失败行','不用于业务报价','价格未审批'],'headers':headers,'rows':[row,second,third]}])
    output=BytesIO()
    with ZipFile(BytesIO(content)) as source,ZipFile(output,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            body=source.read(name)
            if name=='xl/worksheets/sheet1.xml':
                root=E.fromstring(body);cell=root.find("x:sheetData/x:row/x:c[@r='G10']",NS)
                E.SubElement(cell,'{'+MAIN+'}f').text='1000+1000';body=E.tostring(root,encoding='utf-8')
            target.writestr(name,body)
    (folder/'import-example.xlsx').write_bytes(output.getvalue())
    with TestClient(create_app(Settings(folder,folder/'logs','Asia/Shanghai'))) as client:
        login_admin(client)
        response=client.post('/api/excel/export',json={'etd':'2026-11-15','quantities':{'20GP':2,'40HQ':1},'page_size':1,'page':2})
        assert response.status_code==200
        (folder/'export-example.xlsx').write_bytes(response.content)
    return folder


if __name__=='__main__':print(create_fixture(sys.argv[1]))

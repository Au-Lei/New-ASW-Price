"""Explicit test-only published data. Never imported by app startup or a public API."""
from pathlib import Path
import json
import sqlite3
import sys

from backend.app.database import connect, initialize
from backend.app.quotation import Draft


def sample_data(path):
    with connect(path) as db:
        db.row_factory = sqlite3.Row
        origin = db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
        port = db.execute("SELECT * FROM ports WHERE name_en='ROTTERDAM'").fetchone()
        route = db.execute('SELECT route_id FROM port_routes WHERE port_id=?',(port['id'],)).fetchone()[0]
        db.execute("INSERT INTO users VALUES ('fixture-actor','fixture-actor','disabled-test-account','admin',0,0)")
        for suffix in ('a','b'):
            db.execute('INSERT INTO carriers(id,name,name_key) VALUES (?,?,?)',('carrier-'+suffix,'测试船公司'+suffix,'TEST-CARRIER-'+suffix))
            db.execute("INSERT INTO suppliers(id,name,name_key,kind) VALUES (?,?,?,'agent')",('supplier-'+suffix,'测试代理'+suffix,'TEST-SUPPLIER-'+suffix))
            db.execute('INSERT INTO source_series(id,supplier_id,name,name_key) VALUES (?,?,?,?)',('series-'+suffix,'supplier-'+suffix,'测试协议'+suffix,'TEST-SERIES-'+suffix))
        data=Draft(origin_port_id=origin,destination_port_id=port['id'],route_id=route,carrier_id='carrier-a',source_series_id='series-a',
                   service_key='测试直达服务',service_kind='direct',fee_basis='CY-CY',currency='USD',sailing='周三',transit_days='32',
                   valid_from='2026-11-01',valid_to='2026-11-30',validity_basis='ETD',origin_confirmed=True,
                   prices={'20GP':'1000','40GP':'1800','40HQ':'1900'},included_fees='海运费',extra_fees='PSS另计',unknown_fees='目的港费用待确认',
                   manual_note='仅用于开发验收的虚构报价，禁止用于业务').model_dump()
    return data


def insert_published(path, data, identity='published-a', current=True, prices=None):
    with connect(path) as db:
        db.execute('INSERT INTO quotes(id) VALUES (?)',(identity,))
        keys=('origin_port_id','destination_port_id','route_id','carrier_id','source_series_id','service_key','service_kind','path_text','fee_basis','currency','sailing','transit_days','remark','valid_from','valid_to','validity_basis','validity_original','included_fees','extra_fees','unknown_fees','conditions','conditions_confirmed','origin_confirmed')
        record={key:data[key] for key in keys}
        record['transit_days']=int(record['transit_days']) if record['transit_days'] else None
        record.update(id=identity+'-v1',quote_id=identity,version=1,is_current=int(current),created_by='fixture-actor',approved_by='fixture-actor',submitted_at='2026-10-25T10:00:00+08:00',approved_at='2026-10-25T11:00:00+08:00')
        db.execute('INSERT INTO quote_versions('+','.join(record)+') VALUES ('+','.join('?' for _ in record)+')',tuple(record.values()))
        for box, amount in (prices if prices is not None else {'20GP':100000,'40GP':180000,'40HQ':190000}).items():
            db.execute('INSERT INTO quote_prices VALUES (?,?,?,?)',(identity+'-v1',box,amount,int(amount==0)))
    return identity+'-v1'


if __name__=='__main__':
    # Dedicated disposable demo only. Explicit prefix prevents accidental use of the user's data DB.
    folder=Path(sys.argv[1]).resolve()
    if not str(folder).startswith('/private/tmp/asw-task4-') or (folder/'asw.sqlite3').exists():
        raise SystemExit('只允许尚无数据库的 /private/tmp/asw-task4-* 隔离目录')
    database=folder/'asw.sqlite3'
    initialize(database)
    sample=sample_data(database)
    insert_published(database,sample)
    insert_published(database,{**sample,'carrier_id':'carrier-b','source_series_id':'series-b','currency':'EUR','conditions':'最低10柜；仅限轻货','conditions_confirmed':True},identity='published-b',prices={'20GP':90000})
    (folder/'manual-example.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2))
    print('已建立隔离验收资料，两条虚构正式报价。',folder)

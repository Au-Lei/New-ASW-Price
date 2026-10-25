"""Rebuild editable image drafts from cached model responses, without resending images."""
import json,sqlite3
from uuid import uuid4
from .database import connect
from .ai_extract import Extraction,Document,normalize_extracted_quotes,unique_image_rows,shared_image_dates
from .quotation import validation
from .review import dump,audit
VERSION='cached-rebuild-1'

def cached_image_rows(db,task_id):
    attempt=db.execute("SELECT id FROM extraction_attempts WHERE task_id=? AND status='success' ORDER BY rowid DESC LIMIT 1",(task_id,)).fetchone()
    if not attempt:raise ValueError('没有可回放的成功提取记录')
    rows=[];seen=[];recognized=0
    for result in db.execute("SELECT location_json,response_json FROM ai_calls WHERE attempt_id=? AND status='success' ORDER BY ordinal",(attempt[0],)):
        part=json.loads(result[0]);payload=Extraction.model_validate_json(json.loads(result[1])['content']);recognized+=len(payload.quotes)
        rows.extend(unique_image_rows(normalize_extracted_quotes(db,payload.quotes,Document('image',[],{},{},''),part),part,seen))
    shared_image_dates(rows)
    return rows,recognized

def signature(raw):
    return (str(raw.get('facts',{}).get('destination_port','')).strip().casefold(),dump(raw.get('prices',{})))

def rebuild(database,task_id,apply=False):
    report={}
    with connect(database) as db:
        db.row_factory=sqlite3.Row
        if apply:db.execute('BEGIN IMMEDIATE')
        item=db.execute('SELECT i.*,b.status batch_status FROM ai_imports i JOIN upload_batches b ON b.id=i.batch_id WHERE i.task_id=?',(task_id,)).fetchone()
        if not item:raise ValueError('提取结果不存在')
        old_rows=db.execute("SELECT d.*,b.status AS batch_status FROM draft_rows d JOIN upload_batches b ON b.id=d.batch_id WHERE json_extract(d.raw_json,'$.ai_import_id')=? ORDER BY d.rowid",(item['id'],)).fetchall()
        editable=[r for r in old_rows if r['batch_status'] in ('draft','rejected','withdrawn')]
        target_batch=editable[0]['batch_id'] if editable else uuid4().hex
        created=False
        if any(json.loads(r['raw_json']).get('rebuild_version')==VERSION for r in editable):return {'already_rebuilt':True}
        normalized,recognized=cached_image_rows(db,task_id)
        original={r['id']:r for r in json.loads(item['preview_json'])['rows']}
        pool={}
        for record in old_rows:
            raw=json.loads(record['raw_json']);pool.setdefault(signature(raw.get('original',{})),[]).append(record)
        who=dict(db.execute("SELECT * FROM users WHERE id='local-admin'").fetchone())
        report={'recognized':recognized,'retained':len(normalized),'updated':0,'restored':0,'countries_complete':0,'ports_complete':0,'skipped_locked':0,'target_batch_id':target_batch}
        for source_order,row in enumerate(normalized):
            matches=pool.get(signature(row['raw']),[]);old=matches.pop(0) if matches else None
            if old and old['batch_status'] not in ('draft','rejected','withdrawn'):
                report['skipped_locked']+=1
                continue
            if old:
                values=json.loads(old['edited_json']);baseline=original.get(old['id'],{}).get('data')
                if not baseline:continue
                updated={k:(row['data'][k] if values[k]==baseline[k] else values[k]) for k in values}
                rid=old['id'];raw=json.loads(old['raw_json']);report['updated']+=1
            else:
                rid=uuid4().hex;updated=row['data'];raw={'kind':'ai','ai_import_id':item['id'],'model':'deepseek-flash','prompt_version':'cached-rebuild'};report['restored']+=1
            raw.update(original=row['raw'],mapping_notes=row['mapping_notes'],rebuild_version=VERSION,source_order=source_order)
            fields=row['raw']['enrichment']['fields']
            report['countries_complete']+=bool(fields.get('countryEn') and fields.get('countryCn'))
            report['ports_complete']+=bool(fields.get('portEn') and fields.get('portCn'))
            if not apply:continue
            if not editable and not created:
                db.execute('INSERT INTO upload_batches(id,owner_id) VALUES (?,?)',(target_batch,who['id']));created=True
            issues,_=validation(db,updated)
            if old:
                db.execute('UPDATE draft_rows SET raw_json=?,edited_json=?,validation_json=?,revision=revision+1,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(dump(raw),dump(updated),dump(issues),who['id'],rid))
            else:
                db.execute('INSERT INTO draft_rows(id,batch_id,task_id,raw_json,edited_json,validation_json,updated_by) VALUES (?,?,?,?,?,?,?)',(rid,target_batch,task_id,dump(raw),dump(updated),dump(issues),who['id']))
            # Keep all original evidence; append only missing source snippets.
            existing={r[0] for r in db.execute('SELECT manual_note FROM source_evidence WHERE draft_row_id=?',(rid,))}
            for source in row['sources']:
                if source['excerpt'] in existing:continue
                db.execute('INSERT INTO source_evidence(id,file_id,task_id,draft_row_id,page_number,image_region_json,manual_note) VALUES (?,?,?,?,?,?,?)',(uuid4().hex,item['file_id'],task_id,rid,source['page'],dump(source['region']) if source['region'] else None,source['excerpt']))
            audit(db,who,'restore_image_row' if not old else 'rebuild_image_row','draft_row',rid,{'data':values} if old else None,{'data':updated,'rules':VERSION})
        if apply:db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(target_batch,))
    return report

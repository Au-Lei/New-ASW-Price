"""Host-side, local-only correction of editable image drafts; no provider requests."""
import json,sqlite3
from uuid import uuid4
from .database import connect
from .ai_extract import Quote,Document,normalize_extracted_quotes
from .review import dump,audit
from .quotation import validation
VERSION='catalog-fields-1'

def repair_image_drafts(database,apply=False):
    report={'reviewed':0,'updated':0,'added':0,'unresolved':0}
    with connect(database) as db:
        db.row_factory=sqlite3.Row
        if apply:db.execute('BEGIN IMMEDIATE')
        who=dict(db.execute("SELECT * FROM users WHERE id='local-admin'").fetchone())
        source_rows=db.execute("""SELECT d.* FROM draft_rows d JOIN upload_batches b ON b.id=d.batch_id
          WHERE b.status IN ('draft','rejected','withdrawn')
          AND EXISTS(SELECT 1 FROM source_evidence e JOIN uploaded_files f ON f.id=e.file_id
                     WHERE e.draft_row_id=d.id AND f.media_type LIKE 'image/%')""").fetchall()
        for record in source_rows:
            raw=json.loads(record['raw_json']);original=raw.get('original',{})
            if raw.get('normalization_version')==VERSION or not original.get('facts'):continue
            report['reviewed']+=1;old=json.loads(record['edited_json'])
            item=db.execute('SELECT preview_json FROM ai_imports WHERE id=?',(raw.get('ai_import_id'),)).fetchone()
            if not item:continue
            baseline=next((r['data'] for r in json.loads(item[0])['rows'] if r['id']==record['id']),None)
            if baseline is None:continue
            quote=Quote.model_validate({k:v for k,v in original.items() if k in Quote.model_fields})
            normalized=normalize_extracted_quotes(db,[quote],Document('image',[],{},{},''),{'pages':[1]})
            # Respect an explicitly edited destination; do not split an operator's replacement.
            if old['destination_port_id']!=baseline['destination_port_id']:continue
            evidence=db.execute('SELECT * FROM source_evidence WHERE draft_row_id=?',(record['id'],)).fetchall()
            for index,row in enumerate(normalized):
                values={k:(row['data'][k] if old[k]==baseline[k] else old[k]) for k in old}
                updated_raw={**raw,'original':row['raw'],'mapping_notes':row['mapping_notes'],'normalization_version':VERSION}
                if not row['data']['destination_port_id']:report['unresolved']+=1
                if index==0:report['updated']+=1
                else:report['added']+=1
                if not apply:continue
                issues,_=validation(db,values);rid=record['id'] if index==0 else uuid4().hex
                if index==0:
                    db.execute('UPDATE draft_rows SET edited_json=?,raw_json=?,validation_json=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?',(dump(values),dump(updated_raw),dump(issues),who['id'],rid))
                else:
                    db.execute('INSERT INTO draft_rows(id,batch_id,task_id,raw_json,edited_json,validation_json,included,updated_by) VALUES (?,?,?,?,?,?,?,?)',(rid,record['batch_id'],record['task_id'],dump(updated_raw),dump(values),dump(issues),record['included'],who['id']))
                    for source in evidence:
                        copy=dict(source);copy.update(id=uuid4().hex,draft_row_id=rid)
                        db.execute('INSERT INTO source_evidence('+','.join(copy)+') VALUES ('+','.join('?' for _ in copy)+')',tuple(copy.values()))
                audit(db,who,'normalize_image_draft','draft_row',rid,{'data':old} if index==0 else {'split_from':record['id']},{'data':values,'rules':VERSION})
            if apply:db.execute('UPDATE upload_batches SET revision=revision+1 WHERE id=?',(record['batch_id'],))
    return report

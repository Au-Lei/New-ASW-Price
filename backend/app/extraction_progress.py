"""Durable, factual extraction stages; progress never represents a guessed percentage."""
import json
from .database import connect


def report_progress(database,task_id,token,stage,completed=0,total=0):
    progress={'stage':stage,'completed':completed,'total':total}
    with connect(database) as db:
        db.execute("UPDATE extraction_tasks SET progress_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND run_token=? AND status='processing'",(json.dumps(progress),task_id,token))

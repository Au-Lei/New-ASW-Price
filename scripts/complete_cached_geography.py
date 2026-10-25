"""Only public geographic names are sent; cached quotation rows stay local."""
import sys,argparse,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import load_settings
from backend.app.database import connect
from backend.app.rebuild_image_drafts import cached_image_rows
from backend.app.port_metadata import complete_unknown_ports
p=argparse.ArgumentParser();p.add_argument('task_id');args=p.parse_args();settings=load_settings();database=settings.data_dir/'asw.sqlite3'
with connect(database) as db:
 db.row_factory=sqlite3.Row;rows,_=cached_image_rows(db,args.task_id)
print('Sending public port names only; no images, prices, source files or customer data.',flush=True)
requests=complete_unknown_ports(database,settings,rows,lambda:None)
print({'geographic_requests':requests,'rows_with_full_geography':sum(all(r['raw']['enrichment']['fields'].get(k) for k in ('portEn','portCn','countryEn','countryCn')) for r in rows),'rows':len(rows)})

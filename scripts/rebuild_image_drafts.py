import sys,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import load_settings
from backend.app.rebuild_image_drafts import rebuild
p=argparse.ArgumentParser();p.add_argument('task_id');p.add_argument('--apply',action='store_true');args=p.parse_args()
s=load_settings();print(rebuild(s.data_dir/'asw.sqlite3',args.task_id,args.apply))

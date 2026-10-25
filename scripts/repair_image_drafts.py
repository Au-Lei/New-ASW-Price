"""Reorganize existing editable image results locally without AI or publication."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import load_settings
from backend.app.repair_ai_drafts import repair_image_drafts
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
settings=load_settings()
print(repair_image_drafts(settings.data_dir/'asw.sqlite3',args.apply))

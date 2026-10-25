"""Host-side backup and non-destructive restore. Run with the project Python."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import load_settings
from backend.app.backups import create_backup,restore_backup

parser=argparse.ArgumentParser(description='海运报价系统备份与恢复')
sub=parser.add_subparsers(dest='command',required=True)
sub.add_parser('backup')
r=sub.add_parser('restore');r.add_argument('archive',type=Path);r.add_argument('destination',type=Path)
args=parser.parse_args()
try:
 if args.command=='backup':
  settings=load_settings();result=create_backup(settings.data_dir/'asw.sqlite3',settings.data_dir)
 else:result=restore_backup(args.archive,args.destination)
 print(result)
except Exception as exc:raise SystemExit('操作未完成：'+str(exc))

"""Consistent SQLite + immutable originals backup; restore only into a new folder."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from .database import connect, MIGRATIONS
from .auth import current_actor, event
from .review import dump

NAME=re.compile(r'backup-\d{8}T\d{6}-[a-f0-9]{8}\.zip')
KEY=re.compile(r'[a-f0-9]{32}\.(xlsx|pdf|png|jpe?g)')
def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def create_backup(database,data_dir,who=None):
    data_dir=Path(data_dir);folder=data_dir/'backups';folder.mkdir(parents=True,exist_ok=True)
    name='backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]+'.zip';target=folder/name
    with tempfile.TemporaryDirectory(prefix='asw-backup-',dir=folder) as temporary:
        temp=Path(temporary);snapshot=temp/'asw.sqlite3';archive=temp/'archive.zip'
        with connect(database) as lock:
            lock.row_factory=sqlite3.Row;lock.execute('BEGIN IMMEDIATE')
            if who and current_actor(lock)['role']!='admin':raise HTTPException(403,'仅管理员可备份')
            files=[dict(f) for f in lock.execute('SELECT id,storage_key,sha256,size_bytes FROM uploaded_files ORDER BY id')]
            # Hold writer exclusion while an independent reader snapshots committed data.
            with sqlite3.connect(database) as source,sqlite3.connect(snapshot) as dest:source.backup(dest)
            entries=[]
            with ZipFile(archive,'w',ZIP_DEFLATED) as z:
                z.write(snapshot,'asw.sqlite3')
                for f in files:
                    if not KEY.fullmatch(f['storage_key']):raise ValueError('原件存储标识无效，备份未完成')
                    file=data_dir/'uploads'/f['storage_key']
                    if file.is_symlink() or not file.is_file() or file.stat().st_size!=f['size_bytes'] or sha(file)!=f['sha256']:raise ValueError('原件缺失或校验不符，备份未完成：'+f['id'])
                    member='uploads/'+f['storage_key'];z.write(file,member);entries.append({**f,'member':member})
                manifest={'format':1,'schema_version':lock.execute('SELECT max(version) FROM schema_migrations').fetchone()[0],'created_at':datetime.now(timezone.utc).isoformat(),'database_sha256':sha(snapshot),'files':entries}
                z.writestr('manifest.json',dump(manifest))
            if who:event(lock,who,'create_backup','backup',name)
        os.chmod(archive,0o600);os.replace(archive,target)
    return {'name':name,'size_bytes':target.stat().st_size,'file_count':len(files),'created_at':manifest['created_at']}

def restore_backup(archive,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('恢复目录必须是尚不存在的新目录，不能覆盖现有数据')
    if not destination.parent.is_dir():raise ValueError('恢复目录的上级目录不存在')
    with tempfile.TemporaryDirectory(prefix='.asw-restore-',dir=destination.parent) as temporary:
        staging=Path(temporary)
        with ZipFile(archive) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(infos)>100000 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>10*1024**3:raise ValueError('备份体积、文件数或重复成员不符合要求')
            if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>20*1024*1024:raise ValueError('缺少有效备份清单')
            manifest=json.loads(z.read('manifest.json'))
            if manifest.get('format')!=1 or manifest.get('schema_version')!=MIGRATIONS[-1][0]:raise ValueError('备份版本与当前程序不匹配，请使用对应版本程序')
            expected={'manifest.json','asw.sqlite3'}
            for f in manifest['files']:
                if not KEY.fullmatch(f['storage_key']) or f['member']!='uploads/'+f['storage_key']:raise ValueError('备份路径不合法')
                expected.add(f['member'])
            if set(names)!=expected or any(i.flag_bits&1 or (i.external_attr>>16)&0o170000==0o120000 for i in infos):raise ValueError('备份含额外、加密或链接文件')
            for name in expected-{'manifest.json'}:
                path=staging/name;path.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as source,path.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
            if sha(staging/'asw.sqlite3')!=manifest['database_sha256']:raise ValueError('数据库校验失败')
            for f in manifest['files']:
                path=staging/f['member']
                if path.stat().st_size!=f['size_bytes'] or sha(path)!=f['sha256']:raise ValueError('原件校验失败')
        with sqlite3.connect(staging/'asw.sqlite3') as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('备份数据库完整性或关联检查失败')
            if db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]!=manifest['schema_version']:raise ValueError('数据库实际版本与清单不符')
            files=sorted(db.execute('SELECT id,storage_key,sha256,size_bytes FROM uploaded_files'))
            if files!=sorted((f['id'],f['storage_key'],f['sha256'],f['size_bytes']) for f in manifest['files']):raise ValueError('清单与原件数据库记录不一致')
            db.execute('DELETE FROM auth_sessions');db.execute('DELETE FROM auth_failures')
        (staging/'uploads').mkdir(exist_ok=True)
        if destination.exists():raise ValueError('恢复目录已存在，未覆盖任何数据')
        os.rename(staging,destination)
    return {'destination':str(destination),'file_count':len(manifest['files'])}

def backup_router(database,settings):
    router=APIRouter(prefix='/api/backups',tags=['备份'])
    def require_admin(db):
        who=current_actor(db)
        if who['role']!='admin':raise HTTPException(403,'仅管理员可备份')
        return who
    @router.get('')
    def listing():
        with connect(database) as db:db.row_factory=sqlite3.Row;require_admin(db)
        folder=settings.data_dir/'backups'
        return {'items':[{'name':p.name,'size_bytes':p.stat().st_size} for p in sorted(folder.glob('backup-*.zip'),reverse=True) if NAME.fullmatch(p.name) and not p.is_symlink()]}
    @router.post('')
    def create():
        with connect(database) as db:db.row_factory=sqlite3.Row;who=require_admin(db)
        try:return create_backup(database,settings.data_dir,who)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    @router.get('/{name}')
    def download(name:str):
        with connect(database) as db:db.row_factory=sqlite3.Row;who=require_admin(db)
        if not NAME.fullmatch(name):raise HTTPException(404,'备份不存在')
        file=settings.data_dir/'backups'/name
        if file.is_symlink() or not file.is_file():raise HTTPException(404,'备份不存在')
        with connect(database) as db:event(db,who,'download_backup','backup',name)
        return FileResponse(file,media_type='application/zip',filename=name,headers={'Cache-Control':'no-store'})
    return router

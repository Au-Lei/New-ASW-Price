"""Server-side sessions, password hashing and explicit administrative privileges."""
import hashlib
import hmac
import os
from contextvars import ContextVar
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .database import connect

principal_id=ContextVar('principal_id',default=None)
COOKIE='asw_session'

def password_hash(password):
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=3,maxmem=64*1024*1024).hex()
    return 'scrypt$32768$8$3$'+salt.hex()+'$'+digest

def verify(password,encoded):
    try:
        kind,n,r,p,salt,digest=encoded.split('$')
        if (kind,n,r,p)!=('scrypt','32768','8','3'):return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=3,maxmem=64*1024*1024).hex()
        return hmac.compare_digest(actual,digest)
    except (ValueError,TypeError):return False

DUMMY=password_hash('dummy-password-no-account')

def public_user(row):return {k:row[k] for k in ('id','username','role','active','must_change_password')}
def current_actor(db):
    uid=principal_id.get()
    row=db.execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone() if uid else None
    if not row:raise HTTPException(401,'请先登录')
    return dict(row)
def system_actor(db):
    row=db.execute("SELECT * FROM users WHERE id='local-admin'").fetchone()
    if not row:raise RuntimeError('系统审计账号缺失')
    return dict(row)
def event(db,who,action,entity='account',identity=None):
    db.execute('INSERT INTO audit_events(id,actor_id,actor_label,action,entity_type,entity_id) VALUES (?,?,?,?,?,?)',(uuid4().hex,who['id'],who['username'],action,entity,identity or who['id']))

def bootstrap(database,settings):
    with connect(database) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT * FROM users WHERE id='local-admin'").fetchone()
        if row and row['password_hash']=='!local-only-no-login':
            name='admin' if not db.execute("SELECT 1 FROM users WHERE username='admin' AND id!='local-admin'").fetchone() else row['username']
            temporary=secrets.token_urlsafe(24)
            file=settings.data_dir/'admin-initial.txt';staged=settings.data_dir/('.admin-initial-'+uuid4().hex)
            fd=os.open(staged,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:stream.write(f'用户名：{name}\n临时密码：{temporary}\n首次登录必须修改密码；修改后本文件会自动移除。\n')
            os.replace(staged,file)
            db.execute("UPDATE users SET username=?,password_hash=?,role='admin',active=1,must_change_password=1 WHERE id='local-admin'",(name,password_hash(temporary)))
            db.execute("UPDATE upload_batches SET owner_id='local-admin' WHERE owner_id IS NULL")
            event(db,dict(row),'bootstrap_admin')

def same_origin(request):
    origin=request.headers.get('origin')
    if origin and origin!=f'{request.url.scheme}://{request.headers.get("host","")}':raise HTTPException(403,'请求来源不匹配，请从系统页面重试')
    if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'不接受跨站请求')

def authorize(request,database):
    path=request.url.path
    if not path.startswith('/api/'):return None
    if request.method not in ('GET','HEAD','OPTIONS'):same_origin(request)
    if path in ('/api/health','/api/auth/login','/api/auth/status'):return None
    raw=request.cookies.get(COOKIE,'')
    with connect(database) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT s.csrf_token,s.expires_at,u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND u.active=1',(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
    if not row or row['expires_at']<=int(time.time()):raise HTTPException(401,'登录已过期，请重新登录')
    if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),row['csrf_token']):raise HTTPException(403,'请求验证已失效，请刷新页面后重试')
    if row['must_change_password'] and path not in ('/api/auth/me','/api/auth/password','/api/auth/logout'):raise HTTPException(403,'首次登录请先修改临时密码')
    if (path.startswith('/api/auth/users') or path.startswith('/api/backups') or (path.startswith('/api/catalog/') and request.method not in ('GET','HEAD'))) and row['role']!='admin':raise HTTPException(403,'仅管理员可执行此操作')
    return row['id']

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Login(Strict):
    username:str=Field(min_length=1,max_length=80)
    password:str=Field(min_length=1,max_length=128)
class ChangePassword(Strict):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)
class NewUser(Strict):
    username:str=Field(min_length=1,max_length=80,pattern=r'^[\w.\-\u4e00-\u9fff]+$')
    role:Literal['business','admin']='business'
class UpdateUser(Strict):
    role:Literal['business','admin']
    active:bool

def auth_router(database,settings):
    router=APIRouter(prefix='/api/auth',tags=['账号登录'])
    def admin(db):
        who=current_actor(db)
        if who['role']!='admin':raise HTTPException(403,'仅管理员可管理账号')
        return who
    def new_session(db,uid,response):
        raw=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=int(time.time())
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(now,))
        db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)',(hashlib.sha256(raw.encode()).hexdigest(),uid,csrf,now,now+settings.session_hours*3600))
        response.set_cookie(COOKIE,raw,max_age=settings.session_hours*3600,httponly=True,secure=settings.cookie_secure,samesite='strict',path='/')
        return csrf
    @router.get('/status')
    def status():return {'login_required':True,'initial_credentials':'data/admin-initial.txt（仅首次初始化）'}
    @router.post('/login')
    def login(value:Login,request:Request,response:Response):
        now=int(time.time());ip=request.client.host if request.client else 'unknown'
        keys=[hashlib.sha256(('account:'+value.username.casefold()+':'+ip).encode()).hexdigest(),hashlib.sha256(('ip:'+ip).encode()).hexdigest()]
        failed=False
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            for key,limit in zip(keys,(5,30)):
                state=db.execute('SELECT * FROM auth_failures WHERE key=?',(key,)).fetchone()
                if state and state['window_start']>now-900 and state['failures']>=limit:raise HTTPException(429,'登录失败次数过多，请15分钟后重试')
            row=db.execute('SELECT * FROM users WHERE username=?',(value.username,)).fetchone()
            correct=verify(value.password,row['password_hash'] if row else DUMMY)
            if not correct or not row or not row['active']:
                failed=True
                for key in keys:db.execute('INSERT INTO auth_failures VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET failures=CASE WHEN window_start<=? THEN 1 ELSE failures+1 END,window_start=CASE WHEN window_start<=? THEN excluded.window_start ELSE window_start END',(key,now,now-900,now-900))
            else:
                db.execute('DELETE FROM auth_failures WHERE key=?',(keys[0],))
                csrf=new_session(db,row['id'],response);event(db,dict(row),'login')
                result={'user':public_user(row),'csrf_token':csrf}
        if failed:raise HTTPException(401,'用户名或密码不正确，或账号已停用')
        return result
    @router.get('/me')
    def me(request:Request):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;who=current_actor(db)
            session=db.execute('SELECT csrf_token FROM auth_sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),)).fetchone()
            return {'user':public_user(who),'csrf_token':session['csrf_token']}
    @router.post('/logout')
    def logout(request:Request,response:Response):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;who=current_actor(db)
            db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),));event(db,who,'logout')
        response.delete_cookie(COOKIE,path='/');return {'ok':True}
    @router.post('/password')
    def change(value:ChangePassword,response:Response):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=current_actor(db)
            if not verify(value.current_password,who['password_hash']):raise HTTPException(403,'当前密码不正确')
            if len(value.new_password.strip())<12:raise HTTPException(422,'新密码去除首尾空白后至少12位')
            if value.current_password==value.new_password:raise HTTPException(422,'新密码须与当前密码不同')
            db.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(password_hash(value.new_password),who['id']))
            db.execute('DELETE FROM auth_sessions WHERE user_id=?',(who['id'],));event(db,who,'change_password')
        if who['id']=='local-admin':(settings.data_dir/'admin-initial.txt').unlink(missing_ok=True)
        response.delete_cookie(COOKIE,path='/');return {'ok':True,'login_required':True}
    @router.get('/users')
    def users():
        with connect(database) as db:
            db.row_factory=sqlite3.Row;admin(db);return {'items':[public_user(r) for r in db.execute('SELECT * FROM users ORDER BY rowid')]}
    @router.post('/users',status_code=201)
    def create(value:NewUser):
        temporary=secrets.token_urlsafe(18)
        try:
            with connect(database) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=admin(db)
                if db.execute('SELECT count(*) FROM users WHERE active=1').fetchone()[0]>=10:raise HTTPException(422,'最多启用10个账号')
                uid=uuid4().hex;db.execute('INSERT INTO users(id,username,password_hash,role,must_change_password) VALUES (?,?,?,?,1)',(uid,value.username,password_hash(temporary),value.role));event(db,who,'create_user',identity=uid)
        except sqlite3.IntegrityError:raise HTTPException(409,'用户名已存在')
        return {'id':uid,'temporary_password':temporary}
    @router.put('/users/{uid}')
    def update(uid:str,value:UpdateUser):
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=admin(db);old=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not old:raise HTTPException(404,'账号不存在')
            if old['active'] and old['role']=='admin' and (not value.active or value.role!='admin') and db.execute("SELECT count(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]<=1:raise HTTPException(422,'须保留至少一名启用的管理员')
            if value.active and not old['active'] and db.execute('SELECT count(*) FROM users WHERE active=1').fetchone()[0]>=10:raise HTTPException(422,'最多启用10个账号')
            db.execute('UPDATE users SET role=?,active=? WHERE id=?',(value.role,int(value.active),uid));db.execute('DELETE FROM auth_sessions WHERE user_id=?',(uid,));event(db,who,'update_user',identity=uid)
        return {'ok':True}
    @router.post('/users/{uid}/reset-password')
    def reset(uid:str):
        temporary=secrets.token_urlsafe(18)
        with connect(database) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');who=admin(db)
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise HTTPException(404,'账号不存在')
            db.execute('UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?',(password_hash(temporary),uid));db.execute('DELETE FROM auth_sessions WHERE user_id=?',(uid,));event(db,who,'reset_password',identity=uid)
        return {'temporary_password':temporary}
    return router

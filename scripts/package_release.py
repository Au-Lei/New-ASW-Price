"""Build an allowlisted source + built frontend release; excludes credentials and business data."""
from pathlib import Path
from hashlib import sha256
import json
from zipfile import ZipFile,ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]
allowed=[]
for name in ('README.md','.env.example','.env.ai.example','.gitignore'):
    allowed.append(ROOT/name)
for folder,suffixes in [('backend',{'.py','.sql','.json','.txt','.in'}),('scripts',{'.py','.sh'}),('docs',{'.md','.json','.png','.html'})]:
    allowed.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts)
for name in ('package.json','package-lock.json','index.html','vite.config.js'):
    allowed.append(ROOT/'frontend'/name)
for folder in ('frontend/src','frontend/public','frontend/tests'):
    allowed.extend(p for p in (ROOT/folder).rglob('*') if p.is_file())
allowed.extend(p for p in (ROOT/'frontend/dist').rglob('*') if p.is_file())
allowed.append(ROOT/'backend/assets/quotation-template.xlsx')
# Synthetic BIFF8 fixtures are needed by the shipped regression tests.
allowed.extend((ROOT/'backend/tests/fixtures').glob('legacy-*.xls'))
manifest={}
for p in sorted(set(allowed)):
    if not p.is_file() or p.is_symlink():raise SystemExit('Missing or unsafe release entry: '+str(p.relative_to(ROOT)))
    rel=p.relative_to(ROOT).as_posix()
    if any(x in rel.split('/') for x in ('.env','.env.ai','data','logs','.venv','node_modules','.git')):raise SystemExit('Forbidden release entry')
    manifest[rel]=sha256(p.read_bytes()).hexdigest()
folder=ROOT/'deliverables';folder.mkdir(exist_ok=True);target=folder/'ASW-v1.0.0.zip'
with ZipFile(target,'w',ZIP_DEFLATED) as z:
    for rel in manifest:z.write(ROOT/rel,'ASW-v1.0.0/'+rel)
    z.writestr('ASW-v1.0.0/RELEASE-MANIFEST.json',json.dumps({'version':'1.0.0','files':manifest},ensure_ascii=False,indent=2))
(target.with_suffix('.zip.sha256')).write_text(sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
print('Release:',target,'files:',len(manifest),'bytes:',target.stat().st_size)

"""Verify manifest then install and run the archive in a fresh disposable directory. No AI calls."""
from hashlib import sha256
from pathlib import Path
import json,subprocess,sys,tempfile
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
archive=ROOT/'deliverables/ASW-v1.0.0.zip'
with tempfile.TemporaryDirectory(prefix='asw-release-') as temp:
    folder=Path(temp).resolve()
    with ZipFile(archive) as z:
        for member in z.namelist():
            target=(folder/member).resolve()
            if not target.is_relative_to(folder):raise SystemExit('Unsafe archive path')
        z.extractall(folder)
    project=folder/'ASW-v1.0.0';manifest=json.loads((project/'RELEASE-MANIFEST.json').read_text())
    for name,digest in manifest['files'].items():assert sha256((project/name).read_bytes()).hexdigest()==digest,name
    def run(args):subprocess.run(args,cwd=project,check=True)
    run(['uv','venv','--python',sys.executable,'.venv'])
    run(['uv','pip','sync','--offline','--python','.venv/bin/python','backend/requirements.txt'])
    run(['npm','ci','--offline','--prefix','frontend'])
    run(['npm','--prefix','frontend','run','build'])
    assert (project/'frontend/dist/favicon.svg').is_file(), 'Missing public assets after rebuild'
    run([str(project/'.venv/bin/python'),'-m','pytest','backend/tests/test_environment.py','-q'])
    run([str(project/'.venv/bin/python'),'scripts/accept_delivery.py','--report',str(folder/'offline-e2e.json')])
    report={'status':'passed','scope':'fresh archive extraction, manifest, isolated Python dependencies, npm ci, frontend rebuild, environment tests, offline real HTTP workflow and restore','ai_calls':0}
    (ROOT/'docs/task10-clean-install-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Fresh release installation and offline workflow: PASS')

import os,subprocess,sys
from pathlib import Path
base=Path(__file__).parent
root=base/'due-attempt-guard'
path=root/'package/src/due/django/evidence/attempts.py'
original=path.read_bytes()
env=dict(os.environ,DUE_DATABASE_URL='postgresql://postgres@127.0.0.1:55449/due_guard',PYTHONPATH=str((root/'package/src').resolve()))
args=[str((base/'due-row-reads/.venv/Scripts/python.exe').resolve()),'-m','pytest','-c','package/pyproject.toml','package/tests/evidence/test_entry_check.py','-q','--reuse-db']
try:
 path.write_bytes(subprocess.check_output(['git','-C',str(root),'show','origin/main:package/src/due/django/evidence/attempts.py']))
 with (base/'gap-retained-entry-red.log').open('w') as log:
  red=subprocess.run(args+['-k','combines'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
 assert red.returncode==1
finally:path.write_bytes(original)
with (base/'gap-retained-entry-green.log').open('w') as log:
 green=subprocess.run(args,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
assert green.returncode==0
print('baseline budget fails; candidate controls pass')

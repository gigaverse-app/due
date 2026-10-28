import os,subprocess,sys
from pathlib import Path
base=Path(__file__).parent
for name,checkout in [('lookup','mail-gap-profile'),('guard','mail-gap-guard'),('combined','mail-gap-combined')]:
 env=dict(os.environ,MAIL_TEST_DB='test_gap_profile_due',PYTHONUNBUFFERED='1')
 target='gigaverse/core/mail/reminders/engine/tests'
 if name!='combined': target+='/test_bulk_delivery_due_work_contract.py'
 with (base/f'gap-mail-{name}-proofs.log').open('w') as log:
  r=subprocess.run([sys.executable,str(base/'run_backend_arm.py'),checkout,'-m','pytest','-c','pytest.ini',target,'-q','--reuse-db','-m','not real_celery_worker','-p','due.django.conformance.reporting',f'--due-work-profile-report={base}/gap-mail-{name}-profiles.json',f'--durable-work-report={base}/gap-mail-{name}-effects.json'],env=env,stdout=log,stderr=subprocess.STDOUT)
 print(name,r.returncode,flush=True)
 if r.returncode: print((base/f'gap-mail-{name}-proofs.log').read_text()[-4000:]);sys.exit(r.returncode)

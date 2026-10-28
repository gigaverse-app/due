import os, subprocess, sys
from pathlib import Path
base=Path(__file__).parent
source=(base/'mail-claim-entry/perf/scripts/measure_bulk_mail_persistence.py').read_text()
source=source.replace('import hashlib','import cProfile\nimport hashlib')
source=source.replace('            result = operation()', """            profiler = cProfile.Profile()
            profiler.enable()
            try:
                result = operation()
            finally:
                profiler.disable()
                profiler.dump_stats(str(Path(os.environ['MAIL_PROFILE_DIR']) / f'{time.time_ns()}.prof'))""")
for name,checkout,engine in [('native','mail-flatten-main','native'),('due','mail-claim-entry','due')]:
 root=base/checkout
 out=base/f'gap-profile-{name}'
 out.mkdir(exist_ok=True)
 target=root/'perf/results/dev/gap-profile/test_mail_profile.py'
 target.parent.mkdir(parents=True,exist_ok=True)
 target.write_text(source)
 env=dict(os.environ,MAIL_PROFILE_DIR=str(out),MAIL_TEST_DB='test_gap_profile_'+name,MAIL_BENCH_ENGINE=engine,MAIL_BENCH_BLOCK='gap-profile-'+name,MAIL_BENCH_SAMPLES='8',MAIL_BENCH_OUTPUT=str(out/'results.jsonl'))
 with (out/'run.log').open('w') as log:
  result=subprocess.run([sys.executable,str(base/'run_backend_arm.py'),checkout,'-m','pytest','-c','pytest.ini',str(target),'-q','--reuse-db','-k','50-success'],env=env,stdout=log,stderr=subprocess.STDOUT)
 print(name,result.returncode,flush=True)
 if result.returncode: print((out/'run.log').read_text()[-4000:]); sys.exit(result.returncode)

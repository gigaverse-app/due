import json,os,subprocess,sys
from pathlib import Path
base=Path(__file__).parent
source=(base/'mail-claim-entry/perf/scripts/measure_bulk_mail_persistence.py').read_text()
source=source.replace('        started = time.perf_counter()\n        with connection.execute_wrapper', '        cpu_started = time.process_time()\n        started = time.perf_counter()\n        with connection.execute_wrapper')
source=source.replace('            "elapsed_ms": elapsed,', '            "elapsed_ms": elapsed,\n            "cpu_ms": (time.process_time() - cpu_started) * 1000,')
sequence=[('native','mail-flatten-main','native'),('baseline','mail-claim-entry','due'),('lookup','mail-gap-profile','due')]
sequence += list(reversed(sequence))
for i,(name,checkout,engine) in enumerate(sequence):
 root=base/checkout
 target=root/'perf/results/dev/gap-benchmark/test_mail_persistence.py'
 target.parent.mkdir(parents=True,exist_ok=True); target.write_text(source)
 out=base/f'gap-bench-{i}-{name}'
 env=dict(os.environ,MAIL_TEST_DB='test_gap_profile_'+('native' if engine=='native' else 'due'),MAIL_BENCH_ENGINE=engine,MAIL_BENCH_BLOCK=f'gap-bench-{i}-{name}',MAIL_BENCH_SAMPLES='20',MAIL_BENCH_REQUIRE_CLEAN='0',MAIL_BENCH_OUTPUT=str(out.with_suffix('.jsonl')),PYTHONUNBUFFERED='1')
 with out.with_suffix('.log').open('w') as log:
  r=subprocess.run([sys.executable,str(base/'run_backend_arm.py'),checkout,'-m','pytest','-c','pytest.ini',str(target),'-q','--reuse-db','-k','50-success'],env=env,stdout=log,stderr=subprocess.STDOUT)
 print(name,r.returncode,flush=True)
 if r.returncode: print(out.with_suffix('.log').read_text()[-3000:]); sys.exit(r.returncode)

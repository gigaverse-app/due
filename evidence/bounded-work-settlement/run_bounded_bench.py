import json
import os
from pathlib import Path
import subprocess
import sys

base = Path(__file__).parent
source = (base / 'bounded_benchmark.py').read_text()
arms = [('native', 'mail-flatten-main', 'native'), ('ordinary', 'mail-gap-retained-both', 'due'), ('bounded', 'mail-bounded-settlement', 'due')]
sequence = arms + list(reversed(arms))
for index, (name, checkout, engine) in enumerate(sequence):
    root = base / checkout
    target = root / 'perf/results/dev/bounded-benchmark/test_bounded_mail.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    output = base / f'{os.environ.get("BENCH_PREFIX", "bounded-bench")}-{index}-{name}'
    env = dict(os.environ, MAIL_TEST_DB='test_bounded_' + engine, MAIL_BENCH_ENGINE=engine,
               MAIL_BENCH_BLOCK=output.name, MAIL_BENCH_SAMPLES=os.environ.get('BOUNDED_SAMPLES', '25'),
               MAIL_BENCH_REQUIRE_CLEAN='1', MAIL_BENCH_OUTPUT=str(output.with_suffix('.jsonl')),
               PYTHONUNBUFFERED='1')
    with output.with_suffix('.log').open('w') as log:
        run = subprocess.run([sys.executable, str(base / 'run_bounded_arm.py'), checkout, '-m', 'pytest', '-c', 'pytest.ini', str(target), '-q', '--reuse-db'], env=env, stdout=log, stderr=subprocess.STDOUT)
    print(name, run.returncode, flush=True)
    if run.returncode:
        print(output.with_suffix('.log').read_text()[-8000:], flush=True)
        sys.exit(run.returncode)

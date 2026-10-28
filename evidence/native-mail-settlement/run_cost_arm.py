import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

base = Path(__file__).parent
root = (base / sys.argv[1]).resolve()
engine, block, database = sys.argv[2:5]
benchmark = subprocess.check_output(['git', '-C', str(base / 'backend'), 'show', '13ea57040:perf/scripts/measure_bulk_mail_persistence.py'], text=True)
target = root / 'perf/results/dev/mail-cost-20261027/test_mail_persistence_measure.py'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(benchmark, encoding='utf-8')
for line in (root / 'deployment/scripts/test_env.dummy').read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        name, value = line.split('=', 1)
        os.environ[name] = value
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
os.environ['FIREBASE_ADMIN_PRIVATE_KEY'] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
os.environ.update(
    DATABASE_URL='postgresql://postgres@127.0.0.1:55449/gigaverse',
    TEST_DATABASE_NAME=database,
    DJANGO_SETTINGS_MODULE='gigaverse.settings',
    PG_DUMP=str(base / 'pg15/pgsql/bin/pg_dump.exe'),
    REDIS_URL='redis://127.0.0.1:6379', CACHE_BACKEND='locmem',
    PYTHONPATH=os.pathsep.join(str(root / path) for path in ('.', 'libraries/due/src', 'libraries/pytest-obligation/src')),
    MAIL_BENCH_ENGINE=engine, MAIL_BENCH_BLOCK=block, MAIL_BENCH_SAMPLES='5',
    MAIL_BENCH_OUTPUT=str(base / f'mail-cost-{block}.jsonl'),
    PYTHONUNBUFFERED='1',
)
os.chdir(root)
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-c', 'pytest.ini', str(target), '-q', '--reuse-db'], env=os.environ))

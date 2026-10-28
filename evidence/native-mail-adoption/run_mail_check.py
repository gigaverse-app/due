import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

base = Path(__file__).parent
root = (base / sys.argv[1]).resolve()
for line in (root / 'deployment/scripts/test_env.dummy').read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        name, value = line.split('=', 1)
        os.environ[name] = value
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
os.environ['FIREBASE_ADMIN_PRIVATE_KEY'] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
os.environ.update(
    DATABASE_URL='postgresql://postgres@127.0.0.1:55449/gigaverse',
    TEST_DATABASE_NAME='test_' + root.name.replace('-', '_'),
    SCHEMA_DUMP_DB='schema_' + root.name.replace('-', '_'),
    DJANGO_SETTINGS_MODULE='gigaverse.settings',
    PG_DUMP=str(base / 'pg15/pgsql/bin/pg_dump.exe'),
    REDIS_URL='redis://127.0.0.1:6379', CACHE_BACKEND='locmem',
    PYTHONPATH=os.pathsep.join(str(root / path) for path in ('.', 'libraries/due/src', 'libraries/pytest-obligation/src')),
    PYTHONUNBUFFERED='1',
)
os.chdir(root)
raise SystemExit(subprocess.call([sys.executable, *sys.argv[2:]], env=os.environ))

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

base = Path(__file__).parent
arms = {
    'native': ('mail-flatten-main', 'native'),
    'shared': ('mail-shared-baseline', 'due'),
    'static': ('mail-static-adoption', 'due'),
    'composed': ('mail-composed-adoption', 'due'),
}
sequence = ['native', 'shared', 'static', 'composed', 'composed', 'static', 'shared', 'native']
manifest = {'start': datetime.now(timezone.utc).isoformat(), 'arms': {}, 'sequence': sequence, 'samples_per_block_scenario': 5, 'warmups_per_block_scenario': 1}
for name, (checkout, engine) in arms.items():
    root = base / checkout
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
    if tracked:
        raise RuntimeError(f'{checkout} has tracked changes: {tracked}')
    manifest['arms'][name] = {'checkout': checkout, 'engine': engine, 'sha': sha}
for index, name in enumerate(sequence):
    checkout, engine = arms[name]
    block = f'adoption-{index}-{name}'
    with (base / f'{block}.log').open('w') as log:
        result = subprocess.run([sys.executable, str(base / 'run_cost_arm.py'), checkout, engine, block, f'test_cost_adoption_{name}'], stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{block} failed; inspect its log')
    print(f'{block}: passed', flush=True)
manifest['end'] = datetime.now(timezone.utc).isoformat()
manifest['driver_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(base / 'adoption-benchmark-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

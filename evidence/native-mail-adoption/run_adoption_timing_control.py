import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

base = Path(__file__).parent
manifest = {'start': datetime.now(timezone.utc).isoformat(), 'database': 'test_cost_adoption_control', 'arms': []}
for index, arm in enumerate(['static', 'composed', 'composed', 'static']):
    checkout = 'mail-static-adoption' if arm == 'static' else 'mail-composed-adoption'
    block = f'adoption-control-{index}-{arm}'
    manifest['arms'].append({'block': block, 'sha': subprocess.check_output(['git', '-C', str(base / checkout), 'rev-parse', 'HEAD'], text=True).strip()})
    with (base / f'{block}.log').open('w') as log:
        subprocess.run([sys.executable, str(base / 'run_cost_arm.py'), checkout, 'due', block, manifest['database']], stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'{block}: passed', flush=True)
manifest['end'] = datetime.now(timezone.utc).isoformat()
(base / 'adoption-timing-control-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

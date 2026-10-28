"""Frozen native / current Due / prepared-progress comparison, serial ABCCBA."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

base = Path(__file__).parent
due = base / 'due-settlement-contract'
mail = base / 'mail-static-capture'
manifest = []
for index, arm in enumerate(['native', 'baseline', 'prepared', 'prepared', 'baseline', 'native']):
    root = base / 'mail-flatten-main' if arm == 'native' else mail
    revision = 'aeb2922' if arm == 'baseline' else 'f022ab6'
    hashes = {}
    if arm != 'native':
        paths = subprocess.check_output(['git', '-C', str(due), 'ls-tree', '-r', '--name-only', revision, 'package/src/due'], text=True).splitlines()
        for path in paths:
            content = subprocess.check_output(['git', '-C', str(due), 'show', f'{revision}:{path}'])
            target = root / 'libraries/due/src/due' / Path(path).relative_to('package/src/due')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            hashes[str(target.relative_to(root))] = hashlib.sha256(content).hexdigest()
        app = root / 'gigaverse/core/mail/reminders/engine/bulk_delivery.py'
        app.write_text(app.read_text().replace('condition=lambda now: ~Q(campaign__status=CampaignStatus.PARKED)', 'condition=~Q(campaign__status=CampaignStatus.PARKED)'))
        subprocess.run(['git', '-C', str(root), 'add', 'libraries/due/src/due', str(app)], check=True)
        subprocess.run(['git', '-C', str(root), 'commit', '--allow-empty', '-m', f'test(perf): measure settlement {index} {arm}'], check=True)
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    block = f'settlement-{index}-{arm}'
    manifest.append({'block': block, 'source': sha, 'due': revision if arm != 'native' else None, 'files': hashes})
    (base / 'settlement-benchmark-manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'START {block} {sha}', flush=True)
    with (base / f'{block}.log').open('w') as log:
        subprocess.run([sys.executable, str(base / 'run_cost_arm.py'), root.name, 'native' if arm == 'native' else 'due', block, 'test_mail_static_capture'], stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'PASS {block}', flush=True)

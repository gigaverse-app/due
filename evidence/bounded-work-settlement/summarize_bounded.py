import json
import statistics
import os
from pathlib import Path
from collections import defaultdict

base = Path(__file__).parent
groups = defaultdict(list)
blocks = defaultdict(list)
prefix = os.environ.get('BENCH_PREFIX', 'bounded-bench')
for path in sorted(base.glob(prefix + '-*.jsonl')):
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row['warmup']:
            continue
        arm = row['block'].split('-')[-1]
        groups[(row['chunks'], arm)].append(row['delivery'])
        blocks[(row['chunks'], row['block'])].append(row['delivery']['elapsed_ms'])
summary = []
for (chunks, arm), values in sorted(groups.items()):
    item = dict(chunks=chunks, arm=arm, n=len(values))
    for key in ('statements', 'writes', 'commits', 'server_transactions', 'elapsed_ms', 'sql_ms', 'commit_ms'):
        item[key] = round(statistics.median(v[key] for v in values), 3)
    for key in ('cpu_ms', 'db_cpu_ms'):
        item[key] = round(statistics.mean(v[key] for v in values), 3)
    item['db'] = {key: round(statistics.mean(v['db'][key] for v in values), 3) for key in values[0]['db']}
    summary.append(item)
print(json.dumps(summary, indent=2))
print('block medians', {str(k): round(statistics.median(v), 3) for k, v in blocks.items()})
(base / (prefix + '-summary.json')).write_text(json.dumps(summary, indent=2) + '\n')

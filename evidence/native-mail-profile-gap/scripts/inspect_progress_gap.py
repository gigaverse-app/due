import json
import re
from collections import Counter
from pathlib import Path

base = Path(__file__).parent
for label in ('main-shortlinks', 'combined-progress'):
    rows = [json.loads(line) for line in (base/f'mail-cost-final-{label}-v.jsonl').read_text().splitlines()]
    row = next(r for r in rows if not r['warmup'] and r['tickets'] == 50 and r['outcome'] == 'success')
    counts = Counter()
    trace = []
    for phase in ('admission', 'delivery'):
        for index, query in enumerate(row[phase]['trace']):
            sql = query['sql']
            table = re.search(r'(?:FROM|UPDATE|INTO) "([^"]+)"', sql)
            name = table.group(1) if table else 'clock' if 'clock_timestamp()' in sql else ''
            counts[sql.split()[0], name] += 1
            trace.append(f'{phase} {index}: {sql}')
    print(label)
    for (kind, table), count in sorted(counts.items()):
        print(f'{count:3} {kind:8} {table}')
    (base/f'progress-trace-{label}.txt').write_text('\n'.join(trace))

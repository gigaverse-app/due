from pathlib import Path
import shutil
import subprocess

base = Path(__file__).parent
source = base / 'due-bounded-settlement'
target = base / 'mail-bounded-settlement'
for name in (
    'capabilities/authoring.py', 'capabilities/bulk.py',
    'runtime/coordination.py', 'runtime/settlement_batch.py',
):
    shutil.copy2(source / 'package/src/due/django' / name, target / 'libraries/due/src/due/django' / name)
path = target / 'gigaverse/core/mail/reminders/engine/bulk_delivery.py'
text = path.read_text()
start = text.index('\n', text.index('    """An inline fast path', text.index('def dispatch_due_chunks'))) + 1
text = text[:start] + '''    # Benchmark-only adoption of the success path; not a shipping migration.
    chunks = pending_bulk_chunks().filter(campaign_id=campaign_id).order_by("seq")

    def eligible_ids():
        for chunk_id in chunks.values_list("pk", flat=True).iterator(chunk_size=100):
            if not EmailCampaign.objects.filter(pk=campaign_id, status=CampaignStatus.RUNNING).exists():
                break
            yield chunk_id

    return sum(delivery.attempt_many(eligible_ids(), settlement_batch_size=8).values())
'''
path.write_text(text)
subprocess.run(['git', 'add', 'libraries/due/src/due/django', 'gigaverse/core/mail/reminders/engine/bulk_delivery.py'], cwd=target, check=True)
if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=target).returncode:
    subprocess.run(['git', 'commit', '-m', 'test(perf): freeze bounded settlement mail benchmark'], cwd=target, check=True)

runner = (base / 'run_backend_arm.py').read_text().replace('127.0.0.1:55449', '127.0.0.1:56449')
(base / 'run_bounded_arm.py').write_text(runner)

source = (base / 'mail-claim-entry/perf/scripts/measure_bulk_mail_persistence.py').read_text()
source = source.replace('import pytest\n', 'import pytest\nimport psutil\n')
source = source.replace('        self.commit_ms = 0.0', '        self.commit_ms = 0.0\n        self.autocommit_statements = 0')
source = source.replace('        started = time.perf_counter()\n        try:\n            return execute', '        self.autocommit_statements += int(connection.get_autocommit() and not connection.in_atomic_block)\n        started = time.perf_counter()\n        try:\n            return execute')
source = source.replace('        started = time.perf_counter()\n        with connection.execute_wrapper', '''        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            cursor.execute("SELECT pg_backend_pid()")
            backend = psutil.Process(cursor.fetchone()[0])
            cursor.execute("SELECT pg_stat_statements_reset()")
        db_before = backend.cpu_times()
        cpu_started = time.process_time()
        started = time.perf_counter()
        with connection.execute_wrapper''')
source = source.replace('        counts = Counter', '''        cpu_ms = (time.process_time() - cpu_started) * 1000
        db_after = backend.cpu_times()
        db_cpu_ms = ((db_after.user + db_after.system) - (db_before.user + db_before.system)) * 1000
        with connection.cursor() as cursor:
            cursor.execute("""SELECT coalesce(sum(total_plan_time), 0), coalesce(sum(total_exec_time), 0),
                coalesce(sum(shared_blks_hit), 0), coalesce(sum(shared_blks_read), 0),
                coalesce(sum(shared_blks_dirtied), 0), coalesce(sum(wal_records), 0), coalesce(sum(wal_bytes), 0),
                coalesce(sum(calls), 0), coalesce(sum(rows), 0)
                FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
                AND query NOT LIKE 'SELECT pg_stat_statements_reset%'""")
            values = cursor.fetchone()
        db = dict(zip(('plan_ms', 'exec_ms', 'buffer_hits', 'buffer_reads', 'buffers_dirtied', 'wal_records', 'wal_bytes', 'calls', 'rows'), map(float, values)))
        counts = Counter''')
source = source.replace('            "elapsed_ms": elapsed,', '''            "elapsed_ms": elapsed,
            "cpu_ms": cpu_ms,
            "db_cpu_ms": db_cpu_ms,
            "server_transactions": self.commits + self.autocommit_statements,
            "db": db,''')
source = source.replace('[(50, "success"), (100, "success"), (200, "success"), (50, "throttle"), (50, "permanent")]', '[(50, "success"), (200, "success"), (400, "success")]')
(base / 'bounded_benchmark.py').write_text(source)

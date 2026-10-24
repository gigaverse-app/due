import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from due.django.testing import hold_nudges

from acme import providers
from acme.models import Job, PrintOrder, Revision
from acme.services import add_revision, approve_print, request_proof


def wait_for(read, expected, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value == expected:
            return value
        time.sleep(0.25)
    raise CommandError(f"Timed out waiting for {expected!r}; last value={value!r}. Start worker AND Beat.")


class Command(BaseCommand):
    help = "Run Willow Cafe's order through the real worker and Beat. Safe to repeat."

    def handle(self, *args, **options):
        job_id = max(418, (Job.objects.aggregate(last=Max("pk"))["last"] or 0) + 1)
        job = Job.objects.create(pk=job_id)
        self.stdout.write(f"Acme Printworks job {job.pk}: Willow Cafe, 500 brochures")
        # Lose the fast notification. Beat must discover the committed task.
        with hold_nudges():
            first = add_revision(job.pk, 17, "Opening day: June 12")
        wait_for(lambda: Revision.objects.get(pk=first.pk).word_count, 4)
        self.stdout.write("1. Estimate: 4 words, recovered after a lost notification")
        request_proof(first.pk)
        wait_for(lambda: bool(Job.objects.get(pk=job.pk).published_key), True)
        newer = add_revision(job.pk, 18, "New opening day: June 19")
        request_proof(newer.pk)
        wait_for(lambda: bool(Job.objects.get(pk=job.pk).published_key), True)
        job.refresh_from_db()
        assert providers.proof_text(job.published_key) == newer.text
        self.stdout.write("2. Proof: revision 18, June 19")
        wait_for(lambda: providers.portal_key(job.pk), job.published_key)
        self.stdout.write("3. Portal: displays revision 18")
        providers.lose_next_print_reply()
        order = approve_print(job.pk, job.published_key, quantity=500)
        wait_for(lambda: PrintOrder.objects.get(pk=order.pk).status, "accepted")
        assert providers.print_count(str(order.reference)) == 1
        self.stdout.write("4. Printer: accepted 500 copies; lost reply recovered; exactly one order")
        with providers.database() as db:
            spool = db.execute("SELECT count(*) FROM renders WHERE spool IS NOT NULL").fetchone()[0]
        assert spool == 0
        self.stdout.write("5. Cleanup: temporary spool removed; immutable proof retained")
        self.stdout.write("6. Run pytest -q for late writes, process death, and replay contracts")

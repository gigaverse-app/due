"""Equivalent immutable-book workflow on Procrastinate's PostgreSQL queue."""

import os
from uuid import uuid4

from django.db import transaction
from procrastinate import JobContext
from procrastinate.contrib.django import app

from examples.books import search
from examples.books.models import Book

QUEUE = os.environ.get("DUE_COMPARISON_QUEUE", "due-comparison")


@app.task(queue=QUEUE, retry=3)
def index_book(book_id: int, key: str, title: str, author: str) -> None:
    # A stable admission identity makes retries safe at the external boundary.
    search.put(search.IndexEntry(key=key, title=title, author=author))
    with transaction.atomic():
        publish_book.configure(lock=f"publish:{key}").defer(book_id=book_id, key=key)


@app.task(queue=QUEUE, retry=3)
def publish_book(book_id: int, key: str) -> None:
    search.publish(key)
    Book.objects.filter(pk=book_id).update(index_key=key, indexing_failed=False)


@transaction.atomic
def create_book(title: str, author: str) -> Book:
    book = Book(title=title, author=author)
    book.full_clean()
    book.save()
    # DjangoConnector uses the SAME Django connection. Rollback removes both
    # the product and queued intent; no on_commit publication gap is introduced.
    key = uuid4().hex
    index_book.configure(lock=f"index:{key}").defer(book_id=book.pk, key=key, title=title, author=author)
    return book


# Procrastinate 3.10's periodic annotation drops the timestamp keyword from
# Task's ParamSpec; the documented runtime callback receives it by keyword.
@app.periodic(cron="* * * * *")  # pyrefly: ignore [bad-argument-type]
@app.task(queue=QUEUE, queueing_lock="comparison-stalled-recovery", pass_context=True)
async def recover_stalled(context: JobContext, timestamp: int) -> None:
    # As documented by Procrastinate, abrupt worker death requires this sweep.
    # Tests shorten the heartbeat threshold only after proving the child exited.
    timeout = float(os.environ.get("DUE_COMPARISON_STALE_SECONDS", "30"))
    for job in await context.app.job_manager.get_stalled_jobs(queue=QUEUE, seconds_since_heartbeat=timeout):
        await context.app.job_manager.retry_job(job)

from django.conf import settings
from django.tasks import task

from .models import Revision


@task(queue_name=settings.CELERY_TASK_DEFAULT_QUEUE)
def count_words(revision_id: int) -> int:
    revision = Revision.objects.get(pk=revision_id)
    count = len(revision.text.split())
    Revision.objects.filter(pk=revision_id).update(word_count=count)
    return count

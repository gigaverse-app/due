from django.tasks import task


@task
def greet(name):
    return f"Hello {name}"

from django.apps import AppConfig


class DueTasksConfig(AppConfig):
    name = "due.django.tasks"
    label = "due_tasks"
    verbose_name = "Due Tasks"
    default_auto_field = "django.db.models.BigAutoField"

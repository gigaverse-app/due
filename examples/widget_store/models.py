from typing import ClassVar

from django.db import models

from due.django.public import UniverseDurableWork, durable_work_schema


class Product(models.Model):
    name = models.CharField(max_length=100, default="Durable widget")
    inventory = models.PositiveIntegerField(default=100)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(inventory__gte=0), name="widget_stock_nonnegative")]


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        DISPATCHED = "dispatched"
        CANCELLED = "cancelled"

    submission_key = models.UUIDField(unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    payment_status = models.CharField(max_length=8, blank=True)
    reserved = models.BooleanField(default=False)
    progress_remaining = models.PositiveSmallIntegerField(default=10)
    expires_at = models.DateTimeField()
    next_stage_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    needs_attention = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(progress_remaining__lte=10), name="widget_progress_bound")
        ]


class CheckoutWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True)
    submission_key = models.UUIDField()

    class Meta:
        indexes, constraints = durable_work_schema("widget_checkout")

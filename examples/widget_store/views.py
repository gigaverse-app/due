import os
import signal
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View

from examples.widget_store import services
from examples.widget_store.models import Order, Product


class StoreView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "widget_store/store.html")


class StateView(View):
    def get(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse(
            {
                "products": list(Product.objects.values("id", "name", "inventory")),
                "orders": list(
                    Order.objects.order_by("-pk").values(
                        "id",
                        "submission_key",
                        "status",
                        "payment_status",
                        "progress_remaining",
                        "needs_attention",
                        "expires_at",
                    )[:100]
                ),
                "crash_enabled": "DUE_WIDGET_SUPERVISOR" in os.environ,
            }
        )


class CommandView(View):
    def post(self, request: HttpRequest, action: str, key: UUID | None = None, decision: str = "") -> JsonResponse:
        try:
            if action == "restock":
                return JsonResponse({"inventory": services.restock().inventory})
            if action == "checkout":
                assert key is not None
                order = services.checkout(key)
            elif action == "payment":
                assert key is not None
                order = services.payment(key, decision)
            elif action == "retry":
                assert key is not None
                return JsonResponse({"retried": services.retry_order(Order.objects.get(submission_key=key).pk)})
            elif action == "crash":
                # Only the opt-in local supervisor sets this value. No request
                # parameter can select a process; ordinary runserver cannot crash.
                if os.environ.get("DUE_WIDGET_SUPERVISOR") != str(os.getppid()):
                    return JsonResponse({"error": "Start the supervised demo to enable crash/restart"}, status=409)
                os.kill(os.getppid(), signal.SIGUSR1)
                return JsonResponse({"restarting": True})
            else:
                raise ValueError("Unknown command")
            return JsonResponse({"order_id": order.pk, "payment_id": str(order.submission_key), "status": order.status})
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)
        except Order.DoesNotExist:
            return JsonResponse({"error": "Order not found"}, status=404)

from django.urls import path

from examples.books.views import BookCreateView, BookListView
from examples.scheduled.views import PublicationListView, PublicationView
from examples.widget_store.views import CommandView, StateView, StoreView

urlpatterns = [
    path("widgets/", StoreView.as_view(), name="widgets"),
    path("widgets/state", StateView.as_view()),
    path("widgets/checkout/<uuid:key>", CommandView.as_view(), {"action": "checkout"}),
    path("widgets/payment_webhook/<uuid:key>/<str:decision>", CommandView.as_view(), {"action": "payment"}),
    path("widgets/retry/<uuid:key>", CommandView.as_view(), {"action": "retry"}),
    path("widgets/restock", CommandView.as_view(), {"action": "restock"}),
    path("widgets/crash_application", CommandView.as_view(), {"action": "crash"}),
    path("scheduled/", PublicationListView.as_view(), name="publications"),
    path("scheduled/<int:pk>/", PublicationView.as_view(), name="publication"),
    path("", BookListView.as_view(), name="books"),
    path("books/new/", BookCreateView.as_view(), name="create_book"),
]

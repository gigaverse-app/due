from django.urls import include, path

from examples.books.views import BookCreateView, BookListView
from examples.scheduled.views import PublicationListView, PublicationView

urlpatterns = [
    path("", include("examples.widget_store.urls")),
    path("scheduled/", PublicationListView.as_view(), name="publications"),
    path("scheduled/<int:pk>/", PublicationView.as_view(), name="publication"),
    path("", BookListView.as_view(), name="books"),
    path("books/new/", BookCreateView.as_view(), name="create_book"),
]

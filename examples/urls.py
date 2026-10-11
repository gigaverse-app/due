from django.urls import path

from examples.books.views import BookCreateView, BookListView

urlpatterns = [
    path("", BookListView.as_view(), name="books"),
    path("books/new/", BookCreateView.as_view(), name="create_book"),
]

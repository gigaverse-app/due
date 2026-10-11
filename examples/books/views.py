from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import CreateView, ListView

from examples.books import search
from examples.books.models import Book
from examples.books.services import create_book


class BookListView(ListView):
    model = Book
    template_name = "books/list.html"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Book]:
        books = Book.objects.order_by("-pk")
        query = self.request.GET.get("q", "").strip()
        return books.filter(index_key__in=search.search(query)) if query else books


class BookCreateView(CreateView):
    model = Book
    fields = ["title", "author"]
    template_name = "books/form.html"

    def form_valid(self, form: ModelForm) -> HttpResponse:
        self.object = create_book(title=form.cleaned_data["title"], author=form.cleaned_data["author"])
        return redirect("books")

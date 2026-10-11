import os
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from examples.scheduled.forms import DeadlineForm, PublicationForm
from examples.scheduled.models import Publication
from examples.scheduled.services import (
    cancel_publication,
    create_publication,
    reschedule_publication,
    retry_publication,
)


class PublicationListView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return render(
            request,
            "scheduled/list.html",
            {"publications": Publication.objects.order_by("-pk"), "form": PublicationForm()},
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        form = PublicationForm(request.POST)
        if form.is_valid():
            publication = create_publication(form.cleaned_data["text"], form.cleaned_data["at"])
            return redirect("publication", pk=publication.pk)
        return render(
            request,
            "scheduled/list.html",
            {"publications": Publication.objects.order_by("-pk"), "form": form},
            status=400,
        )


class PublicationView(View):
    def _render(
        self, request: HttpRequest, pk: int, *, result: str = "", form: DeadlineForm | None = None
    ) -> HttpResponse:
        publication = get_object_or_404(Publication, pk=pk)
        content = ""
        if publication.published_key:
            content = (
                Path(os.environ.get("DUE_OUTPUT_DIR", "/tmp/due-documents")) / publication.published_key
            ).read_text()
        return render(
            request,
            "scheduled/detail.html",
            {
                "publication": publication,
                "content": content,
                "result": result,
                "form": form if form is not None else DeadlineForm(initial={"at": publication.publish_at}),
            },
        )

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        return self._render(request, pk)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        get_object_or_404(Publication, pk=pk)
        action = request.POST.get("action")
        if action == "cancel":
            result = cancel_publication(pk).value
        elif action == "retry":
            result = "retry scheduled" if retry_publication(pk) else "nothing to retry"
        elif action == "reschedule":
            form = DeadlineForm(request.POST)
            if not form.is_valid():
                return self._render(request, pk, form=form)
            result = reschedule_publication(pk, form.cleaned_data["at"]).value
        else:
            return HttpResponse("Unknown action", status=400)
        return self._render(request, pk, result=result)

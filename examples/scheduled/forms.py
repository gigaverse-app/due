from uuid import uuid7

from django import forms


class DeadlineForm(forms.Form):
    at = forms.DateTimeField(
        label="Publish at (UTC)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="A past time publishes on the next worker/recovery pass.",
    )


class PublicationForm(DeadlineForm):
    submission_key = forms.UUIDField(initial=uuid7, widget=forms.HiddenInput)
    text = forms.CharField(widget=forms.Textarea)

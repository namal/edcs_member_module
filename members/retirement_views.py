from django import forms
from .approval_services import form_checkpoint
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Member
from .permissions import district_records, require_permission


class RetirementForm(forms.Form):
    retirement_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "field-input"})
    )

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = member

    def clean_retirement_date(self):
        value = self.cleaned_data["retirement_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(
                "Choose today or an earlier date to process retirement."
            )
        if self.member.date_of_birth and value <= self.member.date_of_birth:
            raise forms.ValidationError("Retirement must be after the date of birth.")
        return value


@login_required
def member_retire(request, pk):
    require_permission(request.user, Member, "change")
    member = get_object_or_404(district_records(request.user, Member), pk=pk)
    form = RetirementForm(
        request.POST if request.method == "POST" else None,
        member=member,
        initial={"retirement_date": timezone.localdate()},
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(
            request,
            form,
            model=Member,
            action="change",
            target=member,
            description=f"Process retirement: {member}",
        )
        if queued is not None:
            return queued
        try:
            with transaction.atomic():
                saved = (
                    district_records(request.user, Member)
                    .select_for_update()
                    .get(pk=member.pk)
                )
                saved.pension_date = form.cleaned_data["retirement_date"]
                saved.status = Member.Status.RETIRED
                saved.save(update_fields=["pension_date", "status", "updated_at"])
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(
                request,
                "Retirement processed. The member's accounts and financial history are retained.",
            )
            return redirect(
                (
                    "member_detail"
                    if request.user.has_perm("members.view_member")
                    else "member_edit"
                ),
                pk=member.pk,
            )
    return render(
        request,
        "members/retire.html",
        {
            "section": "members",
            "title": "Process retirement",
            "member": member,
            "form": form,
        },
    )

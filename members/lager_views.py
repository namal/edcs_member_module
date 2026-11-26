import csv

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .lager_activity import (
    activity_groups,
    activity_query,
    decorate_event,
    lager_summaries,
)
from .models import Lager, Member
from .permissions import district_records, require_permission


class LagerActivityFilterForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        empty_label="All members in this lager",
    )
    category = forms.ChoiceField(required=False, label="Activity type")
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False, label="To date", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, members, groups, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = members if groups else members.none()
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        self.fields["category"].choices = [
            ("", "All available activity"),
            *groups.items(),
        ]
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"

    def clean(self):
        data = super().clean()
        start = data.get("date_from")
        end = data.get("date_to") or timezone.localdate()
        if start and start > end:
            self.add_error("date_to", "To date must be on or after From date.")
        return data


class CSVBuffer:
    def write(self, value):
        return value


def activity_csv(lager, events):
    writer = csv.writer(CSVBuffer())

    def safe(value):
        if value is None:
            return ""
        if isinstance(value, str) and value.lstrip().startswith(
            ("=", "+", "-", "@", "\t", "\r")
        ):
            return "'" + value
        return value

    def rows():
        yield "\ufeff"
        yield writer.writerow(
            [
                "Lager code",
                "Branch",
                "Zonal office",
                "Date",
                "Member no",
                "Member name",
                "Activity",
                "Child",
                "Account type",
                "Reference",
                "Debit (LKR)",
                "Credit (LKR)",
                "Record amount (LKR)",
                "Details",
            ]
        )
        records = (
            events.iterator(chunk_size=1000) if hasattr(events, "iterator") else events
        )
        for event in records:
            event = decorate_event(event)
            values = [
                lager.code,
                lager.district.name,
                lager.zonal_office.name,
                *[
                    event[key]
                    for key in (
                        "event_date",
                        "event_member_no",
                        "event_member_name",
                        "event_label",
                        "event_child",
                        "event_account",
                        "event_reference",
                        "event_debit",
                        "event_credit",
                        "event_amount",
                        "event_details",
                    )
                ],
            ]
            yield writer.writerow([safe(value) for value in values])

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="lager-{lager.pk}-activity.csv"'
    )
    return response


@login_required
@require_GET
def lager_activity(request, pk, export=False):
    require_permission(request.user, Lager, "view")
    lager = get_object_or_404(
        district_records(request.user, Lager).select_related("zonal_office__district"),
        pk=pk,
    )
    members = (
        district_records(request.user, Member)
        .filter(lager=lager)
        .order_by("member_no", "pk")
    )
    groups = activity_groups(request.user)
    form = LagerActivityFilterForm(request.GET, members=members, groups=groups)
    events = []
    summaries = {}
    selected_member = None
    end = timezone.localdate()
    if form.is_valid():
        data = form.cleaned_data
        selected_member = data["member"]
        if selected_member:
            members = members.filter(pk=selected_member.pk)
        end = data["date_to"] or end
        chosen_groups = (
            {data["category"]: groups[data["category"]]} if data["category"] else groups
        )
        events = activity_query(members, chosen_groups, data["date_from"], end)
        if export:
            return activity_csv(lager, events)
        summaries = lager_summaries(request.user, members, data["date_from"], end)
    elif export:
        return HttpResponseBadRequest("Correct the activity filters before exporting.")
    page = Paginator(events, 25).get_page(request.GET.get("page"))
    page.object_list = [decorate_event(row) for row in page.object_list]
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "lagers/activity.html",
        {
            "section": "lagers",
            "title": "Lager activity",
            "lager": lager,
            "form": form,
            "groups": groups,
            "selected_member": selected_member,
            "member_count": members.count(),
            "summaries": summaries,
            "balance_date": end,
            "page_obj": page,
            "query_string": params.urlencode(),
        },
    )

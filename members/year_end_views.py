from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .access_views import superuser_required
from .deposit_interest import current_financial_year, financial_year_start
from .models import FinancialYearClosing
from .views import paginated
from .year_end_services import close_financial_year, closing_preview


class YearEndForm(forms.Form):
    year = forms.IntegerField(
        label="Financial year starting in", min_value=1900, max_value=9998
    )
    review_hash = forms.CharField(required=False, widget=forms.HiddenInput())
    reason = forms.CharField(
        required=False,
        max_length=2000,
        label="Administrator approval reason",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    approve = forms.BooleanField(
        required=False,
        label="I have reviewed and approve this year-end closing and balance carry-forward.",
    )

    def __init__(self, *args, approving=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = approving
        self.fields["approve"].required = approving
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(field.widget, forms.CheckboxInput)
                else "field-input"
            )


@superuser_required
def financial_year_closing_list(request):
    source = request.POST if request.method == "POST" else request.GET.copy()
    if request.method != "POST":
        source.setdefault("year", current_financial_year())
    approving = request.method == "POST" and request.POST.get("intent") == "close"
    form = YearEndForm(source, approving=approving)
    preview = None
    valid = form.is_valid()
    if form.cleaned_data.get("year"):
        try:
            start = financial_year_start(form.cleaned_data["year"])
            preview = closing_preview(start)
            if valid and approving:
                closing = close_financial_year(
                    start,
                    actor=request.user,
                    expected_fingerprint=form.cleaned_data["review_hash"],
                    reason=form.cleaned_data["reason"],
                )
                messages.success(
                    request,
                    f"Year-end closing recorded for {closing.account_count} accounts. Closing balances equal next-year opening balances; no original openings were replaced.",
                )
                return redirect("financial_year_closing_detail", pk=closing.pk)
        except ValidationError as error:
            form.add_error(None, error)
    if preview:
        form.data = form.data.copy()
        form.data["review_hash"] = preview["fingerprint"]
        if preview["can_close"]:
            form.fields["reason"].required = True
            form.fields["approve"].required = True
    return render(
        request,
        "year_end/list.html",
        {
            "title": "Financial year-end closing",
            "section": "financial_year_closing",
            "form": form,
            "preview": preview,
            **paginated(
                request, FinancialYearClosing.objects.select_related("approved_by")
            ),
        },
    )


@superuser_required
def financial_year_closing_detail(request, pk):
    closing = get_object_or_404(
        FinancialYearClosing.objects.select_related("approved_by"), pk=pk
    )
    return render(
        request,
        "year_end/detail.html",
        {
            "title": "Year-end closing approval",
            "section": "financial_year_closing",
            "closing": closing,
        },
    )

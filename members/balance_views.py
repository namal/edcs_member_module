from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .balance_forms import MemberBalanceForm
from .balance_services import member_balance_inquiry
from .models import ChildDeposit, PaymentTransaction
from .permissions import has_module_permission, require_permission
from .transaction_balances import child_account_summary
from .views import paginated


@login_required
@require_GET
def member_balance(request):
    require_permission(request.user, PaymentTransaction, "view")
    params = request.GET.copy()
    params.setdefault("month", timezone.localdate().strftime("%Y-%m"))
    params.setdefault("history", "all")
    form = MemberBalanceForm(
        params if "member" in request.GET else None,
        user=request.user,
        initial={"month": params["month"], "history": params["history"]},
    )
    context = {"title": "Member Balance", "section": "member_balance", "form": form}
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        inquiry = member_balance_inquiry(
            data["member"], data["month"], form.as_of, history=data["history"]
        )
        events = inquiry.pop("events")
        context.update(
            selected_member=data["member"],
            **inquiry,
            **paginated(request, events),
        )
        if has_module_permission(request.user, ChildDeposit, "view"):
            context["child_balance_summary"] = {
                "as_of": form.as_of.isoformat(),
                **child_account_summary(request.user, data["member"], form.as_of),
            }
    return render(request, "transactions/member_balance.html", context)

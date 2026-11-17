from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied
from functools import wraps


class WorkspaceAdminSite(AdminSite):
    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def admin_view(self, view, cacheable=False):
        secured = super().admin_view(view, cacheable=cacheable)

        @wraps(secured)
        def guarded(request, *args, **kwargs):
            model = getattr(getattr(view, "__self__", None), "model", None)
            if (
                request.method == "POST"
                and not (request.user.is_active and request.user.is_superuser)
                and model is not None
                and model._meta.app_label == "members"
            ):
                from .approval_models import ApprovalHierarchy
                from .approval_scope import ALL_MODULES, requires_approval_scope

                aliases = {
                    "openingbalanceamount": "openingbalance",
                    "paymentplanamount": "paymentplan",
                    "paymentplantemplateamount": "paymentplantemplate",
                    "childdeposittransaction": "childdeposit",
                    "childdepositinterestyear": "depositinterestyear",
                    "fixdepositloan": "fixdepositaccount",
                    "fixloanentry": "fixdepositaccount",
                    "fixdepositwithdrawal": "fixdepositaccount",
                }
                module = aliases.get(model._meta.model_name, model._meta.model_name)
                view_action = {"add_view": "add", "delete_view": "delete"}.get(
                    view.__name__, "change"
                )
                if request.POST.get("action") == "delete_selected":
                    view_action = "delete"
                if (
                    requires_approval_scope(module, view_action)
                    and ApprovalHierarchy.objects.filter(
                        module__in=[module, ALL_MODULES],
                        action__in=[view_action, "*"],
                        is_active=True,
                    ).exists()
                ):
                    raise PermissionDenied(
                        "Use the workspace form and Approvals queue for this module. Administrator overrides require a recorded reason."
                    )
            return secured(request, *args, **kwargs)

        return guarded

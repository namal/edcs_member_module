"""Explicit approval scopes, including mandatory below-plan payment exceptions."""

ALL_MODULES = "all_modules"
PAYMENT_EXCEPTIONS = "paymentexception"


def requires_approval_scope(module, action, view_name=""):
    if module == PAYMENT_EXCEPTIONS:
        return action in ("add", "change")
    if module == "membertransfer":
        return action == "add"
    # Recording a repayment creates a new ledger entry, even though its module
    # permission is "change". It must keep the normal immediate posting flow.
    return action == "change" and view_name != "fix_loan_entry"

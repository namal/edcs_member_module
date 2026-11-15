"""Only transfers and updates to existing workspace records need approval."""

ALL_MODULES = "all_modules"


def requires_approval_scope(module, action, view_name=""):
    if module == "membertransfer":
        return action == "add"
    # Recording a repayment creates a new ledger entry, even though its module
    # permission is "change". It must keep the normal immediate posting flow.
    return action == "change" and view_name != "fix_loan_entry"

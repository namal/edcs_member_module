from .permissions import accessible_districts, module_access


def workspace_access(request):
    if not request.user.is_authenticated:
        return {}
    modules = module_access(request.user)
    masters = [
        module
        for module in modules
        if module["section"]
        not in ("members", "payment_plans", "opening_balances", "payment_transactions")
    ]
    return {
        "workspace_modules": modules,
        "has_district_access": accessible_districts(request.user).exists(),
        "master_modules": masters,
        "has_master_access": any(module["visible"] for module in masters),
        "payment_module": next(
            module for module in modules if module["section"] == "payment_plans"
        ),
        "opening_balance_module": next(
            module for module in modules if module["section"] == "opening_balances"
        ),
        "transaction_module": next(
            module for module in modules if module["section"] == "payment_transactions"
        ),
    }

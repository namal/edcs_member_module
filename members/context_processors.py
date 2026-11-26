from .permissions import accessible_districts, assigned_lagers, module_access


def workspace_access(request):
    if not request.user.is_authenticated:
        return {}
    modules = module_access(request.user)
    masters = [
        module
        for module in modules
        if module["section"]
        not in (
            "members",
            "member_transfers",
            "member_ledgers",
            "payment_plans",
            "opening_balances",
            "payment_transactions",
            "children",
            "child_deposits",
            "fix_deposits",
            "deposit_interest",
            "audit_logs",
        )
    ]
    return {
        "workspace_modules": modules,
        "ledger_modules": [
            module
            for module in modules
            if module["section"] in ("member_transfers", "member_ledgers")
        ],
        "has_district_access": accessible_districts(request.user).exists(),
        "has_ledger_access": assigned_lagers(request.user)
        .filter(
            zonal_office__district_id__in=accessible_districts(request.user).values(
                "pk"
            )
        )
        .exists(),
        "uses_member_records": any(
            module["section"]
            in (
                "members",
                "member_ledgers",
                "payment_plans",
                "opening_balances",
                "payment_transactions",
                "children",
                "child_deposits",
                "fix_deposits",
                "deposit_interest",
            )
            and any(module[action] for action in ("view", "add", "change"))
            for module in modules
        ),
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
        "interest_module": next(
            module for module in modules if module["section"] == "deposit_interest"
        ),
        "child_modules": [
            module
            for module in modules
            if module["section"] in ("children", "child_deposits")
        ],
        "fix_deposit_module": next(
            module for module in modules if module["section"] == "fix_deposits"
        ),
    }

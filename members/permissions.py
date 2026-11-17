"""Shared permission checks for the workspace and its navigation."""

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import (
    Child,
    ChildDeposit,
    ChildDepositInterestYear,
    ChildDepositTransaction,
    ChildDepositType,
    District,
    DepositInterestYear,
    FixDepositAccount,
    FixDepositLoan,
    FixDepositPolicy,
    FixDepositWithdrawal,
    FixLoanEntry,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    PaymentPlan,
    PaymentPlanTemplate,
    PaymentTransaction,
    PaymentReceipt,
    PaymentType,
    School,
    Subdivision,
    MemberLedger,
    MemberTransfer,
)

MODULES = (
    (Member, "Members", "members", "member", "users"),
    (
        MemberTransfer,
        "Member Transfers",
        "member_transfers",
        "member_transfer",
        "arrow",
    ),
    (MemberLedger, "Member Ledgers", "member_ledgers", "member_ledger", "wallet"),
    (Child, "Children", "children", "child", "users"),
    (ChildDeposit, "Child Deposits", "child_deposits", "child_deposit", "wallet"),
    (
        ChildDepositType,
        "Child Deposit Types",
        "child_deposit_types",
        "child_deposit_type",
        "wallet",
    ),
    (District, "Districts", "districts", "district", "pin"),
    (OfficeLocation, "Zonal offices", "offices", "office", "building"),
    (Subdivision, "Subdivisions", "subdivisions", "subdivision", "building"),
    (School, "Departments", "schools", "school", "building"),
    (Lager, "Lagers", "lagers", "lager", "building"),
    (PaymentPlan, "Payment Plan", "payment_plans", "payment_plan", "wallet"),
    (
        PaymentPlanTemplate,
        "Payment Plan Templates",
        "payment_plan_templates",
        "payment_plan_template",
        "wallet",
    ),
    (
        DepositInterestYear,
        "Quarterly Interest",
        "deposit_interest",
        "deposit_interest",
        "wallet",
    ),
    (
        PaymentTransaction,
        "Debit / Credit",
        "payment_transactions",
        "payment_transaction",
        "wallet",
    ),
    (
        OpeningBalance,
        "Opening Balances",
        "opening_balances",
        "opening_balance",
        "wallet",
    ),
    (PaymentType, "Payment Types", "payment_types", "payment_type", "wallet"),
    (
        FixDepositAccount,
        "Fix Deposit Loans & Withdrawals",
        "fix_deposits",
        "fix_deposit",
        "wallet",
    ),
    (
        FixDepositPolicy,
        "Fix Deposit Rules",
        "fix_deposit_policies",
        "fix_deposit_policy",
        "wallet",
    ),
)
ACTIONS = ("view", "add", "change", "delete")
MODULE_ACTIONS = {
    MemberTransfer: ("view", "add"),
    MemberLedger: ("view", "add", "change"),
}

DISTRICT_LOOKUPS = {
    Child: "member__district_id",
    ChildDeposit: "child__member__district_id",
    ChildDepositInterestYear: "deposit__child__member__district_id",
    ChildDepositTransaction: "deposit__child__member__district_id",
    FixDepositAccount: "member__district_id",
    FixDepositLoan: "account__member__district_id",
    FixDepositWithdrawal: "account__member__district_id",
    FixLoanEntry: "loan__account__member__district_id",
    District: "pk",
    Member: "district_id",
    OfficeLocation: "district_id",
    Lager: "zonal_office__district_id",
    School: "district_id",
    Subdivision: "zonal_office__district_id",
    PaymentPlan: "member__district_id",
    PaymentTransaction: "member__district_id",
    PaymentReceipt: "member__district_id",
    OpeningBalance: "member__district_id",
    DepositInterestYear: "member__district_id",
}

# Every member-linked model uses the same current ledger ownership boundary.
MEMBER_LOOKUPS = {
    "member": "pk",
    "child": "member_id",
    "childdeposit": "child__member_id",
    "childdeposittransaction": "deposit__child__member_id",
    "childdepositinterestyear": "deposit__child__member_id",
    "paymenttransaction": "member_id",
    "paymentreceipt": "member_id",
    "paymentreceiptallocation": "receipt__member_id",
    "paymentplan": "member_id",
    "paymentplanamount": "plan__member_id",
    "openingbalance": "member_id",
    "openingbalanceamount": "opening_balance__member_id",
    "depositinterestyear": "member_id",
    "fixdepositaccount": "member_id",
    "fixdepositloan": "account__member_id",
    "fixloanentry": "loan__account__member_id",
    "fixdepositwithdrawal": "account__member_id",
    "ledgerrecord": "member_id",
    "ledgerevent": "record__member_id",
}


def assigned_lagers(user):
    if not user or not user.is_authenticated or not user.is_active:
        return Lager.objects.none()
    if user.is_superuser:
        return Lager.objects.all()
    return Lager.objects.filter(
        maintenance_officer=user,
        maintenance_officer__is_active=True,
        maintenance_officer__ledger_profile__epf_number__isnull=False,
    ).exclude(maintenance_officer__ledger_profile__epf_number__regex=r"^\s*$")


def accessible_members(user):
    if not user or not user.is_authenticated or not user.is_active:
        return Member.objects.none()
    if user.is_superuser:
        return Member.objects.all()
    return Member.objects.filter(
        district_id__in=accessible_districts(user).values("pk"),
        lager_id__in=assigned_lagers(user).values("pk"),
    )


def require_member_access(user, member, *, lock=False):
    if lock and user and not user.is_superuser:
        lager_id = (
            Member.objects.filter(pk=getattr(member, "pk", member))
            .values_list("lager_id", flat=True)
            .first()
        )
        Lager.objects.select_for_update().filter(pk=lager_id).first()
    if not accessible_members(user).filter(pk=getattr(member, "pk", member)).exists():
        raise PermissionDenied("This member is outside your assigned ledger access.")


def require_lager_access(user, lager_id):
    if (
        not assigned_lagers(user)
        .filter(
            pk=lager_id,
            zonal_office__district_id__in=accessible_districts(user).values("pk"),
        )
        .exists()
    ):
        raise PermissionDenied("Choose a ledger assigned to your user and EPF number.")


def guard_member_actor(member, *, locking=False):
    from .ledger_tracking import ledger_actor

    actor = ledger_actor.get()
    if actor is None or actor.is_superuser:
        return
    previous = (
        Member.objects.filter(pk=member.pk).values("lager_id").first()
        if member.pk
        else None
    )
    if locking and not getattr(member, "_member_transfer", False):
        list(
            Lager.objects.select_for_update()
            .filter(
                pk__in=[member.lager_id, previous["lager_id"] if previous else None]
            )
            .order_by("pk")
        )
    if previous:
        require_member_access(actor, member.pk)
    if not getattr(member, "_member_transfer", False) and (member.lager_id or locking):
        require_lager_access(actor, member.lager_id)


def district_only_queryset(user, queryset):
    """Location metadata for explicitly authorized receiving-office selections."""
    if user and user.is_authenticated and user.is_active and user.is_superuser:
        return queryset
    lookup = DISTRICT_LOOKUPS.get(queryset.model)
    return (
        queryset.filter(**{f"{lookup}__in": accessible_districts(user).values("pk")})
        if lookup
        else queryset
    )


def accessible_districts(user):
    if not user or not user.is_authenticated or not user.is_active:
        return District.objects.none()
    if user.is_superuser:
        return District.objects.all()
    return District.objects.filter(authorized_users=user)


def scope_queryset(user, queryset):
    if user and user.is_authenticated and user.is_active and user.is_superuser:
        return queryset
    if queryset.model in (MemberLedger, MemberTransfer):
        districts = accessible_districts(user).values("pk")
        lagers = assigned_lagers(user).values("pk")
        members = accessible_members(user).values("pk")

        if queryset.model is MemberLedger:
            return queryset.filter(
                Q(district_id__in=districts) | Q(member__district_id__in=districts)
            ).filter(
                (Q(status="transferred") & Q(lager_id__in=lagers))
                | Q(member_id__in=members)
            )
        return queryset.filter(
            Q(from_ledger__district_id__in=districts)
            | Q(to_ledger__district_id__in=districts)
            | Q(member__district_id__in=districts)
        ).filter(
            Q(from_ledger__lager_id__in=lagers)
            | Q(to_ledger__lager_id__in=lagers)
            | Q(member_id__in=members)
        )
    queryset = district_only_queryset(user, queryset)
    if queryset.model is Lager:
        return queryset.filter(pk__in=assigned_lagers(user).values("pk"))
    member_lookup = (
        MEMBER_LOOKUPS.get(queryset.model._meta.model_name)
        if queryset.model._meta.app_label == "members"
        else None
    )
    if member_lookup:
        return queryset.filter(
            **{f"{member_lookup}__in": accessible_members(user).values("pk")}
        )
    return queryset


def require_district(user, district_id):
    if not accessible_districts(user).filter(pk=district_id).exists():
        raise PermissionDenied("This district is outside your assigned access.")


def district_records(user, model):
    return scope_queryset(user, model.objects.all())


def has_module_permission(user, model, action):
    if action not in MODULE_ACTIONS.get(model, ACTIONS):
        return False
    if model is District and action == "add" and not user.is_superuser:
        return False
    if (
        model in (PaymentType, ChildDepositType, FixDepositPolicy)
        and action != "view"
        and not user.is_superuser
    ):
        return False
    return user.is_active and user.has_perm(permission_name(model, action))


def permission_name(model, action):
    return f"{model._meta.app_label}.{action}_{model._meta.model_name}"


def require_permission(user, model, action):
    if not has_module_permission(user, model, action):
        raise PermissionDenied


def require_location_access(user):
    # Registration needs lookup data, without granting access to master-data pages.
    allowed = (
        (Member, ("view", "add", "change")),
        (MemberTransfer, ("view", "add")),
        (MemberLedger, ("view", "add", "change")),
        (Lager, ("view", "add", "change")),
        (OfficeLocation, ("view", "add", "change")),
        (School, ("view", "add", "change")),
        (Subdivision, ("view", "add", "change")),
    )
    if not any(
        user.has_perm(permission_name(model, action))
        for model, actions in allowed
        for action in actions
    ):
        raise PermissionDenied


def module_access(user):
    modules = []
    for model, label, section, prefix, icon in MODULES:
        access = {
            action: has_module_permission(user, model, action) for action in ACTIONS
        }
        modules.append(
            {
                **access,
                "label": label,
                "section": section,
                "prefix": prefix,
                "icon": icon,
                "url": f"{prefix}_list" if access["view"] else f"{prefix}_create",
                "visible": access["view"] or access["add"],
            }
        )
    return modules

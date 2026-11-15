"""Frozen helpers used only by migration 0022."""

import json
from datetime import datetime
from uuid import uuid4

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

SOURCES = {
    "PaymentTransaction": (
        "member",
        "transaction_date",
        "paymenttransaction",
        "Member debit / credit",
    ),
    "OpeningBalance": ("member", "balance_date", "openingbalance", "Opening balances"),
    "OpeningBalanceAmount": (
        "opening_balance.member",
        "opening_balance.balance_date",
        "openingbalance",
        "Opening amount",
    ),
    "PaymentPlan": ("member", "agreement_date", "paymentplan", "Payment plan"),
    "PaymentPlanAmount": (
        "plan.member",
        "plan.agreement_date",
        "paymentplan",
        "Monthly contribution",
    ),
    "Child": ("member", "created_at", "child", "Child registration"),
    "ChildDeposit": ("child.member", "opened_on", "childdeposit", "Child account"),
    "ChildDepositTransaction": (
        "deposit.child.member",
        "transaction_date",
        "childdeposit",
        "Child debit / credit",
    ),
    "DepositInterestYear": (
        "member",
        "calculated_through",
        "depositinterestyear",
        "Member interest calculation",
    ),
    "ChildDepositInterestYear": (
        "deposit.child.member",
        "calculated_through",
        "depositinterestyear",
        "Child interest calculation",
    ),
    "FixDepositAccount": (
        "member",
        "cycle_started_on",
        "fixdepositaccount",
        "Fix Deposit account",
    ),
    "FixDepositLoan": (
        "account.member",
        "issued_on",
        "fixdepositaccount",
        "Loan issued",
    ),
    "FixLoanEntry": (
        "loan.account.member",
        "entry_date",
        "fixdepositaccount",
        "Loan repayment / assessed interest",
    ),
    "FixDepositWithdrawal": (
        "account.member",
        "requested_on",
        "fixdepositaccount",
        "Withdrawal request / review",
    ),
}


def value(obj, path):
    for name in path.split("."):
        obj = getattr(obj, name)
    return obj


def encode(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Member = apps.get_model("members", "Member")
    Ledger = apps.get_model("members", "MemberLedger")
    Record = apps.get_model("members", "LedgerRecord")
    Event = apps.get_model("members", "LedgerEvent")
    Profile = apps.get_model("members", "LedgerOfficerProfile")
    from django.conf import settings

    User = apps.get_model(settings.AUTH_USER_MODEL)
    Profile.objects.using(alias).bulk_create(
        [
            Profile(user_id=pk)
            for pk in User.objects.using(alias).values_list("pk", flat=True)
        ],
        ignore_conflicts=True,
    )
    ledgers = {}
    for member in (
        Member.objects.using(alias)
        .select_related("district", "office_location", "lager", "school", "subdivision")
        .iterator()
    ):
        ledger = Ledger.objects.using(alias).create(
            member_id=member.pk,
            reference=f"ML-{uuid4().hex[:12].upper()}",
            district_id=member.district_id,
            zonal_office_id=member.office_location_id,
            lager_id=member.lager_id,
            school_id=member.school_id,
            subdivision_id=member.subdivision_id,
            opened_on=member.admit_date,
            legacy_assignment_pending=True,
            registration_snapshot=encode(
                {
                    field.attname: getattr(member, field.attname)
                    for field in Member._meta.concrete_fields
                }
            ),
            location_snapshot={
                "district": member.district.name,
                "zonal_office": member.office_location.name,
                "lager": member.lager.name if member.lager_id else "",
                "school": member.school.name if member.school_id else "",
                "subdivision": member.subdivision.name if member.subdivision_id else "",
            },
        )
        ledgers[member.pk] = ledger.pk
    for model_name, (member_path, date_path, permission, label) in SOURCES.items():
        Model = apps.get_model("members", model_name)
        paths = [member_path.replace(".", "__")]
        for obj in (
            Model.objects.using(alias).select_related(*paths).iterator(chunk_size=500)
        ):
            member_id = value(obj, member_path).pk
            effective = value(obj, date_path)
            if isinstance(effective, datetime):
                effective = (
                    timezone.localtime(effective).date()
                    if timezone.is_aware(effective)
                    else effective.date()
                )
            account = child = ""
            if hasattr(obj, "payment_type_id") and obj.payment_type_id:
                account = obj.payment_type.name
            if model_name.startswith("ChildDeposit"):
                deposit = obj if model_name == "ChildDeposit" else obj.deposit
                account = f"{deposit.account_no} · {deposit.deposit_type.name}"
                child = deposit.child.name
            elif model_name == "Child":
                child = obj.name
            if model_name.startswith("Fix"):
                account_obj = (
                    obj
                    if model_name == "FixDepositAccount"
                    else (
                        obj.loan.account
                        if model_name == "FixLoanEntry"
                        else obj.account
                    )
                )
                account = account_obj.policy.payment_type.name
            record = Record.objects.using(alias).create(
                member_id=member_id,
                original_ledger_id=ledgers[member_id],
                source_model=f"members.{model_name.lower()}",
                source_id=obj.pk,
            )
            snapshot = encode(
                {
                    "data": {
                        field.attname: getattr(obj, field.attname)
                        for field in Model._meta.concrete_fields
                    },
                    "permission": permission,
                    "label": label,
                    "account": account,
                    "child": child,
                    "reference": getattr(obj, "reference", "")
                    or getattr(obj, "account_no", ""),
                }
            )
            Event.objects.using(alias).create(
                record_id=record.pk,
                ledger_id=ledgers[member_id],
                action="created",
                effective_on=effective or timezone.localdate(),
                snapshot=snapshot,
            )


def reverse_backfill(apps, schema_editor):
    if (
        apps.get_model("members", "MemberTransfer")
        .objects.using(schema_editor.connection.alias)
        .exists()
    ):
        raise RuntimeError(
            "Transfer history exists. Ledger tables cannot be rolled back or discarded."
        )

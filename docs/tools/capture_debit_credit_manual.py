#!/usr/bin/env python3
"""Verify Debit/Credit manual examples in a uniquely named disposable database."""

import json
import logging
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-debit-credit-html")
ASSETS = ROOT / "docs/debit_credit_assets"


def main():
    from django.conf import settings

    database = "edcs_debit_doc_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEBUG = True
    import django

    django.setup()
    from django.contrib.auth.models import Permission
    from django.core.serializers.json import DjangoJSONEncoder
    from django.db import transaction
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.approval_scope import PAYMENT_EXCEPTIONS
    from members.child_services import deposit_balance
    from members.test_child_contribution_allocations import (
        ChildContributionAllocationTests as Fixture,
    )
    from members.transaction_balances import member_balances

    logging.getLogger("django.request").setLevel(logging.CRITICAL)
    HTML.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        Fixture.setUpTestData()
        member, operator, admin = Fixture.member, Fixture.operator, Fixture.admin
        today, deposit = Fixture.today, Fixture.deposits[0]
        for user in (operator, *Fixture.approvers):
            user.user_permissions.set(
                Permission.objects.filter(content_type__app_label="members")
            )
        for index, reviewer in enumerate(Fixture.approvers, 1):
            # Financial approvals retain the application's ledger access boundary.
            # These fictional administrative reviewers can inspect both levels
            # without changing the requesting officer's Lager ownership.
            reviewer.is_superuser = True
            reviewer.is_staff = True
            reviewer.save(update_fields=["is_superuser", "is_staff"])
            m.LedgerOfficerProfile.objects.create(
                user=reviewer, epf_number=f"TRAINING-REVIEW-{index}"
            )
        clients = {}
        for name, user in (
            ("operator", operator),
            ("admin", admin),
            ("review1", Fixture.approvers[0]),
            ("review2", Fixture.approvers[1]),
        ):
            clients[name] = Client()
            clients[name].force_login(user)
        captures, checks = [], {}

        def save_response(
            name, response, *, mode="top", height=1100, fill=None, who="operator"
        ):
            assert response.status_code == 200, (name, response.status_code)
            (HTML / f"{name}.html").write_bytes(response.content)
            captures.append([name, name, mode, height, fill or {}])
            summary = (
                response.context.get("balance_summary") if response.context else None
            )
            if summary:
                (HTML / f"api-balances-{name}.json").write_text(
                    json.dumps(summary, cls=DjangoJSONEncoder)
                )
            # Keep each browser page's lookups at the same verified workflow moment.
            accounts = clients[who].get(
                reverse("contribution_child_accounts"), {"member": member.pk}
            )
            if accounts.status_code == 200:
                (HTML / f"api-child-{name}.json").write_bytes(accounts.content)
            return response

        def snap(
            name,
            route,
            *,
            args=None,
            params=None,
            mode="top",
            height=1100,
            fill=None,
            who="operator",
        ):
            response = clients[who].get(reverse(route, args=args), params or {})
            return save_response(
                name, response, mode=mode, height=height, fill=fill, who=who
            )

        def post(route, data, *, args=None, params=None, who="operator"):
            from urllib.parse import urlencode

            url = reverse(route, args=args)
            if params:
                url += "?" + urlencode(params)
            response = clients[who].post(url, data)
            assert response.status_code == 302, (
                route,
                response.status_code,
                (
                    response.context["form"].errors
                    if response.context and "form" in response.context
                    else ""
                ),
            )
            return response

        def receipt_payload(amount, reference, *, partial=False, remaining=False):
            values = {
                "member": member.pk,
                "received_on": today.isoformat(),
                "amount": str(amount),
                "payment_type": "",
                "reference": reference,
                "description": "Fictional training salary contribution",
                "operation_key": str(uuid4()),
                "allocation_context": f"{member.pk}:",
                "remaining-TOTAL_FORMS": "2" if remaining else "0",
                "remaining-INITIAL_FORMS": "0",
                "remaining-MIN_NUM_FORMS": "0",
                "remaining-MAX_NUM_FORMS": "100",
                **{
                    f"allocated_{row.payment_type_id}": str(
                        row.amount - Decimal("100")
                        if partial and row.payment_type.name == "Scholarship"
                        else row.amount
                    )
                    for row in Fixture.plan.amounts.filter(amount__gt=0).select_related(
                        "payment_type"
                    )
                },
                "intent": "preview",
            }
            if remaining:
                values.update(
                    {
                        "remaining-0-payment_type": str(Fixture.accounts[1].pk),
                        "remaining-0-amount": "800",
                        "remaining-1-payment_type": f"child-type:{deposit.deposit_type_id}",
                        "remaining-1-child_deposit": str(deposit.pk),
                        "remaining-1-amount": "500",
                    }
                )
            return values

        opening = m.OpeningBalance.objects.create(
            member=member, balance_date=Fixture.plan.agreement_date
        )
        special, fixed = Fixture.types["Special Deposit"], Fixture.types["Fix Deposit"]
        for typ, amount in ((special, 1000), (fixed, 2000)):
            m.OpeningBalanceAmount.objects.create(
                opening_balance=opening, payment_type=typ, amount=amount
            )
        snap("01-debit-credit-workspace", "payment_transaction_list")
        credit_values = {
            "member": member.pk,
            "payment_type": str(special.pk),
            "transaction_date": (today - timedelta(days=2)).isoformat(),
            "debit": "",
            "credit": "1000",
            "reference": "TRAINING-CREDIT",
            "description": "Training member account credit received",
        }
        snap(
            "02-member-selection-and-saved-balance",
            "payment_transaction_create",
            params={"member": member.pk, "payment_type": special.pk},
            mode="memberBalance",
        )
        snap(
            "03-add-credit-details",
            "payment_transaction_create",
            params={"member": member.pk, "payment_type": special.pk},
            mode="entryDetails",
            fill=credit_values,
        )
        post("payment_transaction_create", credit_values)
        credit = m.PaymentTransaction.objects.get(reference="TRAINING-CREDIT")
        assert member_balances(member, today)["total"] == "4000.00"
        debit_values = {
            **credit_values,
            "transaction_date": (today - timedelta(days=1)).isoformat(),
            "debit": "250",
            "credit": "",
            "reference": "TRAINING-DEBIT",
            "description": "Training authorized member account debit",
        }
        snap(
            "04-add-debit-details",
            "payment_transaction_create",
            params={"member": member.pk, "payment_type": special.pk},
            mode="entryDetails",
            fill=debit_values,
        )
        post("payment_transaction_create", debit_values)
        debit = m.PaymentTransaction.objects.get(reference="TRAINING-DEBIT")
        statement_selection = {"member": member.pk, "payment_type": special.pk}
        response = snap(
            "05-member-account-statement",
            "payment_transaction_list",
            params=statement_selection,
            height=1400,
        )
        assert response.context["final_balance"] == Decimal("1750")
        checks["single_entries_credit_and_debit_reconciled"] = True

        child_selection = {
            "member": member.pk,
            "payment_type": f"child-type:{deposit.deposit_type_id}",
            "child_deposit": deposit.pk,
        }
        child_values = {
            **credit_values,
            **child_selection,
            "transaction_date": today.isoformat(),
            "credit": "200",
            "reference": "TRAINING-CHILD-CREDIT",
            "description": "Training direct child-account credit",
        }
        snap(
            "06-select-child-deposit-account",
            "payment_transaction_create",
            params=child_selection,
            mode="accountSelection",
        )
        post("payment_transaction_create", child_values)
        assert deposit_balance(deposit, today) == Decimal("300")
        assert member_balances(member, today)["total"] == "3750.00"
        snap(
            "07-child-balances-and-transactions",
            "payment_transaction_create",
            params=child_selection,
            mode="childBalances",
        )
        post(
            "payment_transaction_create",
            {
                **child_values,
                "debit": "50",
                "credit": "",
                "reference": "TRAINING-CHILD-DEBIT",
            },
        )
        child_debit = m.ChildDepositTransaction.objects.get(
            reference="TRAINING-CHILD-DEBIT"
        )
        response = snap(
            "08-child-account-statement",
            "payment_transaction_list",
            params=child_selection,
            height=1450,
        )
        assert response.context["final_balance"] == Decimal("250")
        checks["child_entry_reached_child_module_without_member_credit"] = True

        correction_values = {
            "reverses": debit.pk,
            "member": member.pk,
            "payment_type": special.pk,
            "transaction_date": today.isoformat(),
            "debit": "0",
            "credit": "250",
            "reference": "TRAINING-DEBIT-CORRECTION",
            "description": "Training: reverse the incorrect debit while retaining the original record.",
        }
        snap(
            "09-opposite-correction-form",
            "payment_transaction_create",
            params={"reverse": debit.pk},
            mode="entryDetails",
            fill=correction_values,
        )
        post("payment_transaction_create", correction_values)
        assert (
            m.PaymentTransaction.objects.get(
                reference=correction_values["reference"]
            ).reverses_id
            == debit.pk
        )
        response = snap(
            "10-statement-with-linked-correction",
            "payment_transaction_list",
            params=statement_selection,
            height=1450,
        )
        assert response.context["final_balance"] == Decimal("2000")
        assert m.PaymentTransaction.objects.filter(pk=debit.pk).exists()
        post(
            "child_entry_create",
            {
                "reverses": child_debit.pk,
                "transaction_date": today.isoformat(),
                "debit": "0",
                "credit": "50",
                "reference": "TRAINING-CHILD-CORRECTION",
                "description": "Training: reverse the incorrect child debit and retain its original history.",
            },
            args=[deposit.pk],
        )
        assert deposit_balance(deposit, today) == Decimal("300")
        checks["member_and_child_opposite_corrections_retained_originals"] = True
        edited = {
            **credit_values,
            "description": "Training credit reference verified against the source voucher.",
        }
        snap(
            "11-edit-permitted-entry-details",
            "payment_transaction_edit",
            args=[credit.pk],
            mode="entryDetails",
            fill=edited,
        )
        post("payment_transaction_edit", edited, args=[credit.pk])
        assert m.PaymentTransaction.objects.get(pk=credit.pk).member_id == member.pk

        paid = receipt_payload(3000, "TRAINING-SALARY-3000", remaining=True)
        response = clients["operator"].post(reverse("contribution_create"), paid)
        assert (
            response.status_code == 200 and response.context["preview"]
        ), response.context["form"].errors
        preview = response.context["preview"]
        assert preview["is_balanced"] and not preview["requires_approval"]
        for name, mode in (
            ("12-receive-contribution-input", "receiptInput"),
            ("13-standard-plan-allocation", "allocationPreview"),
            ("14-remaining-member-and-child-allocation", "remaining"),
        ):
            save_response(name, response, mode=mode)
        posted = {
            **paid,
            "intent": "post",
            "review_hash": response.context["form"]["review_hash"].value(),
        }
        post("contribution_create", posted)
        receipt = m.PaymentReceipt.objects.get(reference=paid["reference"])
        assert member_balances(member, today)["total"] == "6500.00"
        assert deposit_balance(deposit, today) == Decimal("800")
        before_count = m.PaymentTransaction.objects.count()
        post("contribution_create", posted)
        assert (
            m.PaymentReceipt.objects.count() == 1
            and m.PaymentTransaction.objects.count() == before_count
        )
        snap(
            "15-posted-salary-receipt",
            "contribution_detail",
            args=[receipt.pk],
            height=1500,
        )
        snap(
            "16-child-module-after-receipt",
            "child_deposit_detail",
            args=[deposit.pk],
            height=1400,
        )
        checks["remaining_member_800_child_500_posted_once"] = True

        hierarchy = m.ApprovalHierarchy.objects.create(
            name="Training payment-exception approvals",
            module=PAYMENT_EXCEPTIONS,
            action="add",
        )
        for i, reviewer in enumerate(Fixture.approvers, 1):
            level = m.ApprovalLevel.objects.create(
                hierarchy=hierarchy, position=i, name=f"Review level {i}"
            )
            level.approvers.add(reviewer)
        partial = receipt_payload(1600, "TRAINING-BELOW-PLAN", partial=True)
        response = clients["operator"].post(reverse("contribution_create"), partial)
        assert (
            response.status_code == 200
            and response.context["preview"]["requires_approval"]
        )
        save_response(
            "17-below-plan-allocation-preview", response, mode="allocationPreview"
        )
        post(
            "contribution_create",
            {
                **partial,
                "intent": "post",
                "review_hash": response.context["form"]["review_hash"].value(),
            },
        )
        approval = m.ApprovalRequest.objects.get(module=PAYMENT_EXCEPTIONS)
        assert (
            m.PaymentReceipt.objects.count() == 1
            and member_balances(member, today)["total"] == "6500.00"
        )
        snap(
            "18-payment-exception-approval",
            "approval_request_detail",
            args=[approval.pk],
            who="review1",
            height=1200,
        )
        snap(
            "21-receipt-history-and-pending",
            "contribution_list",
            params={"q": member.member_no},
            height=1400,
        )
        post(
            "approval_request_detail",
            {
                "decision": "approve",
                "reason": "Training: source receipt and below-plan Scholarship amount checked.",
            },
            args=[approval.pk],
            who="review1",
        )
        assert (
            m.PaymentReceipt.objects.count() == 1
            and member_balances(member, today)["total"] == "6500.00"
        )
        snap(
            "19-next-approval-level",
            "approval_request_detail",
            args=[approval.pk],
            who="review2",
            mode="approvalProgress",
        )
        post(
            "approval_request_detail",
            {
                "decision": "approve",
                "reason": "Training: final exception review completed; authorize receipt posting.",
            },
            args=[approval.pk],
            who="review2",
        )
        approval.refresh_from_db()
        assert approval.status == "approved", approval.outcome
        approved_receipt = m.PaymentReceipt.objects.get(reference=partial["reference"])
        snap(
            "20-approved-exception-receipt",
            "contribution_detail",
            args=[approved_receipt.pk],
            height=1400,
        )
        assert member_balances(member, today)["total"] == "8100.00"
        assert deposit_balance(deposit, today) == Decimal("800")
        checks["whole_partial_receipt_waited_for_both_levels"] = True

        response = snap(
            "22-member-balance-report",
            "payment_transaction_report",
            params={"member": member.pk},
            height=1500,
        )
        assert response.context["final_balance"] == Decimal("8100")
        response = snap(
            "23-date-range-and-brought-forward",
            "payment_transaction_list",
            params={
                **statement_selection,
                "date_from": today.isoformat(),
                "date_to": today.isoformat(),
            },
            height=1450,
        )
        assert response.context["brought_forward"] == Decimal("1750")
        assert response.context["final_balance"] == Decimal("3000")
        response = snap(
            "24-invalid-date-selection",
            "payment_transaction_list",
            params={
                **statement_selection,
                "date": today.isoformat(),
                "date_from": (today - timedelta(days=1)).isoformat(),
            },
            mode="statementFilters",
        )
        assert response.context["form"].errors and not response.context.get(
            "selected_member"
        )
        bad = clients["operator"].post(
            reverse("payment_transaction_create"),
            {
                **credit_values,
                "reference": "INVALID-BOTH-SIDES",
                "debit": "10",
                "credit": "10",
            },
        )
        assert bad.status_code == 200 and bad.context["form"].errors
        assert not m.PaymentTransaction.objects.filter(
            reference="INVALID-BOTH-SIDES"
        ).exists()
        assert (
            clients["operator"]
            .post(reverse("payment_transaction_delete", args=[credit.pk]))
            .status_code
            == 403
        )
        allocated = (
            receipt.allocations.filter(transaction__isnull=False).first().transaction
        )
        assert (
            clients["operator"]
            .get(reverse("payment_transaction_edit", args=[allocated.pk]))
            .status_code
            == 403
        )
        # A separate permitted correction is different from changing the
        # immutable allocation. Roll back this extra verification so that the
        # photographed worked balances remain the documented final position.
        allocated_special = receipt.allocations.get(
            payment_type=special, kind="standard"
        ).transaction
        receipt_snapshot = receipt.plan_snapshot.copy()
        with transaction.atomic():
            post(
                "payment_transaction_create",
                {
                    "reverses": allocated_special.pk,
                    "member": member.pk,
                    "payment_type": special.pk,
                    "transaction_date": today.isoformat(),
                    "debit": "500",
                    "credit": "0",
                    "reference": "TRAINING-ALLOCATION-ADJUSTMENT",
                    "description": "Training authorized opposite adjustment; preserve the posted receipt snapshot.",
                },
            )
            assert member_balances(member, today)["total"] == "7600.00"
            receipt.refresh_from_db()
            assert receipt.plan_snapshot == receipt_snapshot
            transaction.set_rollback(True)
        assert member_balances(member, today)["total"] == "8100.00"
        checks["separate_opposite_adjustment_preserves_receipt_snapshot"] = True
        checks["validation_protected_entries_and_date_filter_balances"] = True
        checks["worked_final_balances"] = {
            "member": "8100.00",
            "child": "800.00",
            "special_deposit": "3000.00",
            "selected_day_brought_forward": "1750.00",
        }
        evidence = {
            "prepared_on": today.isoformat(),
            "timezone": "Asia/Colombo",
            "notice": "Actual current-system views with fictional training data in a uniquely named disposable PostgreSQL database. Live accounts, users and policies were not changed.",
            "captures": captures,
            "checks": checks,
            "temporary_database_removed_on_exit": True,
        }
        (ASSETS / "capture-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        print(json.dumps({"screenshots": len(captures), "checks": checks}))
    finally:
        runner.teardown_databases(old)
        teardown_test_environment()


if __name__ == "__main__":
    main()

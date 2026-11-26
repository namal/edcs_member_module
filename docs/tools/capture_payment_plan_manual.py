#!/usr/bin/env python3
"""Capture payment-plan examples in a uniquely named disposable database."""

import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-payment-plan-html")
ASSETS = ROOT / "docs/payment_plan_assets"


def main():
    from django.conf import settings

    database = "edcs_plan_doc_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEBUG = True
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.test_payment_plans import PaymentPlanTests as Fixture
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
        member, other, actor = Fixture.member, Fixture.other, Fixture.user
        admin = get_user_model().objects.create_superuser(
            "plan.manual.manager", first_name="Training", last_name="Manager"
        )
        m.LedgerOfficerProfile.objects.create(user=admin, epf_number="TRAINING-MGR-001")
        clients = {}
        for name, user in (("operator", actor), ("manager", admin)):
            clients[name] = Client()
            clients[name].force_login(user)
        today = timezone.localdate()
        types = {item.name: item for item in m.PaymentType.objects.all()}
        captures, checks = [], {}

        def snap(
            name,
            route,
            *,
            args=None,
            params=None,
            who="operator",
            mode="top",
            height=1050,
            fill=None,
        ):
            response = clients[who].get(reverse(route, args=args), params or {})
            assert response.status_code == 200, (name, response.status_code)
            (HTML / (name + ".html")).write_bytes(response.content)
            captures.append([name, name, mode, height, fill or {}])
            return response

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

        def amounts(values):
            return {
                f"amount_{item.pk}": str(values.get(name, 0))
                for name, item in types.items()
            }

        custom = {
            "member": other.pk,
            "agreement_date": today.isoformat(),
            **amounts({"Special Deposit": 250}),
        }
        snap(
            "03-custom-plan-form",
            "payment_plan_create",
            params={"member": other.pk},
            fill=custom,
            mode="planForm",
        )
        post("payment_plan_create", custom, params={"member": other.pk})
        other_plan = m.PaymentPlan.objects.get(member=other)
        assert other_plan.monthly_total == Decimal("250")
        snap("01-payment-plan-list", "payment_plan_list")
        result = snap(
            "02-select-member", "payment_plan_create", params={"q": member.member_no}
        )
        assert result.context["page_obj"].paginator.count == 1
        checks["selection_excludes_members_with_saved_plans"] = True

        standard = {
            "Special Deposit": 200,
            "Fix Deposit": 500,
            "Share": 300,
            "Pension": 100,
            "Scholarship": 300,
            "Scholar": 200,
            "Funeral": 100,
        }
        template_values = {
            "name": "Training standard monthly plan",
            "description": "Fictional LKR 1,700 monthly agreement; Share is fixed.",
            "is_active": "on",
            **amounts(standard),
            f"fixed_{types['Share'].pk}": "on",
        }
        snap(
            "05-create-template",
            "payment_plan_template_create",
            fill=template_values,
            mode="templateForm",
        )
        post("payment_plan_template_create", template_values)
        template = m.PaymentPlanTemplate.objects.get(name=template_values["name"])
        assert template.monthly_total == Decimal("1700")
        snap("04-template-list", "payment_plan_template_list")
        api = clients["operator"].get(
            reverse("payment_plan_template_options"), {"template": template.pk}
        )
        assert api.status_code == 200
        (HTML / f"api-template-{template.pk}.json").write_bytes(api.content)

        opening = m.OpeningBalance.objects.create(member=member, balance_date=today)
        for name, amount in (("Special Deposit", 1000), ("Fix Deposit", 1500)):
            m.OpeningBalanceAmount.objects.create(
                opening_balance=opening, payment_type=types[name], amount=amount
            )
        before = str(member_balances(member, today)["total"])
        assert Decimal(before) == Decimal("2500")
        plan_values = {
            "member": member.pk,
            "agreement_date": today.isoformat(),
            "apply_template": template.pk,
            **amounts({**standard, "Special Deposit": 400}),
        }
        snap(
            "06-apply-template",
            "payment_plan_create",
            params={"member": member.pk, "template": template.pk},
            fill=plan_values,
            mode="planForm",
        )
        post("payment_plan_create", plan_values, params={"member": member.pk})
        plan = m.PaymentPlan.objects.get(member=member)
        assert plan.monthly_total == Decimal("1900")
        assert plan.amounts.get(payment_type=types["Share"]).is_fixed
        assert template.amounts.get(
            payment_type=types["Special Deposit"]
        ).amount == Decimal("200")
        assert other_plan.monthly_total == Decimal("250")
        snap(
            "07-saved-payment-plan", "payment_plan_detail", args=[plan.pk], height=1450
        )
        assert str(member_balances(member, today)["total"]) == before
        checks["template_copy_is_independent_and_plan_does_not_credit_cash"] = True

        hierarchy = m.ApprovalHierarchy.objects.create(
            name="Training payment-plan edit approval",
            module="paymentplan",
            action="change",
        )
        level = m.ApprovalLevel.objects.create(
            hierarchy=hierarchy, position=1, name="Manager review"
        )
        level.approvers.add(admin)
        edited = {
            "member": member.pk,
            "agreement_date": today.isoformat(),
            **amounts({**standard, "Special Deposit": 500}),
        }
        snap(
            "08-edit-payment-plan",
            "payment_plan_edit",
            args=[plan.pk],
            fill=edited,
            mode="planForm",
        )
        post("payment_plan_edit", edited, args=[plan.pk])
        approval = m.ApprovalRequest.objects.get(module="paymentplan")
        assert m.PaymentPlan.objects.get(pk=plan.pk).monthly_total == Decimal("1900")
        snap(
            "09-plan-change-approval",
            "approval_request_detail",
            args=[approval.pk],
            who="manager",
            height=1050,
        )
        post(
            "approval_request_detail",
            {
                "decision": "approve",
                "reason": "Training: member, revised variable amount and fixed Share checked.",
            },
            args=[approval.pk],
            who="manager",
        )
        approval.refresh_from_db()
        assert approval.status == "approved", approval.outcome
        plan.refresh_from_db()
        assert plan.monthly_total == Decimal("2000")
        snap(
            "10-approved-payment-plan",
            "payment_plan_detail",
            args=[plan.pk],
            height=1450,
        )
        assert str(member_balances(member, today)["total"]) == before
        checks["edit_waited_for_approval_and_did_not_credit_cash"] = True

        changed_template = {
            **template_values,
            **amounts({**standard, "Special Deposit": 900}),
        }
        changed_template.pop("is_active")
        post("payment_plan_template_edit", changed_template, args=[template.pk])
        template.refresh_from_db()
        assert not template.is_active and template.monthly_total == Decimal("2400")
        assert m.PaymentPlan.objects.get(pk=plan.pk).monthly_total == Decimal("2000")
        snap("11-template-changed-independent", "payment_plan_template_list")
        checks["template_edit_and_inactivation_preserved_assigned_plan"] = True

        receipt_values = {
            "member": member.pk,
            "received_on": today.isoformat(),
            "amount": "2000",
            "payment_type": "",
            "reference": "TRAINING-PLAN-RECEIPT",
            "description": "Fictional payment received against the approved monthly plan",
            "operation_key": str(uuid4()),
            "allocation_context": f"{member.pk}:",
            "remaining-TOTAL_FORMS": "0",
            "remaining-INITIAL_FORMS": "0",
            "remaining-MIN_NUM_FORMS": "0",
            "remaining-MAX_NUM_FORMS": "100",
            **{
                f"allocated_{row.payment_type_id}": str(row.amount)
                for row in plan.amounts.filter(amount__gt=0)
            },
            "intent": "preview",
        }
        preview = clients["operator"].post(
            reverse("contribution_create"), receipt_values
        )
        assert (
            preview.status_code == 200 and preview.context["preview"]
        ), preview.context["form"].errors
        assert not preview.context["preview"]["requires_approval"]
        (HTML / "12-receive-contribution-preview.html").write_bytes(preview.content)
        captures.append(
            [
                "12-receive-contribution-preview",
                "12-receive-contribution-preview",
                "contributionPreview",
                1200,
                {},
            ]
        )
        post(
            "contribution_create",
            {
                **receipt_values,
                "intent": "post",
                "review_hash": preview.context["form"]["review_hash"].value(),
            },
        )
        receipt = m.PaymentReceipt.objects.get(member=member)
        assert Decimal(str(member_balances(member, today)["total"])) == Decimal("4500")
        snap(
            "13-posted-contribution-receipt",
            "contribution_detail",
            args=[receipt.pk],
            height=1200,
        )
        snap(
            "14-member-bank-balance",
            "member_balance",
            params={"member": member.pk},
            height=1000,
        )
        snap(
            "15-current-plan-reference",
            "member_balance",
            params={"member": member.pk},
            mode="currentPlan",
        )
        checks["only_posted_receipt_increased_bank_balance"] = {
            "before": before,
            "receipt": "2000.00",
            "after": str(member_balances(member, today)["total"]),
        }
        frozen_receipt = receipt.plan_snapshot.copy()
        # The posted receipt retains its plan snapshot independently of later edits.
        post(
            "payment_plan_edit",
            {**edited, f"amount_{types['Special Deposit'].pk}": "600"},
            args=[plan.pk],
            who="manager",
        )
        receipt.refresh_from_db()
        assert receipt.plan_snapshot == frozen_receipt
        # Restore the worked plan through an authorized save for subsequent captures.
        post("payment_plan_edit", edited, args=[plan.pk], who="manager")
        checks["posted_receipt_snapshot_retained_after_later_plan_change"] = True

        snap(
            "16-delete-plan-confirmation",
            "payment_plan_delete",
            args=[plan.pk],
            mode="deleteDialog",
        )
        post("payment_plan_delete", {}, args=[plan.pk], who="manager")
        assert m.PaymentPlan.objects.filter(pk=plan.pk).exists()
        snap(
            "17-deletion-protected",
            "payment_plan_detail",
            args=[plan.pk],
            who="manager",
            height=1450,
        )
        checks["used_plan_delete_was_protected"] = True
        invalid = clients["operator"].post(
            reverse("payment_plan_edit", args=[plan.pk]),
            {**edited, f"amount_{types['Special Deposit'].pk}": "-1"},
        )
        assert (
            invalid.status_code == 200
            and f"amount_{types['Special Deposit'].pk}"
            in invalid.context["form"].errors
        )
        (HTML / "18-invalid-amount.html").write_bytes(invalid.content)
        captures.append(
            ["18-invalid-amount", "18-invalid-amount", "agreement", 1050, {}]
        )
        fixed_invalid = clients["operator"].post(
            reverse("payment_plan_edit", args=[plan.pk]),
            {**edited, f"amount_{types['Share'].pk}": "301"},
        )
        assert (
            fixed_invalid.status_code == 200
            and f"amount_{types['Share'].pk}" in fixed_invalid.context["form"].errors
        )
        assert m.PaymentPlan.objects.get(pk=plan.pk).monthly_total == Decimal("2000")
        checks["negative_and_changed_fixed_amounts_rejected"] = True
        evidence = {
            "prepared_on": today.isoformat(),
            "timezone": "Asia/Colombo",
            "notice": "Actual current views with fictional records in a uniquely named disposable PostgreSQL database. Live records and settings were not changed.",
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

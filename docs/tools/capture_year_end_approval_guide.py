#!/usr/bin/env python3
"""Capture real Django responses using fictional records in a disposable test DB.

Never posts to the application's normal database. Only the uniquely named
edcs_guide_... database is migrated, populated and removed by DiscoverRunner.
HTML snapshots are later photographed by Chrome; application templates stay intact.
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-year-end-guide-html")
ASSETS = ROOT / "docs/year_end_approval_assets"


def main():
    import django
    from django.conf import settings

    database = "edcs_guide_" + uuid4().hex[:16]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    django.setup()

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.approval_services import decision_rights
    from members.child_interest import save_child_interest_year
    from members.deposit_interest import (
        add_months,
        current_financial_year,
        financial_year_start,
        save_interest_year,
    )

    HTML.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            "guide.admin", first_name="Training", last_name="Administrator"
        )
        operator = user_model.objects.create_user(
            "guide.officer", first_name="Training", last_name="Account Officer"
        )
        checker = user_model.objects.create_user(
            "guide.finance", first_name="Finance", last_name="Reviewer"
        )
        director = user_model.objects.create_user(
            "guide.director", first_name="Final", last_name="Authorizer"
        )
        for user in (operator, checker, director):
            user.user_permissions.set(
                Permission.objects.filter(
                    content_type__app_label="members",
                    codename__in=[
                        "view_depositinterestyear",
                        "add_depositinterestyear",
                        "change_depositinterestyear",
                        "view_paymenttransaction",
                        "view_childdeposit",
                        "view_member",
                        "view_memberledger",
                    ],
                )
            )
        branch = m.District.objects.get(code="COL")
        office = m.OfficeLocation.objects.create(
            district=branch, name="Training Zonal Office", code="GUIDE-Z"
        )
        school = m.School.objects.create(
            district=branch,
            zonal_office=office,
            name="Training Department",
            code="GUIDE-D",
        )
        for index, user in enumerate((operator, checker, director), 1):
            user.assigned_districts.add(branch)
            profile = m.LedgerOfficerProfile.objects.create(
                user=user, epf_number=f"GUIDE-{index:03}"
            )
            profile.zonal_offices.add(office)
        lager = m.Lager.objects.create(
            zonal_office=office,
            name="Training Member Ledger",
            code="GUIDE-L",
            maintenance_officer=operator,
        )
        year = current_financial_year() - 1
        start = financial_year_start(year)
        member = m.Member.objects.create(
            district=branch,
            office_location=office,
            school=school,
            lager=lager,
            member_no="GUIDE-001",
            computer_no="GUIDE-C001",
            full_name="Training Member Silva",
            title="Mr.",
            date_of_birth=date(1985, 5, 12),
            salary="50000",
            office_address="Training office",
            private_address="Training address",
            phone_mobile="0771234567",
            gender="male",
            nic_no="198512345678",
            admit_date=start,
        )
        types = []
        for name, opening, rate in (
            ("Special Deposit", "10000", "8"),
            ("Fix Deposit", "15000", "4"),
        ):
            typ = m.PaymentType.objects.get(name=name)
            typ.interest_rate = Decimal(rate)
            typ.save()
            types.append(typ)
        opening = m.OpeningBalance.objects.create(member=member, balance_date=start)
        for typ, amount in zip(types, ("10000", "15000")):
            m.OpeningBalanceAmount.objects.create(
                opening_balance=opening, payment_type=typ, amount=Decimal(amount)
            )
        m.PaymentTransaction.objects.create(
            member=member,
            payment_type=types[0],
            transaction_date=add_months(start, 1) + timedelta(days=9),
            credit=Decimal("1200"),
            reference="TRAINING-CREDIT",
        )
        m.PaymentTransaction.objects.create(
            member=member,
            payment_type=types[0],
            transaction_date=add_months(start, 6) + timedelta(days=9),
            debit=Decimal("200"),
            reference="TRAINING-DEBIT",
        )
        child = m.Child.objects.create(
            member=member, name="Training Child Silva", date_of_birth=date(2015, 4, 12)
        )
        child_type = m.ChildDepositType.objects.create(name="Training Child Savings")
        deposit = m.ChildDeposit.objects.create(
            child=child,
            deposit_type=child_type,
            opened_on=start,
            opening_amount=Decimal("5000"),
            account_no="CD-GUIDE-001",
        )
        m.ChildDepositTransaction.objects.create(
            deposit=deposit,
            transaction_date=add_months(start, 2) + timedelta(days=9),
            credit=Decimal("600"),
            interest_rate=Decimal("8"),
            reference="TRAINING-CHILD",
        )

        clients = {}
        for name, user in (
            ("admin", admin),
            ("operator", operator),
            ("checker", checker),
        ):
            clients[name] = Client()
            clients[name].force_login(user)

        pages = []

        def snapshot(name, client, route, **params):
            response = client.get(route, params)
            assert response.status_code == 200, (name, response.status_code)
            (HTML / f"{name}.html").write_bytes(response.content)
            pages.append({"name": name, "route": route, "parameters": params})
            return response

        snapshot(
            "01-user-permissions",
            clients["admin"],
            reverse("access_user_edit", args=[operator.pk]),
        )
        policy_data = {
            "name": "Quarterly Interest - Account year-end review",
            "module": "depositinterestyear",
            "action": "change",
            "is_active": "on",
            "reason": "Training example: configure two ordered account year-end review levels.",
            "levels-TOTAL_FORMS": "2",
            "levels-INITIAL_FORMS": "0",
            "levels-MIN_NUM_FORMS": "0",
            "levels-MAX_NUM_FORMS": "20",
            "levels-0-position": "1",
            "levels-0-name": "Finance review",
            "levels-0-approvers": str(checker.pk),
            "levels-0-allow_rejection": "on",
            "levels-1-position": "2",
            "levels-1-name": "Final authorization",
            "levels-1-approvers": str(director.pk),
            "levels-1-allow_rejection": "on",
        }
        response = clients["admin"].post(
            reverse("approval_hierarchy_create"), policy_data
        )
        assert response.status_code == 302
        policy = m.ApprovalHierarchy.objects.get(module="depositinterestyear")
        snapshot(
            "02-hierarchy-configuration",
            clients["admin"],
            reverse("approval_hierarchy_edit", args=[policy.pk]),
        )
        snapshot(
            "03-hierarchy-list", clients["admin"], reverse("approval_hierarchy_list")
        )
        record = save_interest_year(member, types[0], start)
        child_record = save_child_interest_year(deposit, start)
        snapshot(
            "04-quarterly-interest",
            clients["operator"],
            reverse("deposit_interest_list"),
            year=year,
            member=member.pk,
        )
        review = snapshot(
            "05-member-year-end-review",
            clients["operator"],
            reverse("deposit_interest_edit", args=[record.pk]),
        )
        response = clients["operator"].post(
            reverse("deposit_interest_edit", args=[record.pk]),
            {"calculation_hash": review.context["calculation_hash"]},
        )
        assert response.status_code == 302
        request = m.ApprovalRequest.objects.get()
        assert request.module == "depositinterestyear" and request.status == "pending"
        record.refresh_from_db()
        assert record.posted_at is None
        snapshot(
            "06-account-approval-pending",
            clients["admin"],
            reverse("approval_request_detail", args=[request.pk]),
        )
        rights = decision_rights(checker, request)
        snapshot(
            "07-ordinary-approver-access",
            clients["checker"],
            reverse("approval_request_list"),
        )
        assert not rights["approve"]
        response = clients["admin"].post(
            reverse("approval_request_detail", args=[request.pk]),
            {
                "decision": "approve",
                "reason": "Training: administrator reviewed the first level; no live records involved.",
            },
        )
        assert response.status_code == 302
        snapshot(
            "08-next-approval-level",
            clients["admin"],
            reverse("approval_request_detail", args=[request.pk]),
        )
        response = clients["admin"].post(
            reverse("approval_request_detail", args=[request.pk]),
            {
                "decision": "override_approve",
                "reason": "Training: administrator completes the remaining level after checking reconciliations.",
            },
        )
        assert response.status_code == 302
        request.refresh_from_db()
        assert request.status == "approved", request.outcome
        snapshot(
            "09-account-approval-complete",
            clients["admin"],
            reverse("approval_request_detail", args=[request.pk]),
        )
        snapshot(
            "10-member-posted",
            clients["operator"],
            reverse("deposit_interest_detail", args=[record.pk]),
        )
        child_review = snapshot(
            "11-child-year-end-review",
            clients["operator"],
            reverse("child_interest_edit", args=[child_record.pk]),
        )
        response = clients["operator"].post(
            reverse("child_interest_edit", args=[child_record.pk]),
            {"calculation_hash": child_review.context["calculation_hash"]},
        )
        assert response.status_code == 302
        child_request = m.ApprovalRequest.objects.exclude(pk=request.pk).get()
        assert child_request.module == "depositinterestyear"
        snapshot(
            "12-child-approval-pending",
            clients["admin"],
            reverse("approval_request_detail", args=[child_request.pk]),
        )
        response = clients["admin"].post(
            reverse("approval_request_detail", args=[child_request.pk]),
            {
                "decision": "override_approve",
                "reason": "Training: administrator verifies and approves the child account year-end posting.",
            },
        )
        assert response.status_code == 302
        preview = snapshot(
            "13-batch-year-end-review",
            clients["admin"],
            reverse("financial_year_closing_list"),
            year=year,
        )
        response = clients["admin"].post(
            reverse("financial_year_closing_list"),
            {
                "year": str(year),
                "intent": "close",
                "review_hash": preview.context["form"]["review_hash"].value(),
                "reason": "Training only: reconciled every member and child account and approved equal closing and next-year opening balances.",
                "approve": "on",
            },
        )
        assert response.status_code == 302
        closing = m.FinancialYearClosing.objects.get()
        snapshot(
            "14-approved-batch-history",
            clients["admin"],
            reverse("financial_year_closing_detail", args=[closing.pk]),
        )
        snapshot(
            "15-current-year-preview",
            clients["admin"],
            reverse("financial_year_closing_list"),
            year=current_financial_year(),
        )
        snapshot("16-audit-logs", clients["admin"], reverse("audit_log_list"), q="year")
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        matching = [
            line
            for line in cron.stdout.splitlines()
            if "# member-module-quarterly-interest" in line
            and not line.lstrip().startswith("#")
        ]
        evidence = {
            "prepared_on": "2026-11-26",
            "timezone": "Asia/Colombo",
            "captured_at": timezone.now().isoformat(),
            "data_notice": "Current application views with fictional training records in a disposable PostgreSQL database. Live records and policies were not changed.",
            "financial_year_start_month": settings.FINANCIAL_YEAR_START_MONTH,
            "example_year": year,
            "example_start": start.isoformat(),
            "example_end": closing.year_end.isoformat(),
            "ordinary_designated_approver_can_approve": rights["approve"],
            "reason": "The requester is the current single ledger maintenance officer; a different ordinary designated approver lacks ownership of that ledger.",
            "scheduler_inspected": cron.returncode in (0, 1),
            "scheduler_entry_found": bool(matching),
            "scheduled_post_ended": any("--post-ended" in line for line in matching),
            "individual_request_module": request.module,
            "child_request_module": child_request.module,
            "closing_account_count": closing.account_count,
            "closing_newly_posted_count": closing.newly_posted_count,
            "accounts": closing.accounts,
            "total_closing": str(closing.total_closing),
            "total_interest": str(closing.total_interest),
            "pages": pages,
            "temporary_test_database_removed_on_exit": True,
        }
        (ASSETS / "capture-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "captured_pages": len(pages),
                    "example_year": year,
                    "accounts": closing.account_count,
                    "ordinary_approver_allowed": rights["approve"],
                    "scheduler_post_ended": evidence["scheduled_post_ended"],
                }
            )
        )
    finally:
        runner.teardown_databases(old)
        teardown_test_environment()


if __name__ == "__main__":
    main()

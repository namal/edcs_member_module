#!/usr/bin/env python3
"""Exercise member workflows only in a unique, disposable documentation database."""

import csv
import io
import json
import logging
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-member-directory-html")
ASSETS = ROOT / "docs/member_directory_assets"


def main():
    import django
    from django.conf import settings

    database = "edcs_member_doc_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    django.setup()
    logging.getLogger("django.request").setLevel(logging.CRITICAL)

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.child_services import deposit_balance
    from members.transaction_balances import member_balances

    HTML.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        admin = get_user_model().objects.create_superuser(
            "directory.admin", first_name="Training", last_name="Administrator"
        )
        source_user = get_user_model().objects.create_user(
            "directory.origin", first_name="Origin", last_name="Ledger Officer"
        )
        receiver = get_user_model().objects.create_user(
            "directory.receiving", first_name="Receiving", last_name="Ledger Officer"
        )
        backup = get_user_model().objects.create_user(
            "directory.backup", first_name="Alternate", last_name="Responsible Officer"
        )
        for person in (source_user, receiver, backup):
            person.user_permissions.set(
                Permission.objects.filter(content_type__app_label="members")
            )
        branches = [m.District.objects.get(code=code) for code in ("COL", "GAM")]
        offices, schools, lagers, subdivisions = [], [], [], []
        for index, (branch, officer) in enumerate(
            zip(branches, (source_user, receiver)), 1
        ):
            office = m.OfficeLocation.objects.create(
                district=branch,
                name=f"Training {'Origin' if index == 1 else 'Receiving'} Office",
                code=f"DIR-Z{index}",
            )
            subdivision = m.Subdivision.objects.create(
                zonal_office=office,
                name=f"Training Subdivision {index}",
                code=f"DIR-S{index}",
            )
            school = m.School.objects.create(
                district=branch,
                zonal_office=office,
                subdivision=subdivision,
                name=f"Training Department {index}",
                code=f"DIR-D{index}",
            )
            profile = m.LedgerOfficerProfile.objects.create(
                user=officer, epf_number=f"DIR-EPF-{index:03}"
            )
            officer.assigned_districts.add(branch)
            profile.zonal_offices.add(office)
            lager = m.Lager.objects.create(
                zonal_office=office,
                name=f"Training Lager {index}",
                code=f"DIR-L{index}",
                maintenance_officer=officer,
            )
            offices.append(office)
            schools.append(school)
            lagers.append(lager)
            subdivisions.append(subdivision)
        source_user.assigned_districts.add(branches[1])
        source_user.ledger_profile.zonal_offices.add(offices[1])
        backup.assigned_districts.add(branches[1])
        m.LedgerOfficerProfile.objects.create(
            user=backup, epf_number="DIR-EPF-003"
        ).zonal_offices.add(offices[1])
        clients = {}
        for key, person in (
            ("admin", admin),
            ("origin", source_user),
            ("receiving", receiver),
        ):
            clients[key] = Client()
            clients[key].force_login(person)
        captures, field_catalog, checks = [], {}, {}

        def snap(
            name,
            route,
            *,
            params=None,
            args=None,
            client=None,
            fill=None,
            mode="top",
            height=1100,
        ):
            response = (client or clients["admin"]).get(
                reverse(route, args=args), params or {}
            )
            assert response.status_code == 200, (name, response.status_code)
            (HTML / f"{name}.html").write_bytes(response.content)
            captures.append([name, name, mode, height, fill or {}])
            if (
                response.context
                and "form" in response.context
                and name
                in (
                    "04-register-location",
                    "12-edit-member",
                    "23-transfer-form",
                    "29-process-retirement",
                )
            ):
                field_catalog[name] = [
                    {
                        "name": key,
                        "required": field.required,
                        "disabled": field.disabled,
                        "label": str(field.label or key),
                    }
                    for key, field in response.context["form"].fields.items()
                ]
            return response

        def post(route, data, *, args=None, params=None, client=None):
            url = reverse(route, args=args)
            if params:
                from urllib.parse import urlencode

                url += "?" + urlencode(params)
            response = (client or clients["admin"]).post(url, data)
            if response.status_code != 302:
                errors = (
                    response.context["form"].errors.as_json()
                    if response.context and "form" in response.context
                    else str(response.status_code)
                )
                raise AssertionError((route, errors))
            return response

        today = timezone.localdate()
        opening_day = date(today.year, 1, 1)
        member_data = {
            "school": str(schools[0].pk),
            "district": str(branches[0].pk),
            "office_location": str(offices[0].pk),
            "lager": str(lagers[0].pk),
            "subdivision": str(subdivisions[0].pk),
            "maintenance_officer": str(source_user.pk),
            "member_no": "DIR-001",
            "computer_no": "DIR-C001",
            "title": "Mr.",
            "full_name": "Training Directory Member Silva",
            "date_of_birth": "1985-05-12",
            "nic_no": "198512345678",
            "gender": "male",
            "admit_date": opening_day.isoformat(),
            "salary": "50000.00",
            "status": "active",
            "pension_date": "",
            "phone_mobile": "0771234567",
            "phone_office": "0112345678",
            "phone_home": "",
            "office_address": "Training member office address",
            "private_address": "Training private address",
        }
        for name, mode in (
            ("04-register-location", "memberLocation"),
            ("05-register-identity", "memberIdentity"),
            ("06-register-contacts-save", "memberContacts"),
        ):
            snap(name, "member_create", fill=member_data, mode=mode)
        result = post("member_create", {**member_data, "next_step": "payment_plan"})
        member = m.Member.objects.get(member_no="DIR-001")
        assert result.url == reverse("payment_plan_create") + f"?member={member.pk}"
        original = member.ledgers.get(status="active")
        assert original.maintenance_officer_id == source_user.pk
        checks["registration_created_original_ledger"] = original.reference
        for index in range(2, 13):
            post(
                "member_create",
                {
                    **member_data,
                    "member_no": f"DIR-{index:03}",
                    "computer_no": f"DIR-C{index:03}",
                    "full_name": f"Training Member {index:02}",
                    "nic_no": f"1990123456{index:02}",
                    "next_step": "member",
                },
            )
        snap("01-directory", "member_list", params={"sort": "name"})
        filtered = snap(
            "02-search-and-filters",
            "member_list",
            params={
                "q": "DIR-001",
                "district": branches[0].pk,
                "office": offices[0].pk,
                "lager": lagers[0].pk,
                "status": "active",
                "gender": "male",
                "from": opening_day.isoformat(),
                "to": today.isoformat(),
                "sort": "name",
            },
        )
        assert filtered.context["page_obj"].paginator.count == 1
        page2 = snap(
            "03-directory-page-two", "member_list", params={"page": "2", "sort": "name"}
        )
        assert (
            page2.context["page_obj"].number == 2
            and page2.context["page_obj"].paginator.count == 12
        )
        exported = clients["admin"].get(reverse("member_export"), {"q": "DIR-001"})
        export_rows = list(
            csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))
        )
        assert len(export_rows) == 2 and export_rows[1][0] == "DIR-001"
        checks["filtered_csv_rows_excluding_header"] = 1
        snap(
            "07-new-member-profile-actions",
            "member_detail",
            args=[member.pk],
            height=860,
        )

        types = {
            name: m.PaymentType.objects.get(name=name)
            for name in ("Special Deposit", "Fix Deposit", "Pension", "Funeral")
        }
        plan_values = {
            "member": str(member.pk),
            "agreement_date": opening_day.isoformat(),
            **{
                f"amount_{types[name].pk}": str(amount)
                for name, amount in (
                    ("Special Deposit", 500),
                    ("Fix Deposit", 200),
                    ("Pension", 200),
                    ("Funeral", 100),
                )
            },
        }
        snap(
            "08-set-payment-plan",
            "payment_plan_create",
            params={"member": member.pk},
            fill=plan_values,
            height=1050,
        )
        post("payment_plan_create", plan_values, params={"member": member.pk})
        plan = m.PaymentPlan.objects.get(member=member)
        opening_values = {
            "member": str(member.pk),
            "balance_date": opening_day.isoformat(),
            f"amount_{types['Special Deposit'].pk}": "10000",
            f"amount_{types['Fix Deposit'].pk}": "15000",
        }
        snap(
            "09-set-opening-balances",
            "opening_balance_create",
            params={"member": member.pk},
            fill=opening_values,
            height=1000,
        )
        post("opening_balance_create", opening_values, params={"member": member.pk})
        child_values = {
            "member": str(member.pk),
            "name": "Training Child Silva",
            "date_of_birth": "2015-04-12",
            "is_active": "on",
        }
        snap(
            "19-add-child",
            "child_create",
            params={"member": member.pk},
            fill=child_values,
        )
        post("child_create", child_values)
        child = m.Child.objects.get(member=member)
        child_type = m.ChildDepositType.objects.first()
        child_account_values = {
            "child": str(child.pk),
            "deposit_type": str(child_type.pk),
            "opened_on": opening_day.isoformat(),
            "opening_amount": "5000",
            "is_active": "on",
        }
        post("child_deposit_create", child_account_values)
        deposit = m.ChildDeposit.objects.get(child=child)
        receipt_values = {
            "member": str(member.pk),
            "received_on": today.isoformat(),
            "amount": "1500",
            "payment_type": "",
            "reference": "DIR-TRAINING-RECEIPT",
            "description": "Training monthly contribution and child allocation",
            "operation_key": str(uuid4()),
            "allocation_context": f"{member.pk}:",
            **{
                f"allocated_{types[name].pk}": str(amount)
                for name, amount in (
                    ("Special Deposit", 500),
                    ("Fix Deposit", 200),
                    ("Pension", 200),
                    ("Funeral", 100),
                )
            },
            "remaining-TOTAL_FORMS": "1",
            "remaining-INITIAL_FORMS": "0",
            "remaining-MIN_NUM_FORMS": "0",
            "remaining-MAX_NUM_FORMS": "100",
            "remaining-0-payment_type": f"child-type:{child_type.pk}",
            "remaining-0-child_deposit": str(deposit.pk),
            "remaining-0-amount": "500",
            "intent": "preview",
        }
        preview = clients["admin"].post(reverse("contribution_create"), receipt_values)
        assert preview.status_code == 200 and not preview.context["form"].errors
        (HTML / "16-receive-contribution.html").write_bytes(preview.content)
        captures.extend(
            [
                ["16-receive-contribution", "16-receive-contribution", "top", 1050, {}],
                [
                    "16-receive-contribution",
                    "16b-child-remaining-allocation",
                    "remaining",
                    1000,
                    {},
                ],
            ]
        )
        post(
            "contribution_create",
            {
                **receipt_values,
                "review_hash": preview.context["form"]["review_hash"].value(),
                "intent": "post",
            },
        )
        receipt = m.PaymentReceipt.objects.get(member=member)
        snap(
            "17-contribution-receipt",
            "contribution_detail",
            args=[receipt.pk],
            height=1050,
        )
        debit_values = {
            "member": str(member.pk),
            "payment_type": str(types["Special Deposit"].pk),
            "transaction_date": today.isoformat(),
            "debit": "50",
            "credit": "",
            "reference": "DIR-TRAINING-DEBIT",
            "description": "Training member account debit",
        }
        snap(
            "18-member-debit-credit",
            "payment_transaction_create",
            params={"member": member.pk, "payment_type": types["Special Deposit"].pk},
            fill=debit_values,
            mode="entryDetails",
        )
        post("payment_transaction_create", debit_values)
        snap(
            "18b-child-account-entry",
            "payment_transaction_create",
            params={
                "member": member.pk,
                "payment_type": f"child-type:{child_type.pk}",
                "child_deposit": deposit.pk,
            },
            mode="childPanel",
        )
        post(
            "payment_transaction_create",
            {
                **debit_values,
                "payment_type": f"child-type:{child_type.pk}",
                "child_deposit": str(deposit.pk),
                "debit": "100",
                "reference": "DIR-CHILD-DEBIT",
            },
        )
        snap(
            "20-child-account-statement",
            "child_deposit_detail",
            args=[deposit.pk],
            height=1100,
        )
        snap(
            "15-current-balance",
            "member_balance",
            params={"member": member.pk},
            height=1150,
        )
        snap(
            "15b-member-balance-report",
            "payment_transaction_report",
            params={"member": member.pk},
            height=1100,
        )
        snap(
            "18c-account-statement",
            "payment_transaction_list",
            params={"member": member.pk, "payment_type": types["Special Deposit"].pk},
            height=1150,
        )
        snap(
            "21-quarterly-interest",
            "deposit_interest_list",
            params={"member": member.pk, "year": today.year},
            height=1100,
        )
        policy = m.FixDepositPolicy.objects.filter(
            payment_type=types["Fix Deposit"]
        ).first()
        if policy is None:
            policy = m.FixDepositPolicy.objects.create(
                name="Training fixed-deposit rule",
                payment_type=types["Fix Deposit"],
                loan_interest_rate=Decimal("12"),
            )
        post(
            "fix_deposit_create",
            {
                "member": str(member.pk),
                "policy": str(policy.pk),
                "cycle_started_on": opening_day.isoformat(),
                "is_active": "on",
                "notes": "Training account; no loan or withdrawal issued.",
            },
        )
        fixed_account = m.FixDepositAccount.objects.get(member=member)
        snap(
            "22-fixed-deposit-options",
            "fix_deposit_detail",
            args=[fixed_account.pk],
            height=1100,
        )
        snap(
            "10-member-profile-actions", "member_detail", args=[member.pk], height=1050
        )
        snap(
            "11-member-profile-details",
            "member_detail",
            args=[member.pk],
            mode="profileDetails",
        )
        snap(
            "34-print-profile",
            "member_detail",
            args=[member.pk],
            mode="printProfile",
            height=1200,
        )
        snap(
            "33-lager-activity",
            "lager_activity",
            args=[lagers[0].pk],
            params={"member": member.pk},
            height=1100,
        )

        def hierarchy(module, action, name):
            policy = m.ApprovalHierarchy.objects.create(
                module=module, action=action, name=name
            )
            level = m.ApprovalLevel.objects.create(
                hierarchy=policy,
                position=1,
                name="Administrator review",
                allow_rejection=True,
            )
            level.approvers.add(admin)
            return policy

        hierarchy("member", "change", "Training member edits")
        edited = {
            **member_data,
            "phone_mobile": "0777654321",
            "private_address": "Updated training private address",
        }
        snap(
            "12-edit-member",
            "member_edit",
            args=[member.pk],
            client=clients["origin"],
            fill=edited,
            mode="memberContacts",
        )
        post("member_edit", edited, args=[member.pk], client=clients["origin"])
        edit_request = m.ApprovalRequest.objects.get(module="member")
        member.refresh_from_db()
        assert member.phone_mobile == member_data["phone_mobile"]
        snap(
            "13-member-edit-approval",
            "approval_request_detail",
            args=[edit_request.pk],
            height=1050,
        )
        post(
            "approval_request_detail",
            {
                "decision": "override_approve",
                "reason": "Training: verified contact correction against the source record.",
            },
            args=[edit_request.pk],
        )
        edit_request.refresh_from_db()
        member.refresh_from_db()
        assert (
            edit_request.status == "approved"
            and member.phone_mobile == edited["phone_mobile"]
        )
        snap(
            "14-member-audit", "member_audit", params={"member": member.pk}, height=1100
        )
        duplicate = clients["admin"].post(reverse("member_create"), member_data)
        assert duplicate.status_code == 200 and duplicate.context["form"].errors
        (HTML / "35-duplicate-validation.html").write_bytes(duplicate.content)
        captures.append(
            [
                "35-duplicate-validation",
                "35-duplicate-validation",
                "memberIdentity",
                1000,
                {},
            ]
        )
        checks["computer_number_required"] = field_catalog["04-register-location"][
            next(
                index
                for index, item in enumerate(field_catalog["04-register-location"])
                if item["name"] == "computer_no"
            )
        ]["required"]

        hierarchy("membertransfer", "add", "Training member transfers")
        transfer_values = {
            "member": str(member.pk),
            "source_ledger": str(original.pk),
            "district": str(branches[1].pk),
            "zonal_office": str(offices[1].pk),
            "lager": str(lagers[1].pk),
            "subdivision": str(subdivisions[1].pk),
            "school": str(schools[1].pk),
            "maintenance_officer": str(receiver.pk),
            "effective_date": today.isoformat(),
            "reason": "Training: member moves to the receiving office; preserve accounts and original ledger.",
        }
        snap(
            "23-transfer-form",
            "member_transfer_create",
            params={"member": member.pk},
            client=clients["origin"],
            fill=transfer_values,
            mode="transferReceiving",
        )
        snap(
            "23b-transfer-review-and-submit",
            "member_transfer_create",
            params={"member": member.pk},
            client=clients["origin"],
            fill=transfer_values,
            mode="transferSubmit",
        )
        before = {
            "member_balance": member_balances(member, today)["total"],
            "child_balance": str(deposit_balance(deposit, today)),
            "member_entries": m.PaymentTransaction.objects.filter(
                member=member
            ).count(),
            "child_entries": m.ChildDepositTransaction.objects.filter(
                deposit=deposit
            ).count(),
            "plan_id": plan.pk,
            "child_account_id": deposit.pk,
            "member_id": member.pk,
        }
        post("member_transfer_create", transfer_values, client=clients["origin"])
        transfer_request = m.ApprovalRequest.objects.get(module="membertransfer")
        assert not m.MemberTransfer.objects.exists()
        snap(
            "24-transfer-approval",
            "approval_request_detail",
            args=[transfer_request.pk],
            height=1100,
        )
        post(
            "approval_request_detail",
            {
                "decision": "override_approve",
                "reason": "Training: receiving officer, date and carried amounts checked.",
            },
            args=[transfer_request.pk],
        )
        transfer_request.refresh_from_db()
        assert transfer_request.status == "approved", transfer_request.outcome
        transfer = m.MemberTransfer.objects.get(member=member)
        member.refresh_from_db()
        original.refresh_from_db()
        receiving = transfer.to_ledger
        after = {
            "member_balance": member_balances(member, today)["total"],
            "child_balance": str(deposit_balance(deposit, today)),
            "member_entries": m.PaymentTransaction.objects.filter(
                member=member
            ).count(),
            "child_entries": m.ChildDepositTransaction.objects.filter(
                deposit=deposit
            ).count(),
            "plan_id": m.PaymentPlan.objects.get(member=member).pk,
            "child_account_id": m.ChildDeposit.objects.get(child=child).pk,
            "member_id": member.pk,
        }
        assert before == after
        assert (
            original.status == "transferred"
            and receiving.status == "active"
            and receiving.previous_ledger_id == original.pk
        )
        assert (
            clients["origin"]
            .get(reverse("member_detail", args=[member.pk]))
            .status_code
            == 404
        )
        assert (
            clients["receiving"]
            .get(reverse("member_detail", args=[member.pk]))
            .status_code
            == 200
        )
        checks["transfer_before"] = before
        checks["transfer_after"] = after
        checks["original_officer_current_profile_status_after_transfer"] = 404
        checks["receiving_officer_current_profile_status_after_transfer"] = 200
        snap("25-completed-transfer", "member_transfer_detail", args=[transfer.pk])
        snap(
            "26-original-ledger-retained",
            "member_ledger_detail",
            args=[original.pk],
            height=1150,
        )
        snap(
            "27-receiving-ledger",
            "member_ledger_detail",
            args=[receiving.pk],
            params={"include_previous": "on"},
            height=1150,
        )
        snap(
            "27b-carried-balances",
            "member_ledger_detail",
            args=[receiving.pk],
            mode="ledgerBalances",
        )
        ledger_csv = clients["admin"].get(
            reverse("member_ledger_export", args=[receiving.pk]),
            {"include_previous": "on"},
        )
        assert ledger_csv.status_code == 200 and b"Ledger" in ledger_csv.content
        snap(
            "28-change-ledger-officer",
            "member_ledger_edit",
            args=[receiving.pk],
            fill={
                "maintenance_officer": str(backup.pk),
                "reason": "Training responsibility change on the active ledger; current Lager ownership remains separately configured.",
            },
        )
        post(
            "member_ledger_edit",
            {
                "maintenance_officer": str(backup.pk),
                "reason": "Training responsibility change, preserving previous EPF history.",
            },
            args=[receiving.pk],
        )
        receiving.refresh_from_db()
        lagers[1].refresh_from_db()
        assert (
            receiving.maintenance_officer_id == backup.pk
            and lagers[1].maintenance_officer_id == receiver.pk
        )
        checks["page_officer_change_preserves_lager_owner"] = True
        snap(
            "29-process-retirement",
            "member_retire",
            args=[member.pk],
            fill={"retirement_date": today.isoformat()},
        )
        post("member_retire", {"retirement_date": today.isoformat()}, args=[member.pk])
        member.refresh_from_db()
        assert member.status == "retired" and member.pension_date == today
        assert member_balances(member, today)["total"] == before["member_balance"]
        snap(
            "30-retired-member-profile", "member_detail", args=[member.pk], height=1050
        )
        snap("31-delete-confirmation", "member_delete", args=[member.pk])
        post("member_delete", {}, args=[member.pk])
        assert m.Member.objects.filter(pk=member.pk).exists()
        snap(
            "32-member-delete-protected",
            "member_list",
            params={"q": "DIR-001"},
            height=900,
        )
        checks["delete_protected_member_retained"] = True

        for branch in branches:
            for route_name, file_name in (
                ("location_options", "api-locations"),
                ("office_options", "api-offices"),
            ):
                response = clients["admin"].get(
                    reverse(route_name),
                    (
                        {"district": branch.pk, "purpose": "transfer"}
                        if route_name == "location_options"
                        else {"district": branch.pk}
                    ),
                )
                assert response.status_code == 200
                (HTML / f"{file_name}-{branch.pk}.json").write_bytes(response.content)
        for url, filename, params in (
            (
                reverse("payment_transaction_balances"),
                "api-balances.json",
                {
                    "member": member.pk,
                    "date": today.isoformat(),
                    "child_deposit": deposit.pk,
                },
            ),
            (
                reverse("contribution_child_accounts"),
                "api-child-accounts.json",
                {"member": member.pk},
            ),
        ):
            response = clients["admin"].get(url, params)
            assert response.status_code == 200
            (HTML / filename).write_bytes(response.content)
        metadata = {
            "prepared_on": "2026-11-26",
            "timezone": "Asia/Colombo",
            "notice": "Current application screens with fictional training data, all writes confined to a unique disposable test database.",
            "captures": captures,
            "fields": field_catalog,
            "checks": checks,
            "example_member": "DIR-001",
            "example_member_balance": before["member_balance"],
            "example_child_balance": before["child_balance"],
            "filtered_csv_header": export_rows[0],
            "directory_count": 12,
            "template_monthly_total": str(plan.monthly_total),
            "total_receipt": str(receipt.amount),
            "temporary_database_removed_on_exit": True,
        }
        (ASSETS / "capture-evidence.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "screens": len(captures),
                    "registration": "verified",
                    "edit_approval": edit_request.status,
                    "transfer_approval": transfer_request.status,
                    "preserved_balances": before,
                    "retirement": member.status,
                    "delete_protected": True,
                }
            )
        )
    finally:
        runner.teardown_databases(old)
        teardown_test_environment()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify ledger examples and render current views in a disposable database."""

import csv
import io
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-member-ledgers-html")
ASSETS = ROOT / "docs/member_ledgers_assets"


def main():
    from django.conf import settings

    database = "edcs_ledgers_doc_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEBUG = True
    import django

    django.setup()
    from django.contrib.auth.models import Permission
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.fix_deposit_services import issue_loan
    from members.ledger_tracking import ledger_actor
    from members.status_services import submit_status_request
    from members.test_member_transfers import MemberTransferTests as Fixture
    from members.transfer_services import capture_balances

    HTML.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        Fixture.setUpTestData()
        member, original, admin = Fixture.member, Fixture.original, Fixture.actor
        source, receiver, backup = Fixture.officers
        today = timezone.localdate()
        for user in Fixture.officers:
            user.user_permissions.set(
                Permission.objects.filter(content_type__app_label="members")
            )
        # Allow the source to request a move to the second branch/office.
        source.assigned_districts.add(Fixture.offices[1].district)
        source.ledger_profile.zonal_offices.add(Fixture.offices[1])
        backup.assigned_districts.add(Fixture.offices[1].district)
        backup.ledger_profile.zonal_offices.add(Fixture.offices[1])
        for index, school in enumerate(Fixture.schools):
            school.location = ["Colombo", "Gampaha", "Kandy"][index]
            school.save()
        clients = {}
        for name, user in (
            ("admin", admin),
            ("source", source),
            ("receiver", receiver),
        ):
            clients[name] = Client()
            clients[name].force_login(user)
        captures, checks = [], {}

        def snap(
            name,
            route,
            *,
            args=None,
            params=None,
            who="admin",
            mode="top",
            height=1000,
            fill=None,
        ):
            response = clients[who].get(reverse(route, args=args), params or {})
            assert response.status_code == 200, (name, response.status_code)
            (HTML / (name + ".html")).write_bytes(response.content)
            captures.append([name, name, mode, height, fill or {}])
            return response

        def post(route, data, *, args=None, who="admin"):
            response = clients[who].post(reverse(route, args=args), data)
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

        def policy(module, action, name):
            hierarchy = m.ApprovalHierarchy.objects.create(
                module=module, action=action, name=name
            )
            level = m.ApprovalLevel.objects.create(
                hierarchy=hierarchy, position=1, name="Manager review"
            )
            level.approvers.add(admin)
            return hierarchy

        # Separate deposits, child savings and a loan in the balances example.
        actor_token = ledger_actor.set(admin)
        try:
            fixed = m.PaymentType.objects.get(name="Fix Deposit")
            m.OpeningBalanceAmount.objects.create(
                opening_balance=m.OpeningBalance.objects.get(member=member),
                payment_type=fixed,
                amount=Decimal("15000"),
            )
            rule = m.FixDepositPolicy.objects.filter(payment_type=fixed).first()
            if rule is None:
                rule = m.FixDepositPolicy.objects.create(
                    name="Training fixed deposit rule", payment_type=fixed
                )
            rule.loan_interest_rate = Decimal("8")
            rule.save()
            fixed_account = m.FixDepositAccount.objects.create(
                member=member, policy=rule, cycle_started_on=date(2020, 1, 1)
            )
            loan = issue_loan(
                admin,
                fixed_account.pk,
                today,
                Decimal("1000"),
                "Fictional ledger manual example",
            )
        finally:
            ledger_actor.reset(actor_token)
        baseline = capture_balances(member, today)
        assert (
            next(
                row
                for row in baseline["member_deposits"]
                if row["payment_type_id"] == Fixture.payment_type.pk
            )["final_balance"]
            == "1200.00"
        )
        assert baseline["child_deposits"][0]["balance"] == "120.00"
        assert baseline["loans"][0]["principal_due"] == "1000.00"
        checks["unpaid_plan_not_added_to_balance"] = True

        legacy_lager = m.Lager.objects.create(
            zonal_office=Fixture.offices[0], name="Training legacy Lager", code="LEG-L1"
        )
        legacy = m.Member.objects.create(
            district=Fixture.offices[0].district,
            office_location=Fixture.offices[0],
            school=Fixture.schools[0],
            lager=legacy_lager,
            member_no="LEGACY-002",
            full_name="Training Legacy Member",
            nic_no="199012345679",
            gender="female",
            office_address="Training office",
            private_address="Training home",
            phone_mobile="0771234567",
            admit_date=date(2020, 1, 1),
        )
        legacy_ledger = legacy.ledgers.get()
        assert legacy_ledger.legacy_assignment_pending
        snap("01-ledger-list", "member_ledger_list")
        snap(
            "02-filter-member",
            "member_ledger_list",
            params={
                "member": member.pk,
                "district": member.district_id,
                "status": "active",
            },
        )
        snap("03-active-ledger", "member_ledger_detail", args=[original.pk], height=900)
        snap(
            "04-registration-snapshot",
            "member_ledger_detail",
            args=[original.pk],
            mode="registration",
        )
        snap(
            "05-current-balances",
            "member_ledger_detail",
            args=[original.pk],
            mode="currentBalances",
        )
        snap(
            "06-activity-record-details",
            "member_ledger_detail",
            args=[original.pk],
            mode="recordDetails",
        )
        snap(
            "07-activity-filters-export",
            "member_ledger_detail",
            args=[original.pk],
            params={"date_from": "2025-02-01", "date_to": "2025-02-01"},
            mode="ledgerActivity",
            height=950,
        )
        export = clients["admin"].get(
            reverse("member_ledger_export", args=[original.pk]),
            {"date_from": "2025-02-01", "date_to": "2025-02-01"},
        )
        rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
        assert rows and all(row["Effective date"] == "2025-02-01" for row in rows)
        (ASSETS / "training-ledger-filtered-export.csv").write_bytes(export.content)
        checks["filtered_csv_rows"] = len(rows)

        snap(
            "08-officer-assignment-required",
            "member_ledger_list",
            params={"status": "pending"},
        )
        assignment = {
            "member": legacy.pk,
            "maintenance_officer": source.pk,
            "reason": "Training: assign responsibility for the existing original ledger.",
        }
        snap(
            "09-assign-original-officer",
            "member_ledger_create",
            params={"member": legacy.pk},
            fill=assignment,
        )
        post("member_ledger_create", assignment)
        legacy_ledger.refresh_from_db()
        assert (
            legacy_ledger.pk == legacy.ledgers.get().pk
            and not legacy_ledger.legacy_assignment_pending
        )
        checks["initial_assignment_kept_existing_ledger"] = True

        # Membership state changes use approvals and retain the same ledger.
        policy("memberstatusrequest", "change", "Training membership approval")
        for kind, reason in (
            ("inactivate", "administrative"),
            ("reactivate", "administrative_review"),
        ):
            request = submit_status_request(
                source,
                member.pk,
                kind,
                reason,
                "Training: documented membership status review for the ledger manual.",
                today,
            )
            post(
                "approval_request_detail",
                {
                    "decision": "approve",
                    "reason": "Training: identity, reason and requested date checked.",
                },
                args=[request.approval_id],
            )
            request.refresh_from_db()
            assert request.state == "applied", request.outcome
        member.refresh_from_db()
        assert member.status == "active" and member.ledgers.count() == 1
        snap(
            "10-membership-status-history",
            "member_ledger_detail",
            args=[original.pk],
            mode="statusHistory",
        )
        checks["reactivation_kept_same_ledger"] = True

        # A submitted transfer remains pending until its designated reviewer acts.
        policy("membertransfer", "add", "Training transfer approval")
        values = {
            "member": member.pk,
            "source_ledger": original.pk,
            "district": Fixture.offices[1].district_id,
            "zonal_office": Fixture.offices[1].pk,
            "lager": Fixture.lagers[1].pk,
            "school": Fixture.schools[1].pk,
            "subdivision": "",
            "maintenance_officer": receiver.pk,
            "effective_date": today.isoformat(),
            "reason": "Training workplace transfer; retain original history and carry linked balances.",
        }
        snap(
            "11-transfer-receiving-details",
            "member_transfer_create",
            params={"member": member.pk},
            who="source",
            mode="transferReceiving",
            fill=values,
        )
        post("member_transfer_create", values, who="source")
        approval = m.ApprovalRequest.objects.get(module="membertransfer")
        assert not m.MemberTransfer.objects.exists()
        snap(
            "12-transfer-awaiting-approval",
            "approval_request_detail",
            args=[approval.pk],
            height=1050,
        )
        before = capture_balances(member, today)
        counts = (
            m.Member.objects.count(),
            m.PaymentTransaction.objects.count(),
            m.ChildDepositTransaction.objects.count(),
        )
        post(
            "approval_request_detail",
            {
                "decision": "approve",
                "reason": "Training: receiving officer, date and carried balances verified.",
            },
            args=[approval.pk],
        )
        approval.refresh_from_db()
        assert approval.status == "approved", approval.outcome
        transfer = m.MemberTransfer.objects.get(member=member)
        original.refresh_from_db()
        receiving = transfer.to_ledger
        member.refresh_from_db()
        after = capture_balances(member, today)
        for category in ("member_deposits", "child_deposits", "loans"):
            assert before[category] == after[category], category
        assert counts == (
            m.Member.objects.count(),
            m.PaymentTransaction.objects.count(),
            m.ChildDepositTransaction.objects.count(),
        )
        assert original.closing_balances == receiving.opening_balances
        assert receiving.previous_ledger_id == original.pk
        checks["transfer_preserved_balances_accounts_and_transaction_counts"] = True
        checks["original_closing_equals_receiving_opening"] = True
        snap(
            "13-transferred-original-ledger",
            "member_ledger_detail",
            args=[original.pk],
            height=1050,
        )
        snap(
            "14-active-receiving-ledger",
            "member_ledger_detail",
            args=[receiving.pk],
            height=1000,
        )
        snap(
            "15-carried-opening-balances",
            "member_ledger_detail",
            args=[receiving.pk],
            mode="openingBalances",
        )

        # New postings belong to the receiving page. Original closing stays frozen.
        token = ledger_actor.set(receiver)
        try:
            new_entry = m.PaymentTransaction.objects.create(
                member=member,
                payment_type=Fixture.payment_type,
                transaction_date=today,
                credit=Decimal("50"),
                reference="AFTER-TRANSFER",
                description="Training received contribution after transfer",
            )
        finally:
            ledger_actor.reset(token)
        assert (
            m.LedgerRecord.objects.get(
                source_model="members.paymenttransaction", source_id=new_entry.pk
            ).original_ledger_id
            == receiving.pk
        )
        old_closing = original.closing_balances
        original.refresh_from_db()
        assert original.closing_balances == old_closing
        checks["post_transfer_entry_attached_to_receiving_page"] = True
        query = {
            "include_previous": "on",
            "date_from": "2025-01-01",
            "date_to": today.isoformat(),
        }
        result = snap(
            "16-include-linked-ledger-activity",
            "member_ledger_detail",
            args=[receiving.pk],
            params=query,
            mode="ledgerActivity",
            height=1100,
        )
        assert result.context["page_obj"].paginator.count > 0
        exported = clients["admin"].get(
            reverse("member_ledger_export", args=[receiving.pk]), query
        )
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert {row["Ledger"] for row in rows} == {
            original.reference,
            receiving.reference,
        }
        (ASSETS / "training-linked-ledgers-export.csv").write_bytes(exported.content)
        checks["csv_contains_original_and_receiving_references"] = True

        change = {
            "maintenance_officer": backup.pk,
            "reason": "Training responsibility handover; retain previous officer and EPF.",
        }
        snap(
            "17-change-officer",
            "member_ledger_edit",
            args=[receiving.pk],
            fill=change,
            mode="officerForm",
        )
        post("member_ledger_edit", change, args=[receiving.pk])
        receiving.refresh_from_db()
        Fixture.lagers[1].refresh_from_db()
        assert receiving.maintenance_officer_id == backup.pk
        assert Fixture.lagers[1].maintenance_officer_id == receiver.pk
        snap(
            "18-officer-responsibility-history",
            "member_ledger_detail",
            args=[receiving.pk],
            mode="officerHistory",
        )
        snap(
            "19-ledger-list-epf-comparison",
            "member_ledger_list",
            params={"member": member.pk},
        )
        checks["page_officer_change_kept_lager_master_owner"] = True
        invalid = {"date_from": today.isoformat(), "date_to": "2025-01-01"}
        result = snap(
            "20-invalid-activity-dates",
            "member_ledger_detail",
            args=[receiving.pk],
            params=invalid,
            mode="ledgerFilters",
        )
        assert result.context["form"].errors
        assert (
            clients["admin"]
            .get(reverse("member_ledger_export", args=[receiving.pk]), invalid)
            .status_code
            == 400
        )
        assert (
            clients["admin"]
            .post(reverse("member_ledger_delete", args=[original.pk]))
            .status_code
            == 403
        )
        assert (
            clients["admin"]
            .get(reverse("member_ledger_edit", args=[original.pk]))
            .status_code
            == 403
        )
        assert (
            clients["source"]
            .get(reverse("member_detail", args=[member.pk]))
            .status_code
            == 404
        )
        assert (
            clients["receiver"]
            .get(reverse("member_detail", args=[member.pk]))
            .status_code
            == 200
        )
        checks["historical_page_protected_and_current_access_follows_lager"] = True

        # Supply actual API responses when the browser photographs filled forms.
        for office in Fixture.offices:
            for route, name in (
                ("location_options", "api-locations"),
                ("office_options", "api-offices"),
            ):
                response = clients["source"].get(
                    reverse(route),
                    {"district": office.district_id, "purpose": "transfer"},
                )
                if response.status_code == 200:
                    (HTML / f"{name}-{office.district_id}.json").write_bytes(
                        response.content
                    )
        evidence = {
            "prepared_on": today.isoformat(),
            "timezone": "Asia/Colombo",
            "notice": "Current Django views with fictional training records in a uniquely named disposable PostgreSQL database. Live records and settings were not changed.",
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

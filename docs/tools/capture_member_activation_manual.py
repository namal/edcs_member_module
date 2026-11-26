#!/usr/bin/env python3
"""Capture actual membership views and verify training workflows in an isolated DB."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-member-activation-html")
ASSETS = ROOT / "docs/member_activation_assets"


def main():
    from django.conf import settings

    database = "edcs_activation_doc_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEBUG = True
    import django

    django.setup()
    from django.contrib.auth.models import Permission
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.urls import reverse
    from django.utils import timezone
    from members import models as m
    from members.test_membership_status import MembershipStatusTests as Fixture
    from members.approval_services import decide, decision_rights
    from members.status_services import submit_status_request
    from members.transaction_balances import member_balances

    HTML.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        Fixture.setUpTestData()
        actor, manager, head, admin = (
            Fixture.operator,
            Fixture.reviewer,
            Fixture.second,
            Fixture.admin,
        )
        m.LedgerOfficerProfile.objects.create(user=manager, epf_number="GUIDE-MGR-001")
        m.LedgerOfficerProfile.objects.create(user=head, epf_number="GUIDE-HEAD-001")
        Fixture.level.name = "Manager review"
        Fixture.level.save()
        second_level = m.ApprovalLevel.objects.create(
            hierarchy=Fixture.policy, position=2, name="Final higher-level review"
        )
        second_level.approvers.add(head)
        clients = {}
        for name, user in (
            ("operator", actor),
            ("manager", manager),
            ("head", head),
            ("admin", admin),
        ):
            clients[name] = Client()
            clients[name].force_login(user)
        captures = []
        checks = {}

        def snap(
            name,
            route,
            *,
            params=None,
            args=None,
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

        def decision(
            request,
            who,
            action="approve",
            reason="Training: source records, member identity and reason verified.",
        ):
            response = clients[who].post(
                reverse("approval_request_detail", args=[request.approval_id]),
                {"decision": action, "reason": reason},
            )
            assert response.status_code == 302, (who, action, response.status_code)
            request.refresh_from_db()
            request.approval.refresh_from_db()

        snap(
            "01-role-permissions",
            "access_role_create",
            who="admin",
            mode="statusPermissions",
            fill={
                "name": "Training Membership Manager",
                "module_permissions": [
                    str(
                        Permission.objects.get(
                            codename="change_memberstatusrequest",
                            content_type__app_label="members",
                        ).pk
                    )
                ],
            },
        )
        snap(
            "02-approval-hierarchy",
            "approval_hierarchy_edit",
            args=[Fixture.policy.pk],
            who="admin",
            height=800,
        )
        snap(
            "03-ordered-levels",
            "approval_hierarchy_edit",
            args=[Fixture.policy.pk],
            who="admin",
            mode="levels",
        )
        snap(
            "04-requester-approval-setting",
            "approval_hierarchy_edit",
            args=[Fixture.policy.pk],
            who="admin",
            mode="firstLevel",
            fill={
                "levels-0-allow_self_approval": True,
                "levels-0-approvers": [str(actor.pk), str(manager.pk)],
            },
        )

        today = timezone.localdate()
        member_data = {
            "district": Fixture.branch.pk,
            "office_location": Fixture.office.pk,
            "lager": Fixture.lager.pk,
            "school": Fixture.school.pk,
            "member_no": "ACT-NEW-001",
            "computer_no": "ACT-C001",
            "title": "Ms.",
            "full_name": "Training New Activation Member",
            "date_of_birth": "1990-02-02",
            "gender": "female",
            "nic_no": "199012345678",
            "phone_mobile": "0771234567",
            "admit_date": today.isoformat(),
            "office_address": "Training office address",
            "private_address": "Training private address",
            "status": "pending",
        }
        identity_fill = {
            name: member_data[name]
            for name in (
                "member_no",
                "computer_no",
                "title",
                "full_name",
                "date_of_birth",
                "gender",
                "nic_no",
                "admit_date",
            )
        }
        snap(
            "05-new-registration-status",
            "member_create",
            mode="memberIdentity",
            fill=identity_fill,
        )
        response = clients["operator"].post(reverse("member_create"), member_data)
        assert response.status_code == 302
        new_member = m.Member.objects.get(member_no="ACT-NEW-001")
        assert new_member.status == "pending"
        activation = new_member.status_requests.get()
        snap(
            "06-initial-activation-pending",
            "member_status_detail",
            args=[activation.pk],
            height=1150,
        )
        decision(activation, "manager")
        new_member.refresh_from_db()
        assert new_member.status == "pending"
        decision(activation, "head")
        new_member.refresh_from_db()
        assert new_member.status == "active"
        snap(
            "07-initial-activation-applied",
            "member_status_detail",
            args=[activation.pk],
            height=1150,
        )
        checks["registration_awaited_all_levels"] = True

        member = Fixture.member
        before = {
            "ledger": member.ledgers.get(status="active").reference,
            "balance": member_balances(member, today)["total"],
            "entries": m.PaymentTransaction.objects.filter(member=member).count(),
        }
        form_values = {
            "reason": "resignation",
            "description": "Training: signed member resignation confirmed against the source record.",
            "requested_effective_date": today.isoformat(),
        }
        snap(
            "09-deactivation-request-form",
            "member_status_create",
            params={"member": member.pk},
            fill=form_values,
            height=1350,
        )
        # Supporting files are never displayed publicly; this PDF is entirely fictional.
        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
        document = SimpleUploadedFile(
            "training-resignation-notice.pdf", pdf, content_type="application/pdf"
        )
        response = clients["operator"].post(
            reverse("member_status_create"),
            {
                "member": member.pk,
                "kind": "inactivate",
                **form_values,
                "documents": document,
            },
        )
        assert response.status_code == 302
        inactive = member.status_requests.get()
        assert not decision_rights(actor, inactive.approval)["approve"]
        member.refresh_from_db()
        assert member.status == "active"
        snap(
            "10-deactivation-pending",
            "member_status_detail",
            args=[inactive.pk],
            height=1150,
        )
        snap(
            "11-supporting-documents",
            "member_status_detail",
            args=[inactive.pk],
            mode="documents",
        )
        snap(
            "12-manager-approval-review",
            "approval_request_detail",
            args=[inactive.approval_id],
            who="manager",
            height=1100,
        )
        snap(
            "13-record-decision",
            "approval_request_detail",
            args=[inactive.approval_id],
            who="manager",
            mode="decision",
            fill={
                "reason": "Training: identity, signed resignation and effective date verified."
            },
        )
        download = clients["manager"].get(
            reverse("member_status_document", args=[inactive.documents.get().pk])
        )
        assert download.status_code == 200
        assert Fixture.other.pk != actor.pk
        outsider = Client()
        outsider.force_login(Fixture.other)
        assert (
            outsider.get(
                reverse("member_status_document", args=[inactive.documents.get().pk])
            ).status_code
            == 404
        )
        decision(inactive, "manager")
        member.refresh_from_db()
        assert member.status == "active"
        snap(
            "14-next-approval-level",
            "approval_request_detail",
            args=[inactive.approval_id],
            who="head",
            mode="progress",
        )
        decision(inactive, "head")
        member.refresh_from_db()
        assert member.status == "inactive"
        snap(
            "15-deactivation-applied",
            "member_status_detail",
            args=[inactive.pk],
            height=1150,
        )
        checks["deactivation_waited_for_final_review"] = True
        checks["documents_private"] = True

        reactivation_values = {
            "reason": "eligibility_restored",
            "description": "Training: membership eligibility restored following the approved review.",
            "requested_effective_date": today.isoformat(),
        }
        snap(
            "16-reactivation-request-form",
            "member_status_create",
            params={"member": member.pk},
            fill=reactivation_values,
            height=1350,
        )
        response = clients["operator"].post(
            reverse("member_status_create"),
            {"member": member.pk, "kind": "reactivate", **reactivation_values},
        )
        assert response.status_code == 302
        reactive = member.status_requests.latest("pk")
        decision(reactive, "manager")
        decision(reactive, "head")
        member.refresh_from_db()
        assert member.status == "active"
        after = {
            "ledger": member.ledgers.get(status="active").reference,
            "balance": member_balances(member, today)["total"],
            "entries": m.PaymentTransaction.objects.filter(member=member).count(),
        }
        assert before == after and member.ledgers.count() == 1
        assert member.active_periods.count() == 2
        checks["before"] = before
        checks["after"] = after
        checks["same_ledger_and_balances"] = True
        snap(
            "17-reactivation-and-active-periods",
            "member_status_detail",
            args=[reactive.pk],
            mode="statusHistory",
        )
        snap(
            "18-ledger-status-history",
            "member_ledger_detail",
            args=[Fixture.ledger.pk],
            mode="ledgerStatusHistory",
        )
        snap(
            "19-membership-audit-logs",
            "audit_log_list",
            who="admin",
            params={"module": "memberstatusrequest"},
            height=1150,
        )
        event = m.AuditLog.objects.filter(
            source_model="members.member",
            source_id=member.pk,
            after__status__value="active",
        ).latest("pk")
        snap(
            "20-member-audit-change",
            "audit_log_detail",
            args=[event.pk],
            who="admin",
            mode="auditTable",
        )

        rejected = submit_status_request(
            actor,
            member.pk,
            "inactivate",
            "administrative",
            "Training request rejected because the provided evidence is incomplete.",
            today,
        )
        decision(
            rejected,
            "manager",
            "reject",
            "Training: evidence is insufficient for the requested change.",
        )
        cancelled = submit_status_request(
            actor,
            member.pk,
            "inactivate",
            "other",
            "Training request withdrawn after confirming it was entered in error.",
            today,
        )
        decision(
            cancelled,
            "operator",
            "cancel",
            "Training: requester withdraws the mistaken request before approval.",
        )
        future = submit_status_request(
            actor,
            member.pk,
            "inactivate",
            "administrative",
            "Training future-dated membership status change for scheduled processing.",
            today + timedelta(days=2),
        )
        decision(future, "manager")
        decision(future, "head")
        assert future.state == "scheduled"
        member.refresh_from_db()
        assert member.status == "active"
        snap(
            "21-approved-scheduled-request",
            "member_status_detail",
            args=[future.pk],
            height=1150,
        )
        snap("08-find-status-requests", "member_status_list", height=1050)
        checks["rejection_cancellation_preserved_state"] = True
        checks["future_approval_scheduled"] = True
        metadata = {
            "prepared_on": "2026-11-26",
            "timezone": "Asia/Colombo",
            "notice": "Actual current-system views with fictional records in a uniquely named disposable test database. Live records and settings were not changed.",
            "captures": captures,
            "checks": checks,
            "temporary_database_removed_on_exit": True,
        }
        (ASSETS / "capture-evidence.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        print(json.dumps({"screenshots": len(captures), "checks": checks}))
    finally:
        runner.teardown_databases(old)


if __name__ == "__main__":
    main()

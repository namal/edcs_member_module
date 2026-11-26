"""Ledger ownership is an authorization boundary, including within one district."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .access_forms import WorkspaceUserForm
from .approval_services import decide
from .child_interest import post_child_interest_year, save_child_interest_year
from .contribution_services import allocation_preview, post_receipt
from .deposit_interest import post_interest_year, save_interest_year
from .fix_deposit_services import issue_loan
from .ledger_tracking import ledger_actor
from .permissions import accessible_members, district_records


class LedgerAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.admin = get_user_model().objects.create_superuser(
            "ledger-access-admin", password="test-only-pass"
        )
        cls.district = m.District.objects.get(code="COL")
        cls.office = m.OfficeLocation.objects.create(
            district=cls.district, name="Shared ledger zone", code="ACL-ZONE"
        )
        cls.school = m.School.objects.create(
            district=cls.district,
            zonal_office=cls.office,
            name="Shared ledger department",
            code="ACL-SCHOOL",
        )
        cls.payment_type = m.PaymentType.objects.get(name="Special Deposit")
        cls.payment_type.interest_rate = Decimal("8")
        cls.payment_type.save()
        cls.policy = m.FixDepositPolicy.objects.first()
        cls.policy.loan_interest_rate = Decimal("8")
        cls.policy.save()
        cls.rows = []
        for index in range(2):
            user = get_user_model().objects.create_user(
                f"ledger-owner-{index}",
                password="test-only-pass",
                first_name=f"Ledger Owner {index}",
                is_staff=True,
            )
            user.assigned_districts.add(cls.district)
            user.user_permissions.set(
                Permission.objects.filter(content_type__app_label="members")
            )
            profile = m.LedgerOfficerProfile.objects.create(
                user=user, epf_number=f"LEDGER-EPF-{index}"
            )
            profile.zonal_offices.add(cls.office)
            lager = m.Lager.objects.create(
                zonal_office=cls.office,
                name=f"Assigned master {index}",
                code=f"ACL-LEDGER-{index}",
                maintenance_officer=user,
            )
            member = m.Member.objects.create(
                district=cls.district,
                office_location=cls.office,
                school=cls.school,
                lager=lager,
                member_no=f"ACL-MEMBER-{index}",
                full_name=f"Private Ledger Member {index}",
                nic_no=f"1990123456{index}0",
                salary="1000",
                office_address="Office address",
                private_address="Private address",
                phone_mobile="0771234567",
                gender="female",
                admit_date=date(2020, 1, 1),
            )
            opening = m.OpeningBalance.objects.create(
                member=member, balance_date=date(2025, 1, 1)
            )
            m.OpeningBalanceAmount.objects.create(
                opening_balance=opening, payment_type=cls.payment_type, amount="1000"
            )
            m.OpeningBalanceAmount.objects.create(
                opening_balance=opening,
                payment_type=cls.policy.payment_type,
                amount="5000",
            )
            plan = m.PaymentPlan.objects.create(
                member=member, agreement_date=date(2025, 1, 1)
            )
            m.PaymentPlanAmount.objects.create(
                plan=plan, payment_type=cls.payment_type, amount="50"
            )
            entry = m.PaymentTransaction.objects.create(
                member=member,
                payment_type=cls.payment_type,
                transaction_date=date(2025, 2, 1),
                credit="200",
                reference=f"ACL-ENTRY-{index}",
            )
            child = m.Child.objects.create(
                member=member,
                name=f"Private Ledger Child {index}",
                date_of_birth=date(2015, 1, 1),
            )
            deposit = m.ChildDeposit.objects.create(
                child=child,
                deposit_type=m.ChildDepositType.objects.first(),
                opened_on=date(2025, 1, 1),
                opening_amount="100",
            )
            child_entry = m.ChildDepositTransaction.objects.create(
                deposit=deposit,
                transaction_date=date(2025, 2, 1),
                credit="20",
                interest_rate="8",
            )
            account = m.FixDepositAccount.objects.create(
                member=member, policy=cls.policy, cycle_started_on=date(2020, 1, 1)
            )
            loan = issue_loan(user, account.pk, date(2025, 3, 1), Decimal("100"))
            year = save_interest_year(
                member, cls.payment_type, date(2025, 1, 1), today=cls.today
            )
            child_year = save_child_interest_year(
                deposit, date(2025, 1, 1), today=cls.today
            )
            values = {
                "member": member,
                "received_on": cls.today,
                "amount": Decimal("50"),
                "payment_type": None,
                "reference": f"ACL-RECEIPT-{index}",
                "description": "Recorded contribution",
                "operation_key": uuid4(),
            }
            preview = allocation_preview(member, cls.today, Decimal("50"))
            receipt = post_receipt(user, values, preview["fingerprint"])
            cls.rows.append(
                dict(
                    user=user,
                    profile=profile,
                    lager=lager,
                    member=member,
                    opening=opening,
                    plan=plan,
                    entry=entry,
                    child=child,
                    deposit=deposit,
                    child_entry=child_entry,
                    account=account,
                    loan=loan,
                    year=year,
                    child_year=child_year,
                    receipt=receipt,
                )
            )

    def setUp(self):
        self.own, self.other = self.rows
        self.user = self.own["user"]
        self.client.force_login(self.user)

    def assert_denied(self, response):
        self.assertIn(response.status_code, (403, 404))

    def test_same_district_ledger_ownership_filters_every_member_linked_model(self):
        for key in (
            "member",
            "opening",
            "plan",
            "entry",
            "child",
            "deposit",
            "child_entry",
            "account",
            "loan",
            "year",
            "child_year",
            "receipt",
        ):
            with self.subTest(key=key):
                model = type(self.own[key])
                self.assertEqual(
                    set(
                        district_records(self.user, model).values_list("pk", flat=True)
                    ),
                    (
                        {self.own[key].pk}
                        if key != "entry"
                        else {
                            self.own[key].pk,
                            self.own["receipt"].allocations.get().transaction_id,
                        }
                    ),
                )
        self.assertEqual(set(accessible_members(self.user)), {self.own["member"]})

    def test_directory_dropdowns_exports_and_dashboard_do_not_reveal_other_members(
        self,
    ):
        for route in (
            "member_list",
            "member_export",
            "child_list",
            "child_deposit_list",
            "payment_plan_list",
            "opening_balance_list",
            "fix_deposit_list",
            "contribution_list",
            "member_ledger_list",
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.other["member"].full_name)
                self.assertNotContains(response, self.other["member"].member_no)
                self.assertNotContains(response, self.other["child"].name)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["total_members"], 1)
        self.assertEqual(response.context["districts"][0].member_count, 1)

    def test_direct_record_urls_and_forged_edits_are_blocked(self):
        routes = {
            "member": ("member_detail", "member_edit", "member_delete"),
            "child": ("child_detail", "child_edit", "child_delete"),
            "deposit": (
                "child_deposit_detail",
                "child_deposit_edit",
                "child_deposit_delete",
            ),
            "child_entry": ("child_entry_edit", "child_entry_delete"),
            "plan": ("payment_plan_detail", "payment_plan_edit", "payment_plan_delete"),
            "opening": (
                "opening_balance_detail",
                "opening_balance_edit",
                "opening_balance_delete",
            ),
            "entry": ("payment_transaction_edit", "payment_transaction_delete"),
            "account": (
                "fix_deposit_detail",
                "fix_deposit_edit",
                "fix_loan_create",
                "fix_withdrawal_create",
            ),
            "loan": ("fix_loan_detail", "fix_loan_entry", "fix_loan_settle"),
            "year": ("deposit_interest_detail", "deposit_interest_edit"),
            "child_year": ("child_interest_detail", "child_interest_edit"),
            "receipt": ("contribution_detail",),
        }
        for key, names in routes.items():
            for name in names:
                with self.subTest(name=name):
                    url = reverse(name, args=[self.other[key].pk])
                    self.assert_denied(self.client.get(url))
                    self.assert_denied(self.client.post(url, {}))

    def test_foreign_member_credit_and_child_entry_submissions_create_nothing(self):
        before = (
            m.PaymentTransaction.objects.count(),
            m.ChildDepositTransaction.objects.count(),
        )
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "member": self.other["member"].pk,
                "payment_type": self.payment_type.pk,
                "transaction_date": self.today.isoformat(),
                "credit": "500",
                "debit": "0",
                "reference": "FORGED-LEDGER",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assert_denied(
            self.client.post(
                reverse("child_entry_create", args=[self.other["deposit"].pk]),
                {
                    "transaction_date": self.today.isoformat(),
                    "credit": "500",
                    "debit": "0",
                },
            )
        )
        self.assertEqual(
            before,
            (
                m.PaymentTransaction.objects.count(),
                m.ChildDepositTransaction.objects.count(),
            ),
        )

    def test_model_write_and_delete_guards_recheck_owner_inside_request_context(self):
        token = ledger_actor.set(self.user)
        try:
            foreign = self.other["member"]
            foreign.phone_mobile = "0772222222"
            with self.assertRaises(PermissionDenied):
                foreign.save()
            with self.assertRaises(PermissionDenied):
                m.ChildDepositTransaction.objects.create(
                    deposit=self.other["deposit"],
                    transaction_date=self.today,
                    credit="10",
                    interest_rate="8",
                )
            with self.assertRaises(PermissionDenied):
                self.other["entry"].delete()
        finally:
            ledger_actor.reset(token)
        self.other["member"].refresh_from_db()
        self.assertEqual(self.other["member"].phone_mobile, "0771234567")
        self.assertTrue(
            m.PaymentTransaction.objects.filter(pk=self.other["entry"].pk).exists()
        )

    def test_foreign_loan_receipt_and_interest_services_are_blocked(self):
        with self.assertRaises((PermissionDenied, Http404)):
            issue_loan(self.user, self.other["account"].pk, self.today, Decimal("10"))
        with self.assertRaises(PermissionDenied):
            post_interest_year(self.other["year"].pk, actor=self.user)
        with self.assertRaises(PermissionDenied):
            post_child_interest_year(self.other["child_year"].pk, actor=self.user)
        values = {
            "member": self.other["member"],
            "received_on": self.today,
            "amount": Decimal("50"),
            "payment_type": None,
            "reference": "FORGED-RECEIPT",
            "description": "",
            "operation_key": uuid4(),
        }
        with self.assertRaises(PermissionDenied):
            post_receipt(self.user, values, "not-authorized")

    def test_unassigned_or_missing_epf_user_has_no_member_access(self):
        user = get_user_model().objects.create_user("unassigned-ledger-user")
        user.assigned_districts.add(self.district)
        user.user_permissions.set(self.user.user_permissions.all())
        self.assertFalse(accessible_members(user).exists())
        m.Lager.objects.filter(pk=self.other["lager"].pk).update(
            maintenance_officer=user
        )
        self.assertFalse(accessible_members(user).exists())
        profile = m.LedgerOfficerProfile.objects.create(
            user=user, epf_number="VALID-NEW-EPF"
        )
        self.assertFalse(accessible_members(user).exists())
        profile.zonal_offices.add(self.office)
        self.assertEqual(set(accessible_members(user)), {self.other["member"]})
        user.assigned_districts.clear()
        self.assertFalse(accessible_members(user).exists())

    def test_reassignment_immediately_revokes_access_in_an_existing_session(self):
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.own["member"].pk])
            ).status_code,
            200,
        )
        lager = self.own["lager"]
        lager.maintenance_officer = self.other["user"]
        lager.save()
        self.assert_denied(
            self.client.get(reverse("member_detail", args=[self.own["member"].pk]))
        )
        self.assertFalse(accessible_members(self.user).exists())
        self.assertEqual(accessible_members(self.other["user"]).count(), 2)
        # Historic individual officer snapshots do not grant current member access.
        self.assertEqual(
            self.own["member"].ledgers.get(status="active").maintenance_officer_id,
            self.user.pk,
        )

    def test_ledger_tables_display_code_and_current_assigned_epf(self):
        for route in ("lager_list", "member_ledger_list"):
            response = self.client.get(reverse(route))
            self.assertContains(response, "Ledger Code")
            self.assertContains(response, "Assigned System User's EPF Number")
            self.assertContains(response, self.own["lager"].code)
            self.assertContains(response, self.own["profile"].epf_number)
            self.assertNotContains(response, self.other["lager"].code)
        profile = self.own["profile"]
        profile.epf_number = "UPDATED-OWNER-EPF"
        profile.save()
        self.assertContains(
            self.client.get(reverse("member_ledger_list")), "UPDATED-OWNER-EPF"
        )
        self.assertEqual(accessible_members(self.user).count(), 1)

    def test_epf_cannot_be_removed_while_ledger_is_assigned(self):
        profile = self.own["profile"]
        profile.epf_number = None
        with self.assertRaises(ValidationError):
            profile.save()
        form = WorkspaceUserForm(
            {
                "username": self.user.username,
                "is_active": "on",
                "districts": [self.district.pk],
                "epf_number": "",
                "zonal_offices": [self.office.pk],
            },
            actor=self.admin,
            instance=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("epf_number", form.errors)

    def test_reports_and_location_choices_do_not_bypass_ledger_access(self):
        self.assert_denied(
            self.client.get(reverse("lager_activity", args=[self.other["lager"].pk]))
        )
        self.assert_denied(
            self.client.get(
                reverse("lager_activity_export", args=[self.other["lager"].pk])
            )
        )
        response = self.client.get(
            reverse("location_options"), {"district": self.district.pk}
        )
        self.assertEqual(
            [row["id"] for row in response.json()["lagers"]], [self.own["lager"].pk]
        )
        # Transfer destination metadata grants no access to the destination's members.
        response = self.client.get(
            reverse("location_options"),
            {"district": self.district.pk, "purpose": "transfer"},
        )
        self.assertEqual(len(response.json()["lagers"]), 2)
        self.assert_denied(
            self.client.get(reverse("member_detail", args=[self.other["member"].pk]))
        )

    def pending_edit(self):
        hierarchy = m.ApprovalHierarchy.objects.create(
            name="Ledger editing review", module="paymenttransaction", action="change"
        )
        level = m.ApprovalLevel.objects.create(
            hierarchy=hierarchy, position=1, name="Review"
        )
        level.approvers.add(self.other["user"])
        response = self.client.post(
            reverse("payment_transaction_edit", args=[self.own["entry"].pk]),
            {
                "member": self.own["member"].pk,
                "payment_type": self.payment_type.pk,
                "transaction_date": "2025-02-01",
                "credit": "300",
                "debit": "0",
                "reference": "ACL-EDIT-PENDING",
            },
        )
        self.assertEqual(response.status_code, 302)
        return m.ApprovalRequest.objects.get()

    def test_cross_ledger_approver_cannot_view_or_process_request_but_admin_can(self):
        record = self.pending_edit()
        client = Client()
        client.force_login(self.other["user"])
        self.assert_denied(
            client.get(reverse("approval_request_detail", args=[record.pk]))
        )
        with self.assertRaises(PermissionDenied):
            decide(self.other["user"], record.pk, "approve")
        admin_client = Client()
        admin_client.force_login(self.admin)
        self.assertEqual(
            admin_client.get(
                reverse("approval_request_detail", args=[record.pk])
            ).status_code,
            200,
        )
        result = decide(
            self.admin, record.pk, "override_approve", "Administrator ledger review"
        )
        self.assertEqual(result.status, "approved", result.outcome)

    def test_pending_request_cannot_post_after_ledger_is_reassigned(self):
        record = self.pending_edit()
        m.Lager.objects.filter(pk=self.own["lager"].pk).update(
            maintenance_officer=self.other["user"]
        )
        result = decide(
            self.admin, record.pk, "override_approve", "Access recheck during approval"
        )
        self.assertEqual(result.status, "failed")
        self.own["entry"].refresh_from_db()
        self.assertEqual(self.own["entry"].credit, Decimal("200"))
        self.assert_denied(
            self.client.get(reverse("approval_request_detail", args=[record.pk]))
        )

    def test_administrator_retains_all_ledger_and_financial_access(self):
        self.client.force_login(self.admin)
        self.assertEqual(accessible_members(self.admin).count(), 2)
        for row in self.rows:
            for route, obj in (
                ("member_detail", row["member"]),
                ("child_deposit_detail", row["deposit"]),
                ("contribution_detail", row["receipt"]),
            ):
                self.assertEqual(
                    self.client.get(reverse(route, args=[obj.pk])).status_code, 200
                )

    def test_owner_can_post_member_and_child_credits_and_debits(self):
        for label, credit, debit in (("CREDIT", "50", "0"), ("DEBIT", "0", "10")):
            response = self.client.post(
                reverse("payment_transaction_create"),
                {
                    "member": self.own["member"].pk,
                    "payment_type": self.payment_type.pk,
                    "transaction_date": self.today.isoformat(),
                    "credit": credit,
                    "debit": debit,
                    "reference": f"OWN-MEMBER-{label}",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                m.PaymentTransaction.objects.filter(
                    member=self.own["member"], reference=f"OWN-MEMBER-{label}"
                ).exists()
            )
            response = self.client.post(
                reverse("child_entry_create", args=[self.own["deposit"].pk]),
                {
                    "transaction_date": self.today.isoformat(),
                    "credit": credit,
                    "debit": debit,
                    "reference": f"OWN-CHILD-{label}",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                m.ChildDepositTransaction.objects.filter(
                    deposit=self.own["deposit"], reference=f"OWN-CHILD-{label}"
                ).exists()
            )

    def test_member_cannot_be_registered_against_another_users_lager(self):
        payload = {
            "district": self.district.pk,
            "office_location": self.office.pk,
            "school": self.school.pk,
            "lager": self.other["lager"].pk,
            "member_no": "ACL-NEW-MEMBER",
            "computer_no": "COMP-ACL-NEW-MEMBER",
            "title": "Ms.",
            "date_of_birth": "1990-01-01",
            "full_name": "New ledger member",
            "nic_no": "199012345699",
            "salary": "1000",
            "office_address": "Office",
            "private_address": "Home",
            "phone_mobile": "0771234567",
            "gender": "female",
            "admit_date": self.today.isoformat(),
            "status": "active",
        }
        response = self.client.post(reverse("member_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("lager", response.context["form"].errors)
        self.assertFalse(m.Member.objects.filter(member_no="ACL-NEW-MEMBER").exists())
        payload["lager"] = self.own["lager"].pk
        response = self.client.post(reverse("member_create"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            m.Member.objects.get(member_no="ACL-NEW-MEMBER").lager_id,
            self.own["lager"].pk,
        )

    def move_member_to_other_lager(self):
        member = self.own["member"]
        member.lager = self.other["lager"]
        member.save(update_fields=["lager"])
        return member

    def test_old_active_page_cannot_expose_a_member_after_lager_reassignment(self):
        old_page = self.own["member"].ledgers.get(status="active")
        member = self.move_member_to_other_lager()
        self.assert_denied(
            self.client.get(reverse("member_ledger_detail", args=[old_page.pk]))
        )
        self.assert_denied(self.client.get(reverse("member_detail", args=[member.pk])))
        self.client.force_login(self.other["user"])
        page = self.client.get(reverse("member_ledger_list"))
        self.assertContains(page, self.other["lager"].code, count=2)
        self.assertNotContains(page, self.own["lager"].code)
        self.assertEqual(
            self.client.get(
                reverse("member_ledger_detail", args=[old_page.pk])
            ).status_code,
            200,
        )

    def test_pending_request_is_hidden_and_cannot_apply_after_member_moves(self):
        record = self.pending_edit()
        self.move_member_to_other_lager()
        self.assert_denied(
            self.client.get(reverse("approval_request_detail", args=[record.pk]))
        )
        self.assertEqual(
            decide(
                self.admin, record.pk, "override_approve", "Rechecking moved member"
            ).status,
            "failed",
        )

    def test_transfer_retains_frozen_history_under_the_departing_lager(self):
        from .transfer_services import transfer_member

        member = self.move_member_to_other_lager()
        old_page = member.ledgers.get(status="active")
        result = transfer_member(
            self.other["user"],
            member_pk=member.pk,
            expected_ledger_pk=old_page.pk,
            office=self.office,
            lager=self.own["lager"],
            school=self.school,
            subdivision=None,
            officer=self.user,
            effective_date=self.today,
            reason="Member returns to original ledger",
        )
        old_page.refresh_from_db()
        self.assertEqual(old_page.lager_id, self.other["lager"].pk)
        self.client.force_login(self.other["user"])
        page = self.client.get(reverse("member_ledger_detail", args=[old_page.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["can_view_current_member"])
        self.assert_denied(
            self.client.get(reverse("member_ledger_detail", args=[result.to_ledger_id]))
        )
        self.assert_denied(self.client.get(reverse("member_detail", args=[member.pk])))

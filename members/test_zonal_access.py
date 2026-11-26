from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from . import models as m
from .access_forms import WorkspaceUserForm, workspace_permissions
from .contribution_services import post_receipt
from .ledger_tracking import ledger_actor
from .permissions import accessible_members, accessible_zonal_offices, district_records
from .transfer_services import assign_ledger_officer, eligible_officers, transfer_member


class ZonalAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_ledger_access import LedgerAccessTests

        LedgerAccessTests.setUpTestData.__func__(cls)
        cls.own, cls.other = cls.rows
        cls.user = cls.own["user"]
        cls.second_office = m.OfficeLocation.objects.create(
            district=cls.district, name="Second assigned zone", code="MULTI-Z2"
        )
        cls.second_school = m.School.objects.create(
            district=cls.district,
            zonal_office=cls.second_office,
            name="Second school",
            code="MULTI-S2",
        )
        cls.second_lager = m.Lager.objects.create(
            zonal_office=cls.second_office,
            name="Second owned ledger",
            code="MULTI-L2",
            maintenance_officer=cls.user,
        )
        cls.second_member = m.Member.objects.create(
            district=cls.district,
            office_location=cls.second_office,
            school=cls.second_school,
            lager=cls.second_lager,
            member_no="MULTI-M2",
            full_name="Second zone member",
            salary="1000",
            nic_no="199012345688",
            gender="female",
            office_address="Office",
            private_address="Home",
            phone_mobile="0771234567",
            admit_date=date(2020, 1, 1),
        )
        cls.second_child = m.Child.objects.create(
            member=cls.second_member,
            name="Second zone child",
            date_of_birth=date(2015, 1, 1),
        )
        cls.second_deposit = m.ChildDeposit.objects.create(
            child=cls.second_child,
            deposit_type=m.ChildDepositType.objects.first(),
            opened_on=date(2025, 1, 1),
            opening_amount="100",
        )
        cls.foreign_district = m.District.objects.create(
            name="Zonal foreign district", code="MULTI-D", province="Western"
        )
        cls.foreign_office = m.OfficeLocation.objects.create(
            district=cls.foreign_district, name="Foreign district zone", code="MULTI-Z3"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def data(self, **changes):
        return {
            "username": self.user.username,
            "is_active": "on",
            "epf_number": self.user.ledger_profile.epf_number,
            "districts": [self.district.pk],
            "zonal_offices": [self.office.pk, self.second_office.pk],
            "module_permissions": list(
                workspace_permissions()
                .filter(user=self.user)
                .values_list("pk", flat=True)
            ),
            **changes,
        }

    def test_admin_can_save_and_reload_multiple_offices_across_selected_districts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.data(
                districts=[self.district.pk, self.foreign_district.pk],
                zonal_offices=[
                    self.office.pk,
                    self.second_office.pk,
                    self.foreign_office.pk,
                ],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertCountEqual(
            self.user.ledger_profile.zonal_offices.all(),
            [self.office, self.second_office, self.foreign_office],
        )
        response = self.client.get(reverse("access_user_edit", args=[self.user.pk]))
        self.assertCountEqual(
            response.context["form"]["zonal_offices"].value(),
            [self.office.pk, self.second_office.pk, self.foreign_office.pk],
        )
        self.assertContains(
            self.client.get(reverse("access_users")), self.second_office.name
        )
        self.assertNotIn("ledger_office", response.context["form"].fields)

    def test_cross_district_and_unknown_office_submissions_are_rejected(self):
        for zones in [[self.office.pk, self.foreign_office.pk], [999999]]:
            form = WorkspaceUserForm(
                self.data(zonal_offices=zones), actor=self.admin, instance=self.user
            )
            self.assertFalse(form.is_valid())
            self.assertIn("zonal_offices", form.errors)
        self.assertCountEqual(
            self.user.ledger_profile.zonal_offices.all(), [self.office]
        )

    def test_only_administrators_can_read_or_change_user_office_assignments(self):
        url = reverse("access_user_edit", args=[self.user.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.data()).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("access_user_create")).status_code, 403
        )

    def test_district_office_and_owned_ledger_must_all_match(self):
        self.assertNotIn(self.second_member, accessible_members(self.user))
        self.user.ledger_profile.zonal_offices.add(self.second_office)
        self.assertIn(self.second_member, accessible_members(self.user))
        self.assertNotIn(self.other["member"], accessible_members(self.user))
        self.user.assigned_districts.clear()
        self.assertFalse(accessible_members(self.user).exists())
        self.assertFalse(accessible_zonal_offices(self.user).exists())

    def test_member_children_and_posting_services_deny_an_owned_ledger_in_an_unassigned_office(
        self,
    ):
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.second_member.pk])
            ).status_code,
            404,
        )
        self.assertFalse(
            district_records(self.user, m.ChildDeposit)
            .filter(pk=self.second_deposit.pk)
            .exists()
        )
        token = ledger_actor.set(self.user)
        try:
            with self.assertRaises(PermissionDenied):
                m.ChildDepositTransaction.objects.create(
                    deposit=self.second_deposit,
                    transaction_date=self.today,
                    credit=Decimal("10"),
                    created_by=self.user,
                )
        finally:
            ledger_actor.reset(token)
        with self.assertRaises(PermissionDenied):
            post_receipt(
                self.user,
                {"member": self.second_member, "operation_key": uuid4()},
                "forged",
            )
        self.user.ledger_profile.zonal_offices.add(self.second_office)
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.second_member.pk])
            ).status_code,
            200,
        )
        self.assertTrue(
            district_records(self.user, m.ChildDeposit)
            .filter(pk=self.second_deposit.pk)
            .exists()
        )

    def test_location_options_and_officer_eligibility_use_all_assigned_offices(self):
        response = self.client.get(
            reverse("location_options"), {"district": self.district.pk}
        )
        self.assertNotIn(
            self.second_office.pk, {row["id"] for row in response.json()["offices"]}
        )
        self.assertFalse(
            eligible_officers(self.second_office).filter(pk=self.user.pk).exists()
        )
        self.user.ledger_profile.zonal_offices.add(self.second_office)
        self.assertTrue(eligible_officers(self.office).filter(pk=self.user.pk).exists())
        self.assertTrue(
            eligible_officers(self.second_office).filter(pk=self.user.pk).exists()
        )
        response = self.client.get(
            reverse("location_options"), {"district": self.district.pk}
        )
        self.assertIn(
            self.second_office.pk, {row["id"] for row in response.json()["offices"]}
        )

    def test_office_removal_revokes_existing_session_access_after_responsibility_handover(
        self,
    ):
        self.user.ledger_profile.zonal_offices.add(self.second_office)
        self.other["profile"].zonal_offices.add(self.second_office)
        assign_ledger_officer(
            self.admin,
            self.second_member.ledgers.get(status="active").pk,
            self.other["user"],
            "Responsibility handed over before office removal",
        )
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.second_member.pk])
            ).status_code,
            200,
        )
        self.user.ledger_profile.zonal_offices.remove(self.second_office)
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.second_member.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.own["member"].pk])
            ).status_code,
            200,
        )

    def test_active_responsibility_and_cross_district_changes_are_validated_at_relation(
        self,
    ):
        with self.assertRaises(ValidationError), transaction.atomic():
            self.user.ledger_profile.zonal_offices.add(self.foreign_office)
        with self.assertRaises(ValidationError), transaction.atomic():
            self.user.ledger_profile.zonal_offices.clear()
        form = WorkspaceUserForm(
            self.data(zonal_offices=[]), actor=self.admin, instance=self.user
        )
        self.assertIn("zonal_offices", form.errors)

    def test_administrator_has_every_office_without_assignments(self):
        self.assertFalse(self.admin.assigned_districts.exists())
        self.assertEqual(
            accessible_zonal_offices(self.admin).count(),
            m.OfficeLocation.objects.count(),
        )
        self.assertIn(self.second_member, accessible_members(self.admin))

    def test_transfer_service_rejects_unassigned_receiving_office(self):
        with self.assertRaises(PermissionDenied):
            transfer_member(
                self.user,
                member_pk=self.own["member"].pk,
                expected_ledger_pk=self.own["member"].ledgers.get(status="active").pk,
                office=self.second_office,
                lager=self.second_lager,
                school=self.second_school,
                subdivision=None,
                officer=self.user,
                effective_date=self.today,
                reason="Unassigned destination must be refused",
            )


class ZonalAssignmentMigrationTests(TransactionTestCase):
    def restore_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("members"))

    def test_single_offices_ledgers_and_legacy_district_users_are_preserved(self):
        from .test_contributions import setup_records

        setup_records(self)
        self.addCleanup(self.restore_latest)
        before = [("members", "0027_template_only_fixed_amounts")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        Profile, Office, Lager = [
            old.get_model("members", name)
            for name in ("LedgerOfficerProfile", "OfficeLocation", "Lager")
        ]
        first = self.member.office_location_id
        second = Office.objects.create(
            district_id=self.district.pk,
            name="Migrated second office",
            code="ZONE-MIG2",
        )
        third = Office.objects.create(
            district_id=self.district.pk, name="District-only office", code="ZONE-MIG3"
        )
        Profile.objects.filter(user_id=self.operator.pk).update(zonal_office_id=first)
        Lager.objects.create(
            zonal_office=second,
            name="Migrated owned ledger",
            code="ZONE-MIG-L",
            maintenance_officer_id=self.operator.pk,
        )
        User = old.get_model("auth", "User")
        legacy = User.objects.create(username="legacy-district-only")
        legacy.assigned_districts.add(self.district.pk)
        executor = MigrationExecutor(connection)
        executor.migrate([("members", "0028_multiple_user_zonal_offices")])
        profile = m.LedgerOfficerProfile.objects.get(user=self.operator)
        self.assertCountEqual(
            profile.zonal_offices.values_list("pk", flat=True), [first, second.pk]
        )
        self.assertEqual(profile.epf_number, f"TEST-ACCESS-{self.operator.pk}")
        self.assertTrue(
            m.LedgerOfficerProfile.objects.get(user_id=legacy.pk)
            .zonal_offices.filter(pk=third.pk)
            .exists()
        )

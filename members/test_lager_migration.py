import os
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LagerZonalMigrationTests(TransactionTestCase):
    old_target = [("members", "0019_exclude_transition_quarters")]
    new_target = [("members", "0020_lager_zonal_office")]

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def setUp(self):
        self.old = self.migrate(self.old_target)

    def tearDown(self):
        # A test failure must not leave the rest of the suite on the old schema.
        self.migrate(
            [
                (
                    "members",
                    "0023_approval_hierarchies",
                )
            ]
        )
        super().tearDown()

    def test_identity_migration_backfills_names_without_guessing_birth_or_retirement_dates(
        self,
    ):
        district, zone, _, lager = self.migration_data()
        member = self.old.get_model("members", "Member").objects.create(
            district=district,
            office_location=zone,
            lager=lager,
            member_no="MIG-NAME",
            full_name="Krishantha Namal Spencer",
            salary="0",
            nic_no="MIG-NAME",
        )
        apps = self.migrate(
            [("members", "0021_paymentplantemplate_member_date_of_birth_and_more")]
        )
        migrated = apps.get_model("members", "Member").objects.get(pk=member.pk)
        self.assertEqual(migrated.short_name, "K. N. Spencer")
        self.assertEqual(migrated.retirement_age, 60)
        self.assertIsNone(migrated.date_of_birth)
        self.assertIsNone(migrated.expected_retirement_date)
        self.assertIsNone(migrated.pension_date)

    def migration_data(self, *, second_zone=False):
        District = self.old.get_model("members", "District")
        Office = self.old.get_model("members", "OfficeLocation")
        Lager = self.old.get_model("members", "Lager")
        district = District.objects.create(
            name="Migration district", code="MIG", province="Western"
        )
        zone = Office.objects.create(district=district, name="Zone A", code="MIG-A")
        other = (
            Office.objects.create(district=district, name="Zone B", code="MIG-B")
            if second_zone
            else None
        )
        lager = Lager.objects.create(
            district=district, name="Legacy Lager", code="MIG-L"
        )
        return district, zone, other, lager

    def test_single_zone_preserves_lager_and_member_ids(self):
        district, zone, _, lager = self.migration_data()
        Member = self.old.get_model("members", "Member")
        member = Member.objects.create(
            district=district,
            office_location=zone,
            lager=lager,
            member_no="MIG-1",
            salary="0.00",
            nic_no="MIG-NIC-1",
        )
        apps = self.migrate(self.new_target)
        migrated = apps.get_model("members", "Lager").objects.get(pk=lager.pk)
        self.assertEqual(migrated.zonal_office_id, zone.pk)
        self.assertEqual(migrated.code, lager.code)
        self.assertEqual(
            apps.get_model("members", "Member").objects.get(pk=member.pk).lager_id,
            lager.pk,
        )

    def test_shared_district_lager_splits_by_zone_preserving_financial_links(self):
        district, zone, other, lager = self.migration_data(second_zone=True)
        Member = self.old.get_model("members", "Member")
        first = Member.objects.create(
            district=district,
            office_location=zone,
            lager=lager,
            member_no="MIG-1",
            salary="0.00",
            nic_no="MIG-NIC-1",
        )
        second = Member.objects.create(
            district=district,
            office_location=other,
            lager=lager,
            member_no="MIG-2",
            salary="0.00",
            nic_no="MIG-NIC-2",
        )
        Plan = self.old.get_model("members", "PaymentPlan")
        plan = Plan.objects.create(member=second)
        Opening = self.old.get_model("members", "OpeningBalance")
        opening = Opening.objects.create(member=second)
        PaymentType = self.old.get_model("members", "PaymentType")
        payment_type = PaymentType.objects.create(name="Migration Deposit")
        Amount = self.old.get_model("members", "OpeningBalanceAmount")
        amount = Amount.objects.create(
            opening_balance=opening,
            payment_type=payment_type,
            amount=Decimal("1234.56"),
        )
        apps = self.migrate(self.new_target)
        members = apps.get_model("members", "Member")
        self.assertEqual(members.objects.get(pk=first.pk).lager_id, lager.pk)
        moved = members.objects.get(pk=second.pk)
        self.assertNotEqual(moved.lager_id, lager.pk)
        self.assertEqual(moved.lager.zonal_office_id, other.pk)
        self.assertEqual(moved.lager.name, lager.name)
        self.assertNotEqual(moved.lager.code, lager.code)
        self.assertLessEqual(len(moved.lager.code), 20)
        self.assertEqual(
            apps.get_model("members", "PaymentPlan").objects.get(pk=plan.pk).member_id,
            second.pk,
        )
        self.assertEqual(
            apps.get_model("members", "OpeningBalance")
            .objects.get(pk=opening.pk)
            .member_id,
            second.pk,
        )
        self.assertEqual(
            apps.get_model("members", "OpeningBalanceAmount")
            .objects.get(pk=amount.pk)
            .amount,
            Decimal("1234.56"),
        )

    def test_unused_lager_with_ambiguous_zones_requires_explicit_mapping(self):
        _, _, other, lager = self.migration_data(second_zone=True)
        with patch.dict(os.environ, {"LAGER_ZONAL_MIGRATION_MAP": "{}"}):
            with self.assertRaisesMessage(
                RuntimeError, "requires a zonal office mapping"
            ):
                self.migrate(self.new_target)
        self.assertEqual(
            self.old.get_model("members", "Lager").objects.get(pk=lager.pk).code,
            "MIG-L",
        )
        with patch.dict(
            os.environ, {"LAGER_ZONAL_MIGRATION_MAP": f'{{"{lager.pk}": {other.pk}}}'}
        ):
            apps = self.migrate(self.new_target)
        self.assertEqual(
            apps.get_model("members", "Lager").objects.get(pk=lager.pk).zonal_office_id,
            other.pk,
        )

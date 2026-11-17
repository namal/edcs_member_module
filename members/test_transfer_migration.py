from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TransferLedgerMigrationTests(TransactionTestCase):
    old_target = [("members", "0021_paymentplantemplate_member_date_of_birth_and_more")]
    new_target = [
        ("members", "0022_ledgerofficerprofile_memberledger_ledgerrecord_and_more")
    ]

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes("members"))
        super().tearDown()

    def test_backfill_links_existing_financial_records_without_altering_them(self):
        old = self.migrate(self.old_target)
        User = old.get_model(settings.AUTH_USER_MODEL)
        user = User.objects.create(username="legacy-employee")
        district = old.get_model("members", "District").objects.create(
            name="Ledger migration district", code="L-MIG", province="Western"
        )
        office = old.get_model("members", "OfficeLocation").objects.create(
            district=district, name="Original zone", code="L-MIG-Z"
        )
        member = old.get_model("members", "Member").objects.create(
            district=district,
            office_location=office,
            member_no="L-MIG-M",
            full_name="Legacy Member",
            nic_no="L-MIG-NIC",
            salary="1000",
            admit_date=date(2020, 1, 1),
        )
        payment_type = old.get_model("members", "PaymentType").objects.create(
            name="Migration savings"
        )
        opening = old.get_model("members", "OpeningBalance").objects.create(
            member=member, balance_date=date(2025, 1, 1)
        )
        old.get_model("members", "OpeningBalanceAmount").objects.create(
            opening_balance=opening, payment_type=payment_type, amount="1234.56"
        )
        plan = old.get_model("members", "PaymentPlan").objects.create(
            member=member, agreement_date=date(2025, 1, 1)
        )
        old.get_model("members", "PaymentPlanAmount").objects.create(
            plan=plan, payment_type=payment_type, amount="50"
        )
        entry = old.get_model("members", "PaymentTransaction").objects.create(
            member=member,
            payment_type=payment_type,
            transaction_date=date(2025, 2, 1),
            credit="10",
            reference="LEGACY-CREDIT",
        )
        child = old.get_model("members", "Child").objects.create(
            member=member, name="Legacy Child", date_of_birth=date(2015, 1, 1)
        )
        child_type = old.get_model("members", "ChildDepositType").objects.create(
            name="Migration child savings"
        )
        account = old.get_model("members", "ChildDeposit").objects.create(
            child=child,
            deposit_type=child_type,
            opened_on=date(2025, 1, 1),
            opening_amount="100",
        )
        old.get_model("members", "ChildDepositTransaction").objects.create(
            deposit=account,
            transaction_date=date(2025, 2, 1),
            credit="20",
            interest_rate="8",
        )
        source_names = (
            "Member",
            "PaymentTransaction",
            "OpeningBalance",
            "OpeningBalanceAmount",
            "PaymentPlan",
            "PaymentPlanAmount",
            "Child",
            "ChildDeposit",
            "ChildDepositTransaction",
        )
        before = {
            name: list(old.get_model("members", name).objects.order_by("pk").values())
            for name in source_names
        }
        apps = self.migrate(self.new_target)
        after = {
            name: list(apps.get_model("members", name).objects.order_by("pk").values())
            for name in source_names
        }
        self.assertEqual(before, after)
        ledger = apps.get_model("members", "MemberLedger").objects.get(
            member_id=member.pk
        )
        self.assertEqual(ledger.zonal_office_id, office.pk)
        self.assertEqual(ledger.registration_snapshot["member_no"], "L-MIG-M")
        self.assertTrue(ledger.legacy_assignment_pending)
        self.assertIsNone(ledger.maintenance_officer_id)
        profile = apps.get_model("members", "LedgerOfficerProfile").objects.get(
            user_id=user.pk
        )
        self.assertIsNone(profile.epf_number)
        self.assertIsNone(profile.zonal_office_id)
        record = apps.get_model("members", "LedgerRecord").objects.get(
            source_model="members.paymenttransaction", source_id=entry.pk
        )
        self.assertEqual(record.original_ledger_id, ledger.pk)
        event = apps.get_model("members", "LedgerEvent").objects.get(record=record)
        self.assertEqual(event.snapshot["data"]["reference"], "LEGACY-CREDIT")
        self.assertEqual(Decimal(event.snapshot["data"]["credit"]), Decimal("10"))
        self.assertEqual(apps.get_model("members", "LedgerRecord").objects.count(), 8)

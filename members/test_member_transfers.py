from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .access_forms import WorkspaceUserForm
from .transfer_services import assign_ledger_officer, transfer_member


class MemberTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.actor = get_user_model().objects.create_superuser(
            "transfer-admin", password="test-only-pass"
        )
        cls.offices = []
        cls.schools = []
        cls.lagers = []
        cls.officers = []
        for index, code in enumerate(("COL", "GAM", "KAN")):
            district = m.District.objects.get(code=code)
            office = m.OfficeLocation.objects.create(
                district=district, name=f"Transfer office {index}", code=f"TF-Z{index}"
            )
            school = m.School.objects.create(
                district=district,
                zonal_office=office,
                name=f"Transfer school {index}",
                code=f"TF-S{index}",
            )
            lager = m.Lager.objects.create(
                zonal_office=office, name=f"Transfer lager {index}", code=f"TF-L{index}"
            )
            officer = get_user_model().objects.create_user(
                f"officer-{index}", first_name=f"Officer {index}"
            )
            officer.assigned_districts.add(district)
            m.LedgerOfficerProfile.objects.create(
                user=officer, epf_number=f"EPF-{index}"
            ).zonal_offices.add(office)
            cls.offices.append(office)
            cls.schools.append(school)
            cls.lagers.append(lager)
            cls.officers.append(officer)
        cls.member = m.Member.objects.create(
            district=cls.offices[0].district,
            office_location=cls.offices[0],
            school=cls.schools[0],
            lager=cls.lagers[0],
            member_no="TRANSFER-001",
            full_name="Original Member",
            salary="1000",
            nic_no="199012345670",
            gender="female",
            office_address="Original office address",
            private_address="Original home address",
            phone_mobile="0771234567",
            admit_date=date(2020, 1, 1),
        )
        cls.original = cls.member.ledgers.get(status="active")
        assign_ledger_officer(
            cls.actor, cls.original.pk, cls.officers[0], "Original officer assigned"
        )
        cls.payment_type = m.PaymentType.objects.get(name="Special Deposit")
        opening = m.OpeningBalance.objects.create(
            member=cls.member, balance_date=date(2025, 1, 1)
        )
        m.OpeningBalanceAmount.objects.create(
            opening_balance=opening,
            payment_type=cls.payment_type,
            amount=Decimal("1000"),
        )
        cls.old_entry = m.PaymentTransaction.objects.create(
            member=cls.member,
            payment_type=cls.payment_type,
            transaction_date=date(2025, 2, 1),
            credit=Decimal("200"),
            reference="BEFORE-TRANSFER",
        )
        cls.child = m.Child.objects.create(
            member=cls.member, name="Transfer Child", date_of_birth=date(2015, 1, 1)
        )
        cls.child_account = m.ChildDeposit.objects.create(
            child=cls.child,
            deposit_type=m.ChildDepositType.objects.first(),
            opened_on=date(2025, 1, 1),
            opening_amount=Decimal("100"),
        )
        cls.old_child_entry = m.ChildDepositTransaction.objects.create(
            deposit=cls.child_account,
            transaction_date=date(2025, 2, 1),
            credit=Decimal("20"),
            interest_rate=Decimal("8"),
        )
        cls.plan = m.PaymentPlan.objects.create(
            member=cls.member, agreement_date=date(2025, 1, 1)
        )
        m.PaymentPlanAmount.objects.create(
            plan=cls.plan, payment_type=cls.payment_type, amount=Decimal("25")
        )
        for lager, officer in zip(cls.lagers, cls.officers):
            m.Lager.objects.filter(pk=lager.pk).update(maintenance_officer=officer)

    def setUp(self):
        self.client.force_login(self.actor)

    def transfer(self, index=1, **overrides):
        current = self.member.ledgers.get(status="active")
        data = dict(
            member_pk=self.member.pk,
            expected_ledger_pk=current.pk,
            office=self.offices[index],
            lager=self.lagers[index],
            school=self.schools[index],
            subdivision=None,
            officer=self.officers[index],
            effective_date=self.today,
            reason="Workplace transfer",
        )
        data.update(overrides)
        return transfer_member(self.actor, **data)

    def payload(self, **overrides):
        data = {
            "member": self.member.pk,
            "source_ledger": self.member.ledgers.get(status="active").pk,
            "district": self.offices[1].district_id,
            "zonal_office": self.offices[1].pk,
            "lager": self.lagers[1].pk,
            "school": self.schools[1].pk,
            "subdivision": "",
            "maintenance_officer": self.officers[1].pk,
            "effective_date": self.today.isoformat(),
            "reason": "Workplace transfer",
        }
        data.update(overrides)
        return data

    def reader(self, name, district, *extra):
        user = get_user_model().objects.create_user(name)
        user.assigned_districts.add(district)
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=["view_memberledger", "view_membertransfer", *extra],
            )
        )
        from .test_support import assign_lagers

        assign_lagers(user, m.Lager.objects.filter(zonal_office__district=district))
        client = Client()
        client.force_login(user)
        return user, client

    def test_transfer_keeps_registration_and_financial_ids_and_creates_linked_receiving_ledger(
        self,
    ):
        self.member.refresh_from_db()
        before = {
            field.attname: getattr(self.member, field.attname)
            for field in self.member._meta.concrete_fields
            if field.name
            not in (
                "district",
                "office_location",
                "lager",
                "school",
                "subdivision",
                "updated_at",
            )
        }
        counts = (
            m.Member.objects.count(),
            m.PaymentTransaction.objects.count(),
            m.ChildDepositTransaction.objects.count(),
        )
        result = self.transfer()
        self.member.refresh_from_db()
        self.assertEqual(before, {name: getattr(self.member, name) for name in before})
        self.assertEqual(
            (
                m.Member.objects.count(),
                m.PaymentTransaction.objects.count(),
                m.ChildDepositTransaction.objects.count(),
            ),
            counts,
        )
        self.assertEqual(self.member.office_location_id, self.offices[1].pk)
        self.assertEqual(result.to_ledger.previous_ledger_id, self.original.pk)
        self.assertEqual(
            result.to_ledger.opening_balances["member_deposits"][0]["final_balance"],
            "1200.00",
        )
        self.assertEqual(
            result.to_ledger.opening_balances["child_deposits"][0]["balance"], "120.00"
        )
        self.original.refresh_from_db()
        self.assertEqual(
            self.original.closing_balances, result.to_ledger.opening_balances
        )
        self.assertEqual(self.original.status, "transferred")
        self.assertEqual(self.original.officer_epf, "EPF-0")
        self.assertEqual(result.to_ledger.officer_epf, "EPF-1")
        self.assertEqual(
            m.PaymentTransaction.objects.get(pk=self.old_entry.pk).reference,
            "BEFORE-TRANSFER",
        )
        self.assertEqual(
            self.original.registration_snapshot["office_location_id"],
            self.offices[0].pk,
        )

    def test_new_member_and_child_postings_belong_to_receiving_ledger(self):
        result = self.transfer()
        entry = m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.payment_type,
            transaction_date=self.today,
            credit=Decimal("50"),
            reference="AFTER-TRANSFER",
        )
        child_entry = m.ChildDepositTransaction.objects.create(
            deposit=self.child_account,
            transaction_date=self.today,
            credit=Decimal("10"),
            interest_rate=Decimal("8"),
        )
        for obj in (entry, child_entry):
            record = m.LedgerRecord.objects.get(
                source_model=obj._meta.label_lower, source_id=obj.pk
            )
            self.assertEqual(record.original_ledger_id, result.to_ledger_id)
        self.assertEqual(
            m.LedgerRecord.objects.get(
                source_model="members.paymenttransaction", source_id=self.old_entry.pk
            ).original_ledger_id,
            self.original.pk,
        )
        self.assertEqual(
            m.LedgerRecord.objects.get(
                source_model="members.childdeposittransaction",
                source_id=self.old_child_entry.pk,
            ).original_ledger_id,
            self.original.pk,
        )

    def test_original_office_retains_historical_access_without_receiving_activity(self):
        result = self.transfer()
        m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.payment_type,
            transaction_date=self.today,
            credit=Decimal("50"),
            reference="DESTINATION-ONLY",
        )
        _, source_client = self.reader(
            "source-reader",
            self.offices[0].district,
            "view_paymenttransaction",
            "view_childdeposit",
            "view_member",
        )
        response = source_client.get(
            reverse("member_ledger_detail", args=[self.original.pk])
        )
        self.assertContains(response, "BEFORE-TRANSFER")
        self.assertNotContains(response, "DESTINATION-ONLY")
        self.assertContains(response, "EPF-0")
        self.assertEqual(
            source_client.get(
                reverse("member_detail", args=[self.member.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            source_client.get(
                reverse("member_ledger_detail", args=[result.to_ledger_id])
            ).status_code,
            404,
        )
        _, destination = self.reader(
            "receiving-reader",
            self.offices[1].district,
            "view_paymenttransaction",
            "view_childdeposit",
        )
        response = destination.get(
            reverse("member_ledger_detail", args=[result.to_ledger_id]),
            {"include_previous": "on"},
        )
        self.assertContains(response, "BEFORE-TRANSFER")
        self.assertContains(response, "DESTINATION-ONLY")
        self.assertEqual(
            destination.get(
                reverse("member_ledger_detail", args=[self.original.pk])
            ).status_code,
            200,
        )

    def test_historical_financial_records_cannot_be_edited_or_deleted(self):
        self.transfer()
        self.old_entry.description = "Attempted rewrite"
        with self.assertRaises(ValidationError):
            self.old_entry.save()
        with self.assertRaises(ProtectedError):
            self.old_entry.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            m.PaymentTransaction.objects.filter(pk=self.old_entry.pk).delete()
        self.old_child_entry.credit = Decimal("999")
        with self.assertRaises(ValidationError):
            self.old_child_entry.save()

    def test_transfers_audits_and_ledger_pages_are_permanent(self):
        result = self.transfer()
        result.reason = "Changed"
        with self.assertRaises(ValidationError):
            result.save()
        for obj in (
            result,
            self.original,
            self.original.events.first(),
            self.original.officer_history.first(),
        ):
            with self.assertRaises(ProtectedError):
                obj.delete()
        with self.assertRaises(ProtectedError):
            m.MemberTransfer.objects.filter(pk=result.pk).delete()

    def test_historical_account_and_plan_records_cannot_be_deleted(self):
        self.transfer()
        for obj in (self.child, self.child_account, self.plan):
            with self.subTest(model=obj._meta.model_name):
                with self.assertRaises(ProtectedError), transaction.atomic():
                    type(obj).objects.filter(pk=obj.pk).delete()
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_responsibility_changes_keep_previous_name_and_epf(self):
        replacement = get_user_model().objects.create_user(
            "replacement", first_name="Replacement"
        )
        replacement.assigned_districts.add(self.offices[0].district)
        m.LedgerOfficerProfile.objects.create(
            user=replacement, epf_number="EPF-NEW"
        ).zonal_offices.add(self.offices[0])
        assign_ledger_officer(
            self.actor, self.original.pk, replacement, "Officer handover"
        )
        change = self.original.officer_history.latest("pk")
        self.assertEqual(change.previous_epf, "EPF-0")
        self.assertEqual(change.officer_epf, "EPF-NEW")
        self.assertEqual(change.reason, "Officer handover")
        result = self.transfer()
        with self.assertRaises(ValidationError):
            assign_ledger_officer(
                self.actor, self.original.pk, self.officers[0], "Rewrite original"
            )
        self.assertEqual(result.from_ledger.officer_epf, "EPF-NEW")

    def test_wrong_office_officer_inactive_officer_and_missing_epf_are_rejected_atomically(
        self,
    ):
        for officer in (self.officers[0], self.officers[2]):
            with self.assertRaises(ValidationError):
                self.transfer(officer=officer)
        self.officers[1].is_active = False
        self.officers[1].save()
        with self.assertRaises(ValidationError):
            self.transfer()
        self.officers[1].is_active = True
        self.officers[1].save()
        profile = self.officers[1].ledger_profile
        profile.epf_number = None
        # Simulate malformed legacy data; normal profile writes now prevent this.
        m.LedgerOfficerProfile.objects.filter(pk=profile.pk).update(epf_number=None)
        with self.assertRaises(ValidationError):
            self.transfer()
        self.assertEqual(m.MemberTransfer.objects.count(), 0)
        self.assertEqual(self.member.ledgers.count(), 1)
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, "active")

    def test_stale_transfer_source_and_same_location_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.transfer(expected_ledger_pk=self.original.pk + 9999)
        with self.assertRaises(ValidationError):
            self.transfer(index=0)
        self.transfer()
        with self.assertRaises(ValidationError):
            self.transfer(index=2, expected_ledger_pk=self.original.pk)
        self.assertEqual(m.MemberTransfer.objects.count(), 1)

    def test_only_safe_past_dates_are_allowed(self):
        with self.assertRaises(ValidationError):
            self.transfer(effective_date=self.today + timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.transfer(effective_date=self.today - timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.transfer(effective_date=date(2019, 1, 1))
        # The child was registered today, so earlier transfer would rewrite chronology.
        self.assertEqual(m.MemberTransfer.objects.count(), 0)

    def test_new_backdated_posting_cannot_be_inserted_into_a_closed_period(self):
        self.transfer()
        with self.assertRaises(ValidationError):
            m.PaymentTransaction.objects.create(
                member=self.member,
                payment_type=self.payment_type,
                transaction_date=self.today - timedelta(days=1),
                credit=Decimal("1"),
            )

    def test_current_entry_cannot_be_edited_to_predate_its_receiving_ledger(self):
        self.transfer()
        for obj in (
            m.PaymentTransaction.objects.create(
                member=self.member,
                payment_type=self.payment_type,
                transaction_date=self.today,
                credit=Decimal("50"),
            ),
            m.ChildDepositTransaction.objects.create(
                deposit=self.child_account,
                transaction_date=self.today,
                credit=Decimal("10"),
                interest_rate=Decimal("8"),
            ),
        ):
            with self.subTest(model=obj._meta.model_name):
                events_before = m.LedgerEvent.objects.count()
                obj.transaction_date = self.today - timedelta(days=1)
                with self.assertRaises(ValidationError):
                    obj.save()
                obj.refresh_from_db()
                self.assertEqual(obj.transaction_date, self.today)
                self.assertEqual(m.LedgerEvent.objects.count(), events_before)

    def test_changing_registered_office_bypassing_transfer_is_rejected(self):
        self.member.district = self.offices[1].district
        self.member.office_location = self.offices[1]
        self.member.school = self.schools[1]
        self.member.lager = self.lagers[1]
        with self.assertRaises(ValidationError):
            self.member.save()
        self.member.refresh_from_db()
        self.assertEqual(self.member.office_location_id, self.offices[0].pk)

    def test_transfer_form_and_detail_routes_work(self):
        self.assertEqual(
            self.client.get(
                reverse("member_transfer_create"), {"member": self.member.pk}
            ).status_code,
            200,
        )
        response = self.client.post(reverse("member_transfer_create"), self.payload())
        result = m.MemberTransfer.objects.get()
        self.assertRedirects(
            response, reverse("member_transfer_detail", args=[result.pk])
        )
        self.assertContains(
            self.client.get(
                reverse("member_ledger_detail", args=[result.to_ledger_id])
            ),
            self.original.reference,
        )

    def test_module_permissions_districts_and_financial_sections_are_separate(self):
        user, client = self.reader("metadata-only", self.offices[0].district)
        response = client.get(reverse("member_ledger_detail", args=[self.original.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BEFORE-TRANSFER")
        self.assertNotContains(response, "Transfer Child")
        self.assertEqual(
            client.post(reverse("member_transfer_create"), self.payload()).status_code,
            403,
        )
        user.user_permissions.add(Permission.objects.get(codename="add_membertransfer"))
        self.assertEqual(
            client.post(reverse("member_transfer_create"), self.payload()).status_code,
            200,
        )
        self.assertEqual(m.MemberTransfer.objects.count(), 0)
        self.assertEqual(Client().get(reverse("member_ledger_list")).status_code, 302)

    def test_user_epf_uniqueness_and_office_are_editable_in_administration(self):
        user = get_user_model().objects.create_user("new-officer")
        data = {
            "username": user.username,
            "first_name": "New",
            "last_name": "Officer",
            "is_active": "on",
            "districts": [self.offices[1].district_id],
            "epf_number": " epf-unique ",
            "zonal_offices": [self.offices[1].pk],
        }
        form = WorkspaceUserForm(data, actor=self.actor, instance=user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(
            m.LedgerOfficerProfile.objects.get(user=user).epf_number, "EPF-UNIQUE"
        )
        duplicate = WorkspaceUserForm(
            {**data, "epf_number": "EPF-1"}, actor=self.actor, instance=user
        )
        self.assertIn("epf_number", duplicate.errors)

    def test_export_keeps_historical_ledger_reference_and_does_not_leak_new_entries(
        self,
    ):
        result = self.transfer()
        m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.payment_type,
            transaction_date=self.today,
            credit=Decimal("10"),
            reference="NEW-LEDGER-ONLY",
        )
        _, client = self.reader(
            "export-source", self.offices[0].district, "view_paymenttransaction"
        )
        response = client.get(reverse("member_ledger_export", args=[self.original.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.original.reference)
        self.assertContains(response, "BEFORE-TRANSFER")
        self.assertNotContains(response, "NEW-LEDGER-ONLY")
        self.assertEqual(
            client.get(
                reverse("member_ledger_export", args=[result.to_ledger_id])
            ).status_code,
            404,
        )

    def test_safe_backdated_transfers_keep_a_complete_multi_office_chain(self):
        member = m.Member.objects.create(
            district=self.offices[0].district,
            office_location=self.offices[0],
            school=self.schools[0],
            lager=self.lagers[0],
            member_no="PAST-TRANSFER",
            full_name="Past transfer member",
            salary="0",
            office_address="Office",
            private_address="Home",
            phone_mobile="0771234567",
            nic_no="199012345677",
            gender="female",
            admit_date=date(2020, 1, 1),
        )
        original = member.ledgers.get(status="active")
        assign_ledger_officer(
            self.actor, original.pk, self.officers[0], "Known original officer"
        )
        first = transfer_member(
            self.actor,
            member_pk=member.pk,
            expected_ledger_pk=original.pk,
            office=self.offices[1],
            lager=self.lagers[1],
            school=self.schools[1],
            subdivision=None,
            officer=self.officers[1],
            effective_date=date(2025, 1, 1),
            reason="First workplace transfer",
        )
        m.PaymentTransaction.objects.create(
            member=member,
            payment_type=self.payment_type,
            transaction_date=date(2025, 1, 15),
            credit=Decimal("10"),
        )
        second = transfer_member(
            self.actor,
            member_pk=member.pk,
            expected_ledger_pk=first.to_ledger_id,
            office=self.offices[2],
            lager=self.lagers[2],
            school=self.schools[2],
            subdivision=None,
            officer=self.officers[2],
            effective_date=date(2025, 2, 1),
            reason="Second workplace transfer",
        )
        self.assertEqual(member.ledgers.count(), 3)
        self.assertEqual(member.ledgers.filter(status="active").count(), 1)
        self.assertEqual(second.to_ledger.previous_ledger_id, first.to_ledger_id)
        self.assertEqual(
            second.to_ledger.registration_snapshot["office_location_id"],
            self.offices[0].pk,
        )

    def test_loans_continue_under_receiving_ledger_and_original_outstanding_is_frozen(
        self,
    ):
        account = m.FixDepositAccount.objects.create(
            member=self.member,
            policy=m.FixDepositPolicy.objects.first(),
            cycle_started_on=date(2020, 1, 1),
        )
        loan = m.FixDepositLoan.objects.create(
            account=account,
            issued_on=date(2025, 1, 1),
            principal=Decimal("100"),
            interest_rate=Decimal("8"),
            deposit_balance_at_issue=Decimal("1000"),
            maximum_loan_at_issue=Decimal("900"),
        )
        result = self.transfer()
        m.FixLoanEntry.objects.create(
            loan=loan,
            kind="repayment",
            entry_date=self.today,
            principal_amount=Decimal("20"),
        )
        self.original.refresh_from_db()
        self.assertEqual(
            self.original.closing_balances["loans"][0]["principal_due"], "100.00"
        )
        record = m.LedgerRecord.objects.get(source_model="members.fixloanentry")
        self.assertEqual(record.original_ledger_id, result.to_ledger_id)

    def test_profile_at_departure_is_retained_after_receiving_office_edits(self):
        self.member.phone_mobile = "0772222222"
        self.member.save()
        self.transfer()
        self.member.refresh_from_db()
        self.member.phone_mobile = "0773333333"
        self.member.save()
        self.original.refresh_from_db()
        self.assertEqual(
            self.original.closing_balances["member_profile"]["phone_mobile"],
            "0772222222",
        )

    def test_school_transfer_within_the_same_zone_still_creates_a_new_ledger(self):
        school = m.School.objects.create(
            district=self.offices[0].district,
            zonal_office=self.offices[0],
            name="New school in same zone",
            code="NEW-SAME-ZONE",
        )
        result = self.transfer(index=0, school=school)
        self.assertNotEqual(result.from_ledger_id, result.to_ledger_id)
        self.assertEqual(result.to_ledger.zonal_office_id, self.offices[0].pk)
        self.assertEqual(result.to_ledger.school_id, school.pk)

    def test_late_annual_interest_posts_to_current_ledger_without_rewriting_original_closing(
        self,
    ):
        from .deposit_interest import save_interest_year, post_interest_year
        from .child_interest import save_child_interest_year, post_child_interest_year

        self.payment_type.interest_rate = Decimal("8")
        self.payment_type.save()
        result = self.transfer()
        record = save_interest_year(
            self.member, self.payment_type, date(2025, 1, 1), today=self.today
        )
        posted = post_interest_year(record.pk, actor=self.actor, today=self.today)
        self.assertIsNotNone(posted.ledger_transaction_id)
        mapped = m.LedgerRecord.objects.get(
            source_model="members.paymenttransaction",
            source_id=posted.ledger_transaction_id,
        )
        self.assertEqual(mapped.original_ledger_id, result.to_ledger_id)
        child_record = save_child_interest_year(
            self.child_account, date(2025, 1, 1), today=self.today
        )
        child_posted = post_child_interest_year(
            child_record.pk, actor=self.actor, today=self.today
        )
        mapped = m.LedgerRecord.objects.get(
            source_model="members.childdeposittransaction",
            source_id=child_posted.ledger_transaction_id,
        )
        self.assertEqual(mapped.original_ledger_id, result.to_ledger_id)
        self.original.refresh_from_db()
        self.assertEqual(
            self.original.closing_balances["member_deposits"][0]["final_balance"],
            "1200.00",
        )
        self.assertEqual(
            self.original.closing_balances["child_deposits"][0]["balance"], "120.00"
        )

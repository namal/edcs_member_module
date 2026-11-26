import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .lager_activity import activity_query, decorate_event


class LagerActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "lager-reporter", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.district = m.District.objects.get(code="COL")
        cls.user.assigned_districts.add(cls.district)
        cls.members = []
        cls.lagers = []
        for index, district in enumerate(
            (cls.district, cls.district, m.District.objects.get(code="KAN"))
        ):
            zone = m.OfficeLocation.objects.create(
                district=district, name=f"Report zone {index}", code=f"RZ-{index}"
            )
            lager = m.Lager.objects.create(
                zonal_office=zone, name=f"Report lager {index}", code=f"RL-{index}"
            )
            school = m.School.objects.create(
                district=district,
                zonal_office=zone,
                name=f"Report department {index}",
                code=f"RD-{index}",
            )
            member = m.Member.objects.create(
                district=district,
                office_location=zone,
                lager=lager,
                school=school,
                member_no=f"REPORT-{index}",
                full_name=f"Report Member {index}",
                salary="1000",
                nic_no=f"19901234567{index}",
                admit_date=date(2020, 1, 1),
            )
            cls.members.append(member)
            cls.lagers.append(lager)
        cls.member = cls.members[0]
        cls.lager = cls.lagers[0]
        cls.payment_type = m.PaymentType.objects.get(name="Fix Deposit")
        cls.opening = m.OpeningBalance.objects.create(
            member=cls.member, balance_date=date(2025, 1, 1)
        )
        m.OpeningBalanceAmount.objects.create(
            opening_balance=cls.opening, payment_type=cls.payment_type, amount="1000"
        )
        plan = m.PaymentPlan.objects.create(
            member=cls.member, agreement_date=date(2025, 1, 1)
        )
        m.PaymentPlanAmount.objects.create(
            plan=plan, payment_type=cls.payment_type, amount="50"
        )
        for member in cls.members:
            m.PaymentTransaction.objects.create(
                member=member,
                payment_type=cls.payment_type,
                transaction_date=date(2025, 2, 1),
                credit="100",
                reference=f"CREDIT-{member.pk}",
            )
        # Historical import fixtures deliberately use the base manager; live postings use audited saves.
        m.PaymentTransaction._base_manager.bulk_create(
            [
                m.PaymentTransaction(
                    member=cls.member,
                    payment_type=cls.payment_type,
                    transaction_date=date(2025, 3, 1),
                    debit=Decimal("40"),
                    reference="REPORT-DEBIT",
                )
            ]
        )
        posting = m.PaymentTransaction.objects.create(
            member=cls.member,
            payment_type=cls.payment_type,
            transaction_date=date(2025, 12, 31),
            credit="10",
            reference="REPORT-INTEREST",
        )
        m.DepositInterestYear.objects.create(
            member=cls.member,
            payment_type=cls.payment_type,
            year_start=date(2025, 1, 1),
            year_end=date(2025, 12, 31),
            annual_rate="8",
            calculated_through=date(2025, 12, 31),
            annual_interest="10",
            ledger_transaction=posting,
            posted_at=timezone.now(),
        )
        policy = m.FixDepositPolicy.objects.first()
        cls.account = m.FixDepositAccount.objects.create(
            member=cls.member, policy=policy, cycle_started_on=date(2020, 1, 1)
        )
        cls.loan = m.FixDepositLoan.objects.create(
            account=cls.account,
            issued_on=date(2025, 4, 1),
            principal="200",
            interest_rate="8",
            deposit_balance_at_issue="1060",
            maximum_loan_at_issue="954",
        )
        m.FixLoanEntry.objects.create(
            loan=cls.loan,
            kind="interest",
            entry_date=date(2025, 4, 2),
            interest_amount="5",
        )
        m.FixLoanEntry.objects.create(
            loan=cls.loan,
            kind="repayment",
            entry_date=date(2025, 5, 1),
            principal_amount="20",
            interest_amount="2",
        )
        debit = m.PaymentTransaction._base_manager.bulk_create(
            [
                m.PaymentTransaction(
                    member=cls.member,
                    payment_type=cls.payment_type,
                    transaction_date=date(2025, 6, 1),
                    debit=Decimal("30"),
                    reference="WITHDRAWAL-DEBIT",
                )
            ]
        )[0]
        m.FixDepositWithdrawal.objects.create(
            account=cls.account,
            requested_on=date(2025, 5, 30),
            amount="30",
            status="released",
            released_on=date(2025, 6, 1),
            balance_before="1060",
            balance_after="1030",
            maximum_at_release="795",
            ledger_transaction=debit,
            reviewed_at=timezone.now(),
        )
        cls.child = m.Child.objects.create(
            member=cls.member, name="Report Child", date_of_birth=date(2015, 1, 1)
        )
        cls.deposit = m.ChildDeposit.objects.create(
            child=cls.child,
            deposit_type=m.ChildDepositType.objects.first(),
            opened_on=date(2025, 1, 1),
            opening_amount="100",
        )
        m.ChildDepositTransaction.objects.create(
            deposit=cls.deposit,
            transaction_date=date(2025, 2, 1),
            credit="50",
            interest_rate="8",
        )
        m.ChildDepositTransaction.objects.create(
            deposit=cls.deposit,
            transaction_date=date(2025, 3, 1),
            debit="20",
            interest_rate="8",
        )
        m.ChildDepositInterestYear.objects.create(
            deposit=cls.deposit,
            year_start=date(2025, 1, 1),
            year_end=date(2025, 12, 31),
            annual_rate="8",
            calculated_through=date(2025, 12, 31),
            annual_interest="4",
        )
        from .test_support import assign_member_ledgers

        assign_member_ledgers(cls.user, cls.members)

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, export=False, lager=None):
        return reverse(
            "lager_activity_export" if export else "lager_activity",
            args=[(lager or self.lager).pk],
        )

    def report(self, **params):
        return self.client.get(self.url(), params)

    def grant(self, *codes):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codes
            )
        )

    def test_report_collects_all_member_and_child_financial_sources(self):
        response = self.report()
        self.assertEqual(response.status_code, 200)
        events = response.context["page_obj"]
        self.assertEqual(
            {row["event_source"] for row in events},
            {
                "member",
                "opening",
                "plan",
                "payment",
                "fix_account",
                "loan",
                "loan_entry",
                "withdrawal",
                "withdrawal_review",
                "child",
                "child_account",
                "child_payment",
                "interest",
                "child_interest",
            },
        )
        self.assertEqual({row["event_member_id"] for row in events}, {self.member.pk})
        self.assertContains(response, "Report Child")
        self.assertContains(response, "Principal LKR 20.00; interest LKR 2.00")
        self.assertNotContains(response, self.members[1].full_name)
        self.assertNotContains(response, self.members[2].full_name)

    def test_balances_do_not_double_count_interest_withdrawals_or_loan_principal(self):
        summaries = self.report().context["summaries"]
        self.assertEqual(
            summaries["member_deposits"],
            {
                "balance": Decimal("1040"),
                "credit": Decimal("110"),
                "debit": Decimal("70"),
            },
        )
        self.assertEqual(
            summaries["child_deposits"],
            {
                "balance": Decimal("130"),
                "credit": Decimal("50"),
                "debit": Decimal("20"),
            },
        )
        self.assertEqual(
            summaries["loans"],
            {"principal_due": Decimal("180"), "interest_due": Decimal("3")},
        )

    def test_date_range_has_period_movements_and_as_of_balances(self):
        response = self.report(date_from="2025-03-01", date_to="2025-03-31")
        self.assertTrue(response.context["form"].is_valid())
        self.assertEqual(
            {row["event_date"] for row in response.context["page_obj"]},
            {date(2025, 3, 1)},
        )
        self.assertEqual(
            response.context["summaries"]["member_deposits"],
            {
                "balance": Decimal("1060"),
                "credit": Decimal("0"),
                "debit": Decimal("40"),
            },
        )
        self.assertEqual(
            response.context["summaries"]["child_deposits"]["balance"], Decimal("130")
        )
        self.assertEqual(
            response.context["summaries"]["loans"]["principal_due"], Decimal("0")
        )

    def test_lager_only_permission_does_not_expose_member_or_financial_records(self):
        self.grant("view_lager")
        response = self.report()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["summaries"])
        self.assertFalse(response.context["page_obj"])
        self.assertNotContains(response, self.member.full_name)
        self.assertNotContains(response, self.child.name)

    def test_each_financial_module_keeps_its_own_view_permission(self):
        for permission, category, sources, summary in (
            ("view_paymenttransaction", "payments", {"payment"}, "member_deposits"),
            (
                "view_childdeposit",
                "child_deposits",
                {"child_account", "child_payment"},
                "child_deposits",
            ),
            (
                "view_fixdepositaccount",
                "loans",
                {
                    "fix_account",
                    "loan",
                    "loan_entry",
                    "withdrawal",
                    "withdrawal_review",
                },
                "loans",
            ),
        ):
            with self.subTest(permission=permission):
                self.grant("view_lager", permission)
                response = self.report()
                self.assertEqual(set(response.context["groups"]), {category})
                self.assertEqual(
                    {row["event_source"] for row in response.context["page_obj"]},
                    sources,
                )
                self.assertEqual(set(response.context["summaries"]), {summary})

    def test_add_permission_does_not_grant_financial_view(self):
        self.grant(
            "view_lager",
            "add_paymenttransaction",
            "add_childdeposit",
            "add_fixdepositaccount",
        )
        self.assertFalse(self.report().context["page_obj"])
        response = self.report(category="payments")
        self.assertIn("category", response.context["form"].errors)
        self.assertFalse(response.context["summaries"])

    def test_member_and_child_interest_use_quarterly_interest_permission(self):
        self.grant("view_lager", "view_depositinterestyear")
        response = self.report()
        self.assertEqual(
            {row["event_source"] for row in response.context["page_obj"]},
            {"interest", "child_interest"},
        )
        self.assertFalse(response.context["summaries"])

    def test_authentication_lager_permission_and_district_are_required_for_page_and_export(
        self,
    ):
        for export in (False, True):
            url = self.url(export)
            self.assertEqual(Client().get(url).status_code, 302)
            self.grant("view_paymenttransaction")
            self.assertEqual(self.client.get(url).status_code, 403)
            self.grant("view_lager", "view_paymenttransaction")
            self.assertEqual(
                self.client.get(self.url(export, self.lagers[2])).status_code, 404
            )

    def test_forged_member_and_invalid_dates_do_not_fall_back_to_all_members(self):
        for params in (
            {"member": self.members[1].pk},
            {"member": self.members[2].pk},
            {"member": "bad"},
            {"date_from": "bad"},
            {"date_from": "2025-04-01", "date_to": "2025-01-01"},
        ):
            with self.subTest(params=params):
                response = self.report(**params)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(response.context["page_obj"])
                self.assertFalse(response.context["summaries"])
                self.assertEqual(
                    self.client.get(self.url(True), params).status_code, 400
                )

    def test_export_covers_all_pages_and_escapes_spreadsheet_formulas(self):
        for index in range(30):
            m.PaymentTransaction._base_manager.bulk_create(
                [
                    m.PaymentTransaction(
                        member=self.member,
                        payment_type=self.payment_type,
                        transaction_date=date(2025, 2, 2),
                        credit="1",
                        reference="=unsafe()",
                        description=f"entry {index}",
                    )
                ]
            )
        params = {
            "category": "payments",
            "date_from": "2025-02-02",
            "date_to": "2025-02-02",
        }
        response = self.report(**params)
        self.assertEqual(len(response.context["page_obj"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.count, 30)
        exported = self.client.get(self.url(True), {**params, "page": 2})
        rows = list(
            csv.DictReader(
                io.StringIO(b"".join(exported.streaming_content).decode("utf-8-sig"))
            )
        )
        self.assertEqual(len(rows), 30)
        self.assertEqual({row["Reference"] for row in rows}, {"'=unsafe()"})
        self.assertEqual({row["Lager code"] for row in rows}, {self.lager.code})

    def test_current_lager_assignment_carries_member_and_child_history(self):
        # Both lagers in this test share a zone, so the member hierarchy stays valid.
        other = m.Lager.objects.create(
            zonal_office=self.member.office_location,
            name="Transferred lager",
            code="TRANSFER-L",
            maintenance_officer=self.user,
        )
        self.member.lager = other
        self.member.save()
        self.assertFalse(self.report().context["page_obj"])
        moved = self.client.get(self.url(lager=other))
        self.assertContains(moved, self.child.name)
        self.assertEqual(
            moved.context["summaries"]["member_deposits"]["balance"], Decimal("1040")
        )
        self.assertEqual(
            moved.context["summaries"]["child_deposits"]["balance"], Decimal("130")
        )

    def test_inactive_members_and_empty_lagers_are_included_correctly(self):
        self.member.status = "inactive"
        self.member.save()
        self.assertContains(self.report(), self.member.full_name)
        empty = m.Lager.objects.create(
            zonal_office=self.member.office_location,
            name="Empty lager",
            code="EMPTY-L",
            maintenance_officer=self.user,
        )
        response = self.client.get(self.url(lager=empty))
        self.assertEqual(response.context["member_count"], 0)
        self.assertFalse(response.context["page_obj"])

    def test_all_event_links_resolve_to_their_authorized_source_records(self):
        members = m.Member.objects.filter(pk=self.member.pk)
        for row in activity_query(members, self.report().context["groups"]):
            row = decorate_event(row)
            with self.subTest(source=row["event_source"]):
                self.assertEqual(self.client.get(row["url"]).status_code, 200)

    def test_lager_list_and_member_profile_link_to_activity(self):
        self.assertContains(self.client.get(reverse("lager_list")), self.url())
        self.assertContains(
            self.client.get(reverse("member_detail", args=[self.member.pk])),
            self.url() + f"?member={self.member.pk}",
        )

    def test_lager_totals_include_multiple_members_and_member_filter_is_independent(
        self,
    ):
        member = m.Member.objects.create(
            district=self.district,
            office_location=self.member.office_location,
            school=self.member.school,
            lager=self.lager,
            member_no="REPORT-SECOND",
            full_name="Second member in same lager",
            salary="0",
            nic_no="199012345699",
            admit_date=date(2020, 1, 1),
        )
        opening = m.OpeningBalance.objects.create(
            member=member, balance_date=date(2025, 1, 1)
        )
        m.OpeningBalanceAmount.objects.create(
            opening_balance=opening, payment_type=self.payment_type, amount="10"
        )
        m.PaymentTransaction.objects.create(
            member=member,
            payment_type=self.payment_type,
            transaction_date=date(2025, 2, 1),
            credit="5",
        )
        child = m.Child.objects.create(
            member=member, name="Second parent child", date_of_birth=date(2015, 1, 1)
        )
        m.ChildDeposit.objects.create(
            child=child,
            deposit_type=self.deposit.deposit_type,
            opened_on=date(2025, 1, 1),
            opening_amount="20",
        )
        all_members = self.report()
        self.assertEqual(all_members.context["member_count"], 2)
        self.assertEqual(
            all_members.context["summaries"]["member_deposits"]["balance"],
            Decimal("1055"),
        )
        self.assertEqual(
            all_members.context["summaries"]["child_deposits"]["balance"],
            Decimal("150"),
        )
        selected = self.report(member=member.pk)
        self.assertEqual(
            {row["event_member_id"] for row in selected.context["page_obj"]},
            {member.pk},
        )
        self.assertEqual(
            selected.context["summaries"]["member_deposits"]["balance"], Decimal("15")
        )
        self.assertEqual(
            selected.context["summaries"]["child_deposits"]["balance"], Decimal("20")
        )

    def test_loan_settlement_event_and_repayments_are_reported(self):
        m.FixLoanEntry.objects.create(
            loan=self.loan,
            kind="repayment",
            entry_date=date(2025, 12, 1),
            principal_amount="180",
            interest_amount="3",
        )
        self.loan.settled_on = date(2025, 12, 1)
        self.loan.save()
        response = self.report(category="loans")
        self.assertContains(response, "Loan settled")
        self.assertEqual(
            response.context["summaries"]["loans"],
            {"principal_due": Decimal("0"), "interest_due": Decimal("0")},
        )

    def test_export_retains_financial_permission_boundaries(self):
        self.grant("view_lager", "view_paymenttransaction")
        response = self.client.get(self.url(True), {"member": self.member.pk})
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(len(rows), 4)
        self.assertNotIn(self.child.name, content)
        self.assertEqual({row["Member no"] for row in rows}, {self.member.member_no})
        self.assertEqual(
            self.client.get(self.url(True), {"category": "loans"}).status_code, 400
        )

    def test_released_withdrawal_uses_financial_release_date(self):
        response = self.report(
            category="loans", date_from="2025-06-01", date_to="2025-06-01"
        )
        events = response.context["page_obj"]
        self.assertEqual(
            [row["event_label"] for row in events], ["Withdrawal released"]
        )
        self.assertEqual(events[0]["event_date"], date(2025, 6, 1))
        self.assertEqual(
            response.context["summaries"]["member_deposits"]["debit"], Decimal("30")
        )

    def test_lager_only_operator_can_load_district_locations(self):
        self.grant("add_lager")
        self.assertEqual(
            self.client.get(
                reverse("location_options"), {"district": self.district.pk}
            ).status_code,
            200,
        )

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from . import models as m
from .forms import LagerForm, MemberForm
from .test_support import officer_for


class LedgerMasterOfficerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .tests import MemberWorkflowTests

        MemberWorkflowTests.setUpTestData.__func__(cls)
        cls.first = officer_for(cls.office)
        cls.second = officer_for(cls.other_office)
        cls.first.first_name, cls.first.last_name = "First", "Officer"
        cls.first.save()
        cls.second.first_name, cls.second.last_name = "Second", "Officer"
        cls.second.save()
        cls.lager = m.Lager.objects.create(
            zonal_office=cls.office,
            name="Assigned ledger",
            code="ASSIGNED-L",
            maintenance_officer=cls.second,
        )
        cls.school = m.School.objects.create(
            district=cls.district,
            zonal_office=cls.office,
            name="Assigned department",
            code="ASSIGNED-D",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def member_payload(self, **overrides):
        from .tests import MemberWorkflowTests

        return MemberWorkflowTests.payload(
            self, lager=self.lager.pk, school=self.school.pk, **overrides
        )

    def master_payload(self, **overrides):
        data = dict(
            district=self.district.pk,
            zonal_office=self.office.pk,
            name="New ledger",
            code="NEW-L",
            maintenance_officer=self.second.pk,
            is_active="on",
        )
        data.update(overrides)
        return data

    def test_all_active_officers_show_epf_and_name_before_location_selection(self):
        missing = get_user_model().objects.create_user("no-epf")
        inactive = get_user_model().objects.create_user(
            "inactive-officer", is_active=False
        )
        m.LedgerOfficerProfile.objects.create(user=inactive, epf_number="INACTIVE-EPF")
        for form in (MemberForm(user=self.user), LagerForm(user=self.user)):
            ids = set(
                form.fields["maintenance_officer"].queryset.values_list("pk", flat=True)
            )
            self.assertTrue({self.first.pk, self.second.pk} <= ids)
            self.assertNotIn(missing.pk, ids)
            self.assertNotIn(inactive.pk, ids)
        page = self.client.get(reverse("member_create"))
        self.assertContains(page, "Second Officer")
        self.assertContains(page, self.second.ledger_profile.epf_number)

    def test_registration_assigns_master_officer_without_javascript_or_officer_input(
        self,
    ):
        from .test_support import membership_approval_policy

        membership_approval_policy()
        data = self.member_payload()
        data.pop("maintenance_officer")
        response = self.client.post(reverse("member_create"), data)
        self.assertEqual(response.status_code, 302)
        ledger = m.MemberLedger.objects.get(member__member_no="M-001")
        self.assertEqual(ledger.maintenance_officer_id, self.second.pk)
        self.assertEqual(ledger.officer_name, "Second Officer")
        self.assertEqual(ledger.officer_epf, self.second.ledger_profile.epf_number)
        self.assertEqual(ledger.officer_history.get().officer_id, self.second.pk)

    def test_tampered_officer_selection_cannot_override_lager_assignment(self):
        form = MemberForm(
            self.member_payload(maintenance_officer=self.first.pk), user=self.user
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["maintenance_officer"], self.second)
        self.assertEqual(
            form.save().ledgers.get(status="active").maintenance_officer_id,
            self.second.pk,
        )

    def test_legacy_unassigned_master_requires_setup_before_registration(self):
        m.Lager.objects.filter(pk=self.lager.pk).update(maintenance_officer=None)
        # Do not use the shared fixture helper, which assigns missing fixture masters.
        data = self.member_payload()
        m.Lager.objects.filter(pk=self.lager.pk).update(maintenance_officer=None)
        form = MemberForm(data, user=self.user)
        self.assertIn("lager", form.errors)
        self.assertIn("Master data", str(form.errors))
        self.assertFalse(m.Member.objects.exists())

    def test_inactive_assigned_officer_blocks_registration(self):
        self.second.is_active = False
        self.second.save()
        form = MemberForm(self.member_payload(), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("lager", form.errors)

    def test_master_requires_valid_profile_epf_on_create_and_update(self):
        no_epf = get_user_model().objects.create_user("missing-epf-officer")
        m.LedgerOfficerProfile.objects.create(user=no_epf)
        for instance in (None, self.lager):
            for value in ("", no_epf.pk):
                with self.subTest(instance=bool(instance), officer=value):
                    form = LagerForm(
                        self.master_payload(
                            maintenance_officer=value, officer_epf="FORGED-EPF"
                        ),
                        instance=instance,
                        user=self.user,
                    )
                    self.assertFalse(form.is_valid())
                    self.assertIn("maintenance_officer", form.errors)
                    self.assertIn("officer_epf", form.errors)

    def test_master_displays_authoritative_epf_and_allows_cross_zone_officer(self):
        form = LagerForm(self.master_payload(officer_epf="FORGED-EPF"), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["officer_epf"], self.second.ledger_profile.epf_number
        )
        saved = form.save()
        self.assertEqual(saved.maintenance_officer_id, self.second.pk)
        self.assertContains(
            self.client.get(reverse("lager_edit", args=[saved.pk])),
            self.second.ledger_profile.epf_number,
        )
        self.assertContains(self.client.get(reverse("lager_list")), "Second Officer")

    def test_changing_master_does_not_rewrite_existing_member_responsibility(self):
        form = MemberForm(self.member_payload(), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        ledger = form.save().ledgers.get(status="active")
        self.lager.maintenance_officer = self.first
        self.lager.save()
        ledger.refresh_from_db()
        self.assertEqual(ledger.maintenance_officer_id, self.second.pk)
        data = self.member_payload(member_no="M-002", nic_no="199012345679")
        form = MemberForm(data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.save().ledgers.get(status="active").maintenance_officer_id,
            self.first.pk,
        )

    def test_advanced_master_form_also_requires_epf(self):
        data = self.master_payload(maintenance_officer="")
        page = self.client.post(reverse("admin:members_lager_add"), data)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(m.Lager.objects.filter(code="NEW-L").exists())
        response = self.client.post(
            reverse("admin:members_lager_add"), self.master_payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            m.Lager.objects.filter(
                code="NEW-L", maintenance_officer=self.second
            ).exists()
        )

    def test_admin_master_edits_skip_approval_while_operator_edits_queue(self):
        operator = get_user_model().objects.create_user("master-operator")
        operator.assigned_districts.add(self.district)
        operator.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=["view_lager", "change_lager"],
            )
        )
        policy = m.ApprovalHierarchy.objects.create(
            name="Master edits", module="lager", action="change"
        )
        level = m.ApprovalLevel.objects.create(
            hierarchy=policy, position=1, name="Review"
        )
        level.approvers.add(self.first)
        response = self.client.post(
            reverse("lager_edit", args=[self.lager.pk]),
            self.master_payload(name="Admin setup"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(m.ApprovalRequest.objects.exists())
        self.lager.refresh_from_db()
        self.assertEqual(self.lager.name, "Admin setup")
        from .test_support import assign_lagers

        assign_lagers(operator, [self.lager])
        self.client.force_login(operator)
        response = self.client.post(
            reverse("lager_edit", args=[self.lager.pk]),
            self.master_payload(name="Needs review"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(m.ApprovalRequest.objects.get().status, "pending")
        self.lager.refresh_from_db()
        self.assertEqual(self.lager.name, "Admin setup")

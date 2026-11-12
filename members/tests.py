import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import LagerForm, MemberForm, OfficeLocationForm
from .models import District, Lager, Member, OfficeLocation, School


class MemberWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", password="test-only-pass", is_staff=True
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.district = District.objects.get(code="COL")
        cls.other_district = District.objects.get(code="KAN")
        cls.office = OfficeLocation.objects.create(
            district=cls.district,
            name="Test Office",
            code="TEST-COL",
            address="1 Test Road",
            phone="0112345678",
        )
        cls.other_office = OfficeLocation.objects.create(
            district=cls.other_district, name="Kandy Test", code="TEST-KAN"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        data = {
            "district": self.district.pk,
            "office_location": self.office.pk,
            "member_no": "m-001",
            "computer_no": "",
            "full_name": "Test Member",
            "salary": "85000.50",
            "private_address": "2 Test Lane",
            "office_address": "1 Test Road",
            "phone_mobile": "0771234567",
            "phone_office": "",
            "phone_home": "",
            "admit_date": timezone.localdate().isoformat(),
            "gender": "female",
            "nic_no": "199012345678",
            "status": "active",
            "subdivision": "",
        }
        data.update(overrides)
        district_id = data["district"]
        if "lager" not in data:
            lager = Lager.objects.filter(district_id=district_id).first()
            if lager is None:
                lager = Lager.objects.create(
                    district_id=district_id,
                    name="Default Lager",
                    code=f"DEFAULT-L-{district_id}",
                )
            data["lager"] = lager.pk
        if "school" not in data:
            office = OfficeLocation.objects.filter(
                pk=data["office_location"], district_id=district_id
            ).first()
            office = (
                office or OfficeLocation.objects.filter(district_id=district_id).first()
            )
            school = School.objects.filter(
                district_id=district_id, zonal_office=office
            ).first()
            if school is None:
                school = School.objects.create(
                    district_id=district_id,
                    zonal_office=office,
                    name="Default School",
                    code=f"DEFAULT-S-{district_id}",
                )
            data["school"] = school.pk
        return data

    def create_member(self, **overrides):
        form = MemberForm(data=self.payload(**overrides))
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_login_required_for_all_data_endpoints(self):
        anonymous = Client()
        for name in [
            "dashboard",
            "member_list",
            "member_create",
            "member_export",
            "district_list",
            "district_create",
            "office_list",
            "office_create",
            "office_options",
        ]:
            with self.subTest(name=name):
                self.assertEqual(anonymous.get(reverse(name)).status_code, 302)

    def test_valid_registration_persists_and_normalizes_identifiers(self):
        response = self.client.post(reverse("member_create"), self.payload())
        member = Member.objects.get()
        self.assertRedirects(response, reverse("member_detail", args=[member.pk]))
        self.assertEqual(member.member_no, "M-001")
        self.assertEqual(member.salary, Decimal("85000.50"))
        self.assertIsNone(member.computer_no)

    def test_posted_office_is_derived_from_selected_school(self):
        form = MemberForm(self.payload(office_location=self.other_office.pk))
        self.assertTrue(form.is_valid(), form.errors)
        member = form.save()
        self.assertEqual(member.office_location, self.office)
        self.assertEqual(member.district, self.district)

    def test_future_date_negative_salary_and_invalid_identifiers_rejected(self):
        form = MemberForm(
            self.payload(
                admit_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
                salary="-1",
                nic_no="invalid",
                phone_mobile="123",
            )
        )
        self.assertFalse(form.is_valid())
        for field in ["admit_date", "salary", "nic_no", "phone_mobile"]:
            self.assertIn(field, form.errors)

    def test_duplicate_identifiers_rejected_case_insensitively(self):
        self.create_member(computer_no="pc-001", nic_no="901234567v")
        form = MemberForm(
            self.payload(member_no="m-001", computer_no="PC-001", nic_no="901234567V")
        )
        self.assertFalse(form.is_valid())
        for field in ["member_no", "computer_no", "nic_no"]:
            self.assertIn(field, form.errors)

    def test_multiple_blank_computer_numbers_allowed(self):
        self.create_member()
        self.create_member(member_no="M-002", nic_no="199012345679")
        self.assertEqual(Member.objects.filter(computer_no__isnull=True).count(), 2)

    def test_inactive_office_not_available_for_registration(self):
        self.office.is_active = False
        self.office.save()
        form = MemberForm(self.payload())
        self.assertFalse(form.is_valid())
        self.assertIn("office_location", form.errors)

    def test_inactive_district_not_available_for_registration(self):
        self.district.is_active = False
        self.district.save()
        self.assertFalse(MemberForm(self.payload()).is_valid())
        data = self.client.get(
            reverse("office_options"), {"district": self.district.pk}
        ).json()
        self.assertEqual(data["offices"], [])

    def test_existing_member_in_inactive_office_can_be_updated(self):
        member = self.create_member()
        self.office.is_active = False
        self.office.save()
        self.district.is_active = False
        self.district.save()
        response = self.client.post(
            reverse("member_edit", args=[member.pk]),
            self.payload(full_name="Updated Member"),
        )
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.full_name, "Updated Member")

    def test_office_cannot_move_district_with_registered_members(self):
        self.create_member()
        form = OfficeLocationForm(
            data={
                "district": self.other_district.pk,
                "name": self.office.name,
                "code": self.office.code,
                "is_active": True,
            },
            instance=self.office,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("district", form.errors)

    def test_office_api_returns_only_active_offices_in_requested_district(self):
        response = self.client.get(
            reverse("office_options"), {"district": self.district.pk}
        )
        self.assertEqual(
            [row["id"] for row in response.json()["offices"]], [self.office.pk]
        )
        self.assertEqual(
            self.client.get(reverse("office_options"), {"district": "invalid"}).json()[
                "offices"
            ],
            [],
        )

    def test_search_filters_and_csv_match(self):
        self.create_member(full_name="Asha Test")
        self.create_member(
            member_no="M-002",
            nic_no="199012345679",
            full_name="Nimal Test",
            district=self.other_district.pk,
            office_location=self.other_office.pk,
        )
        for params in [
            {"q": "Asha"},
            {"district": self.district.pk},
            {"office": self.office.pk},
        ]:
            response = self.client.get(reverse("member_list"), params)
            self.assertContains(response, "Asha Test")
            self.assertNotContains(response, "Nimal Test")
            export = self.client.get(reverse("member_export"), params)
            rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][2], "Asha Test")

    def test_csv_neutralizes_spreadsheet_formulas(self):
        self.create_member(full_name='=HYPERLINK("test")')
        response = self.client.get(reverse("member_export"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertTrue(rows[1][2].startswith("'="))

    def test_delete_get_is_read_only_and_post_is_required(self):
        member = self.create_member()
        url = reverse("member_delete", args=[member.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Member.objects.filter(pk=member.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("member_list"))
        self.assertFalse(Member.objects.filter(pk=member.pk).exists())

    def test_linked_masters_are_protected_from_deletion(self):
        self.create_member()
        self.client.post(reverse("office_delete", args=[self.office.pk]))
        self.client.post(reverse("district_delete", args=[self.district.pk]))
        self.assertTrue(OfficeLocation.objects.filter(pk=self.office.pk).exists())
        self.assertTrue(District.objects.filter(pk=self.district.pk).exists())

    def test_csrf_required_for_mutations(self):
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.user)
        response = secure_client.post(reverse("member_create"), self.payload())
        self.assertEqual(response.status_code, 403)

    def test_dashboard_and_all_templates_render_with_populated_data(self):
        member = self.create_member()
        urls = [
            reverse("dashboard"),
            reverse("member_list"),
            reverse("member_create"),
            reverse("district_list"),
            reverse("office_list"),
            reverse("district_create"),
            reverse("office_create"),
            reverse("member_detail", args=[member.pk]),
            reverse("member_edit", args=[member.pk]),
            reverse("district_edit", args=[self.district.pk]),
            reverse("office_edit", args=[self.office.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["total_members"], 1)
        self.assertEqual(dashboard.context["this_month"], 1)
        self.assertEqual(dashboard.context["chart_total"], 1)

    def test_invalid_filter_and_pagination_values_do_not_crash(self):
        for params in [
            {
                "district": "invalid",
                "office": "-1",
                "from": "not-a-date",
                "page": "bad",
            },
            {"page": "999"},
            {"status": "unknown", "gender": "unknown", "to": "2026-99-99"},
        ]:
            self.assertEqual(
                self.client.get(reverse("member_list"), params).status_code, 200
            )

    def test_master_records_can_be_created_and_updated(self):
        response = self.client.post(
            reverse("district_create"),
            {
                "name": "Test District",
                "code": "TST",
                "province": "Test Province",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        district = District.objects.get(code="TST")
        response = self.client.post(
            reverse("office_create"),
            {
                "district": district.pk,
                "name": "Test Branch",
                "code": "TST-01",
                "address": "Test Address",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        office = OfficeLocation.objects.get(code="TST-01")
        response = self.client.post(
            reverse("office_edit", args=[office.pk]),
            {
                "district": district.pk,
                "name": "Updated Branch",
                "code": "TST-01",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        office.refresh_from_db()
        self.assertEqual(office.name, "Updated Branch")

    def make_school(self, **overrides):
        from .models import School

        data = {
            "district": self.district,
            "zonal_office": self.office,
            "name": "Central School",
            "code": "SCH-001",
            "address": "School Road",
        }
        data.update(overrides)
        return School.objects.create(**data)

    def test_district_province_and_member_office_address_are_required(self):
        from .forms import DistrictForm

        self.assertIn(
            "province",
            DistrictForm(
                {"name": "Example", "code": "EX", "province": "", "is_active": True}
            ).errors,
        )
        for address in ["", "   "]:
            with self.subTest(address=address):
                self.assertIn(
                    "office_address",
                    MemberForm(self.payload(office_address=address)).errors,
                )

    def test_registration_without_subdivision_is_valid(self):
        member = self.create_member()
        self.assertIsNone(member.subdivision_id)
        self.assertIsNotNone(member.school_id)
        self.assertIsNotNone(member.lager_id)

    def test_location_form_uses_initial_district_and_prefixed_post_data(self):
        initial = MemberForm(initial={"district": self.district.pk})
        self.assertQuerySetEqual(
            initial.fields["office_location"].queryset, [self.office]
        )
        data = {f"member-{key}": value for key, value in self.payload().items()}
        form = MemberForm(data, prefix="member")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form["province"].value(), self.district.province)

    def test_subdivision_must_belong_to_selected_zonal_office(self):
        from .models import Subdivision

        subdivision = Subdivision.objects.create(
            zonal_office=self.other_office, name="Other Division", code="DIV-OTHER"
        )
        form = MemberForm(self.payload(subdivision=subdivision.pk))
        self.assertIn("subdivision", form.errors)

    def test_school_label_includes_full_location_and_subdivision_is_optional(self):
        from .models import Subdivision

        school = self.make_school()
        expected = f"Central School · {self.district.province_display} · {self.district.name} · {self.office.name}"
        self.assertEqual(str(school), expected)
        subdivision = Subdivision.objects.create(
            zonal_office=self.office, name="North Division", code="DIV-NORTH"
        )
        school.subdivision = subdivision
        school.save()
        self.assertEqual(str(school), f"{expected} · North Division")
        member = self.create_member(school=school.pk, subdivision=subdivision.pk)
        self.assertEqual(member.school, school)
        self.assertEqual(member.subdivision, subdivision)

    def test_school_must_match_member_location(self):
        from .models import Subdivision

        school = self.make_school(
            district=self.other_district, zonal_office=self.other_office
        )
        self.assertIn("lager", MemberForm(self.payload(school=school.pk)).errors)
        school = self.make_school(
            code="SCH-002",
            subdivision=Subdivision.objects.create(
                zonal_office=self.office, name="North", code="NORTH"
            ),
        )
        form = MemberForm(self.payload(school=school.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().subdivision_id)

    def test_school_master_rejects_mismatched_hierarchy(self):
        from .forms import SchoolForm, SubdivisionForm
        from .models import Subdivision

        subdivision = Subdivision.objects.create(
            zonal_office=self.other_office, name="Other", code="DIV-OTHER"
        )
        data = {
            "district": self.district.pk,
            "zonal_office": self.other_office.pk,
            "name": "School",
            "code": "S-1",
            "is_active": True,
        }
        self.assertIn("zonal_office", SchoolForm(data).errors)
        self.assertIn("zonal_office", SubdivisionForm(data).errors)
        data.update(zonal_office=self.office.pk, subdivision=subdivision.pk)
        self.assertIn("subdivision", SchoolForm(data).errors)

    def test_inactive_school_and_subdivision_unavailable_for_new_members(self):
        from .models import Subdivision

        school = self.make_school(is_active=False)
        self.assertIn("school", MemberForm(self.payload(school=school.pk)).errors)
        subdivision = Subdivision.objects.create(
            zonal_office=self.office,
            name="Inactive",
            code="DIV-INACTIVE",
            is_active=False,
        )
        self.assertIn(
            "subdivision", MemberForm(self.payload(subdivision=subdivision.pk)).errors
        )

    def test_existing_inactive_school_and_subdivision_are_preserved_on_edit(self):
        from .models import Subdivision

        subdivision = Subdivision.objects.create(
            zonal_office=self.office, name="North", code="DIV-NORTH"
        )
        school = self.make_school(subdivision=subdivision)
        member = self.create_member(school=school.pk, subdivision=subdivision.pk)
        school.is_active = subdivision.is_active = False
        school.save()
        subdivision.save()
        form = MemberForm(
            self.payload(school=school.pk, subdivision=subdivision.pk), instance=member
        )
        self.assertTrue(form.is_valid(), form.errors)
        response = self.client.get(reverse("member_edit", args=[member.pk]))
        self.assertContains(response, str(school))
        self.assertEqual(
            response.context["initial_locations"]["schools"][0]["id"], school.pk
        )

    def test_location_api_filters_district_and_inactive_records(self):
        from .models import Subdivision

        school = self.make_school()
        self.make_school(
            code="SCH-OTHER",
            district=self.other_district,
            zonal_office=self.other_office,
        )
        subdivision = Subdivision.objects.create(
            zonal_office=self.office, name="North", code="NORTH"
        )
        self.make_school(
            code="SCH-HIDDEN",
            subdivision=Subdivision.objects.create(
                zonal_office=self.office, name="Hidden", code="HIDDEN", is_active=False
            ),
        )
        data = self.client.get(
            reverse("location_options"), {"district": self.district.pk}
        ).json()
        self.assertEqual([row["id"] for row in data["offices"]], [self.office.pk])
        self.assertEqual([row["id"] for row in data["subdivisions"]], [subdivision.pk])
        self.assertEqual([row["id"] for row in data["schools"]], [school.pk])
        self.assertEqual(data["schools"][0]["name"], str(school))
        for district in ["", "bad", self.other_district.pk]:
            data = self.client.get(
                reverse("location_options"), {"district": district}
            ).json()
            self.assertNotIn(self.office.pk, [row["id"] for row in data["offices"]])
        self.assertEqual(Client().get(reverse("location_options")).status_code, 302)

    def test_school_and_subdivision_master_crud_filters_and_district_counts(self):
        from .models import School, Subdivision

        response = self.client.post(
            reverse("subdivision_create"),
            {
                "district": self.district.pk,
                "zonal_office": self.office.pk,
                "name": "North",
                "code": "div-1",
                "is_active": True,
            },
        )
        self.assertRedirects(response, reverse("subdivision_list"))
        subdivision = Subdivision.objects.get(code="DIV-1")
        data = {
            "district": self.district.pk,
            "zonal_office": self.office.pk,
            "subdivision": subdivision.pk,
            "name": "School One",
            "code": "sch-1",
            "is_active": True,
        }
        self.assertRedirects(
            self.client.post(reverse("school_create"), data), reverse("school_list")
        )
        school = School.objects.get(code="SCH-1")
        self.make_school(code="SCH-2")
        self.make_school(
            code="SCH-OTHER",
            district=self.other_district,
            zonal_office=self.other_office,
        )
        for params in [{"district": self.district.pk}, {"q": "SCH-1"}]:
            response = self.client.get(reverse("school_list"), params)
            self.assertContains(response, "School One")
            self.assertNotContains(response, "SCH-OTHER")
        district = next(
            district
            for district in self.client.get(
                reverse("district_list"), {"q": self.district.name}
            ).context["page_obj"]
            if district.pk == self.district.pk
        )
        self.assertEqual(district.school_count, 2)
        data.update(name="Updated School", subdivision="")
        self.assertRedirects(
            self.client.post(reverse("school_edit", args=[school.pk]), data),
            reverse("school_list"),
        )
        school.refresh_from_db()
        self.assertIsNone(school.subdivision_id)
        self.assertRedirects(
            self.client.post(reverse("school_delete", args=[school.pk])),
            reverse("school_list"),
        )
        self.assertFalse(School.objects.filter(pk=school.pk).exists())
        for name in [
            "school_create",
            "subdivision_create",
            "school_list",
            "subdivision_list",
        ]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("subdivision_edit", args=[subdivision.pk])
            ).status_code,
            200,
        )

    def test_linked_location_records_cannot_move_or_be_deleted(self):
        from .forms import SchoolForm, SubdivisionForm
        from .models import School, Subdivision

        subdivision = Subdivision.objects.create(
            zonal_office=self.office, name="North", code="NORTH"
        )
        school = self.make_school(subdivision=subdivision)
        self.create_member(school=school.pk, subdivision=subdivision.pk)
        self.client.post(reverse("school_delete", args=[school.pk]))
        self.client.post(reverse("subdivision_delete", args=[subdivision.pk]))
        self.assertTrue(School.objects.filter(pk=school.pk).exists())
        self.assertTrue(Subdivision.objects.filter(pk=subdivision.pk).exists())
        form = SubdivisionForm(
            {
                "district": self.other_district.pk,
                "zonal_office": self.other_office.pk,
                "name": "North",
                "code": "NORTH",
                "is_active": True,
            },
            instance=subdivision,
        )
        self.assertIn("zonal_office", form.errors)
        form = SchoolForm(
            {
                "district": self.other_district.pk,
                "zonal_office": self.other_office.pk,
                "name": school.name,
                "code": school.code,
                "is_active": True,
            },
            instance=school,
        )
        self.assertIn("district", form.errors)

    def test_school_export_and_member_profile_show_full_location(self):
        school = self.make_school()
        member = self.create_member(school=school.pk)
        self.assertContains(
            self.client.get(reverse("member_detail", args=[member.pk])), str(school)
        )
        row = next(
            csv.DictReader(
                io.StringIO(
                    self.client.get(reverse("member_export")).content.decode(
                        "utf-8-sig"
                    )
                )
            )
        )
        self.assertEqual(row["Province"], self.district.province)
        self.assertEqual(row["Subdivision"], "")
        self.assertEqual(row["Department"], str(school))
        self.assertEqual(row["Province Short Name"], self.district.province_short_name)

    def test_department_labels_in_registration_master_data_and_permissions(self):
        response = self.client.get(reverse("member_create"))
        self.assertEqual(response.context["form"].fields["school"].label, "Department")
        self.assertContains(response, "Department dropdown")
        self.assertIn(
            "Departments",
            [module["label"] for module in response.context["master_modules"]],
        )
        response = self.client.get(reverse("school_list"))
        self.assertEqual(response.context["title"], "Departments")
        self.assertEqual(response.context["singular"], "Department")
        response = self.client.get(reverse("school_create"))
        self.assertEqual(response.context["title"], "Add department")
        for action in ("add", "change", "delete", "view"):
            permission = Permission.objects.get(
                content_type__app_label="members", codename=f"{action}_school"
            )
            self.assertEqual(permission.name, f"Can {action} department")

    def test_member_office_phone_remains_optional_and_is_entered_manually(self):
        self.office.phone = "0112345678"
        self.office.save()
        form = MemberForm(initial={"district": self.district.pk})
        self.assertFalse(form.fields["phone_office"].required)
        self.assertFalse(form["phone_office"].value())
        member = self.create_member(phone_office="")
        self.assertEqual(member.phone_office, "")
        form = MemberForm(self.payload(phone_office="0115558899"), instance=member)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().phone_office, "0115558899")

    def make_lager(self, **overrides):
        data = {"district": self.district, "name": "Central Lager", "code": "LAG-001"}
        data.update(overrides)
        return Lager.objects.create(**data)

    def test_lager_master_crud_requires_district_and_normalizes_code(self):
        payload = {
            "district": self.district.pk,
            "name": "Central Lager",
            "code": "lag-001",
            "is_active": True,
        }
        self.assertIn("district", LagerForm({**payload, "district": ""}).errors)
        self.assertRedirects(
            self.client.post(reverse("lager_create"), payload), reverse("lager_list")
        )
        lager = Lager.objects.get(code="LAG-001")
        self.assertEqual(lager.district, self.district)
        self.assertRedirects(
            self.client.post(
                reverse("lager_edit", args=[lager.pk]),
                {**payload, "name": "Updated Lager"},
            ),
            reverse("lager_list"),
        )
        lager.refresh_from_db()
        self.assertEqual(lager.name, "Updated Lager")
        self.assertRedirects(
            self.client.post(reverse("lager_delete", args=[lager.pk])),
            reverse("lager_list"),
        )
        self.assertFalse(Lager.objects.filter(pk=lager.pk).exists())

    def test_lager_code_and_name_uniqueness(self):
        self.make_lager()
        self.assertIn(
            "code",
            LagerForm(
                {
                    "district": self.other_district.pk,
                    "name": "Other",
                    "code": "lag-001",
                    "is_active": True,
                }
            ).errors,
        )
        form = LagerForm(
            {
                "district": self.district.pk,
                "name": "Central Lager",
                "code": "OTHER",
                "is_active": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(
            LagerForm(
                {
                    "district": self.other_district.pk,
                    "name": "Central Lager",
                    "code": "OTHER",
                    "is_active": True,
                }
            ).is_valid()
        )

    def test_lager_many_members_and_member_directory_export_filter(self):
        lager = self.make_lager()
        self.create_member(lager=lager.pk, full_name="Lager Member One")
        self.create_member(
            lager=lager.pk,
            member_no="M-002",
            nic_no="199012345679",
            full_name="Lager Member Two",
        )
        other_lager = self.make_lager(name="Other Lager", code="OTHER-LAGER")
        self.create_member(
            lager=other_lager.pk,
            member_no="M-003",
            nic_no="199012345680",
            full_name="Other Lager Member",
        )
        self.assertEqual(lager.members.count(), 2)
        response = self.client.get(reverse("member_list"), {"lager": lager.pk})
        self.assertContains(response, "Lager Member One")
        self.assertContains(response, "Lager Member Two")
        self.assertNotContains(response, "Other Lager Member")
        self.assertEqual(response.context["filtered_count"], 2)
        rows = list(
            csv.DictReader(
                io.StringIO(
                    self.client.get(
                        reverse("member_export"), {"lager": lager.pk}
                    ).content.decode("utf-8-sig")
                )
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["Lager Code"] for row in rows}, {lager.code})
        self.assertEqual({row["Lager"] for row in rows}, {lager.name})

    def test_lager_selection_rejects_other_district_and_model_bypass(self):
        lager = self.make_lager(district=self.other_district)
        self.assertIn("lager", MemberForm(self.payload(lager=lager.pk)).errors)
        member = self.create_member()
        member.lager = lager
        with self.assertRaises(ValidationError) as error:
            member.full_clean()
        self.assertIn("lager", error.exception.message_dict)

    def test_inactive_lager_preserved_only_for_existing_members(self):
        lager = self.make_lager()
        member = self.create_member(lager=lager.pk)
        lager.is_active = False
        lager.save()
        self.assertIn("lager", MemberForm(self.payload(lager=lager.pk)).errors)
        form = MemberForm(
            self.payload(lager=lager.pk, full_name="Updated Member"), instance=member
        )
        self.assertTrue(form.is_valid(), form.errors)
        response = self.client.get(reverse("member_edit", args=[member.pk]))
        self.assertEqual(
            response.context["initial_locations"]["lagers"][0]["id"], lager.pk
        )
        self.assertEqual(str(response.context["form"]["lager"].value()), str(lager.pk))
        self.assertEqual(
            self.client.get(
                reverse("location_options"), {"district": self.district.pk}
            ).json()["lagers"],
            [],
        )

    def test_lager_protected_from_deletion_and_district_change_with_members(self):
        lager = self.make_lager()
        member = self.create_member(lager=lager.pk)
        form = LagerForm(
            {
                "district": self.other_district.pk,
                "name": lager.name,
                "code": lager.code,
                "is_active": True,
            },
            instance=lager,
        )
        self.assertIn("district", form.errors)
        self.client.post(reverse("lager_delete", args=[lager.pk]))
        self.assertTrue(Lager.objects.filter(pk=lager.pk).exists())
        self.assertEqual(Member.objects.get(pk=member.pk).lager_id, lager.pk)
        self.client.post(reverse("district_delete", args=[self.district.pk]))
        self.assertTrue(District.objects.filter(pk=self.district.pk).exists())

    def test_lager_filters_member_counts_and_district_counts(self):
        lager = self.make_lager()
        self.make_lager(name="Second Lager", code="LAG-002")
        self.make_lager(
            district=self.other_district, name="Other Lager", code="LAG-KAN"
        )
        self.create_member(lager=lager.pk)
        self.create_member(lager=lager.pk, member_no="M-002", nic_no="199012345679")
        for params in [
            {"district": self.district.pk},
            {"q": "LAG-001"},
            {"q": "Central Lager"},
        ]:
            response = self.client.get(reverse("lager_list"), params)
            self.assertContains(response, lager.name)
            self.assertNotContains(response, "Other Lager")
            listed = next(
                row for row in response.context["page_obj"] if row.pk == lager.pk
            )
            self.assertEqual(listed.member_count, 2)
            self.assertContains(response, f"?lager={lager.pk}")
        response = self.client.get(reverse("district_list"), {"q": self.district.name})
        district = next(
            row for row in response.context["page_obj"] if row.pk == self.district.pk
        )
        self.assertEqual(district.lager_count, 2)
        self.assertEqual(district.member_count, 2)

    def test_lager_options_follow_district_and_preserve_invalid_form_selection(self):
        lager = self.make_lager()
        self.make_lager(district=self.other_district, name="Other Lager", code="OTHER")
        data = self.client.get(
            reverse("location_options"), {"district": self.district.pk}
        ).json()
        self.assertEqual([row["id"] for row in data["lagers"]], [lager.pk])
        self.assertEqual(
            self.client.get(reverse("location_options"), {"district": "bad"}).json()[
                "lagers"
            ],
            [],
        )
        response = self.client.post(
            reverse("member_create"), self.payload(lager=lager.pk, full_name="")
        )
        self.assertEqual(str(response.context["form"]["lager"].value()), str(lager.pk))
        self.assertEqual(
            response.context["initial_locations"]["lagers"][0]["id"], lager.pk
        )
        self.assertContains(
            self.client.get(
                reverse("member_detail", args=[self.create_member(lager=lager.pk).pk])
            ),
            lager.name,
        )

    def test_lager_pages_require_authentication_and_render_with_district_initial(self):
        lager = self.make_lager()
        for name, args in [
            ("lager_list", []),
            ("lager_create", []),
            ("lager_edit", [lager.pk]),
            ("lager_delete", [lager.pk]),
        ]:
            self.assertEqual(Client().get(reverse(name, args=args)).status_code, 302)
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        response = self.client.get(
            reverse("lager_create"), {"district": self.district.pk}
        )
        self.assertEqual(
            str(response.context["form"]["district"].value()), str(self.district.pk)
        )

    def test_district_preserves_full_province_name_and_normalizes_short_name(self):
        response = self.client.post(
            reverse("district_edit", args=[self.district.pk]),
            {
                "name": self.district.name,
                "code": self.district.code,
                "province": "Western Province",
                "province_short_name": " wp ",
                "is_active": True,
            },
        )
        self.assertRedirects(response, reverse("district_list"))
        self.district.refresh_from_db()
        self.assertEqual(self.district.province, "Western Province")
        self.assertEqual(self.district.province_short_name, "WP")
        self.assertEqual(self.district.province_display, "Western Province (WP)")
        response = self.client.get(reverse("district_list"), {"q": "WP"})
        self.assertContains(response, "Western Province")
        self.assertContains(response, ">WP<")

    def test_province_fields_follow_district_and_ignore_posted_overrides(self):
        from .forms import SchoolForm

        form = MemberForm(
            self.payload(province="Incorrect", province_short_name="WRONG")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["province"], self.district.province)
        self.assertEqual(
            form.cleaned_data["province_short_name"], self.district.province_short_name
        )
        for form_class in [MemberForm, SchoolForm]:
            for district in [self.district, self.other_district]:
                with self.subTest(form=form_class.__name__, district=district.name):
                    initial = form_class(initial={"district": district.pk})
                    self.assertEqual(initial["province"].value(), district.province)
                    self.assertEqual(
                        initial["province_short_name"].value(),
                        district.province_short_name,
                    )
        response = self.client.get(reverse("member_create"))
        self.assertEqual(
            response.context["district_province_short_names"][self.district.pk],
            self.district.province_short_name,
        )
        self.assertContains(response, "id_province_short_name")

    def test_short_name_migration_preserves_full_names_and_custom_short_names(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection

        migration = import_module("members.migrations.0005_province_short_name")
        District.objects.filter(pk=self.district.pk).update(
            province="Western Province", province_short_name=""
        )
        District.objects.filter(pk=self.other_district.pk).update(
            province_short_name="CUSTOM"
        )
        custom = District.objects.create(
            name="Custom District", code="CUSTOM", province="Custom Province"
        )
        migration.populate_short_names(apps, SimpleNamespace(connection=connection))
        self.district.refresh_from_db()
        self.other_district.refresh_from_db()
        custom.refresh_from_db()
        self.assertEqual(self.district.province, "Western Province")
        self.assertEqual(self.district.province_short_name, "WP")
        self.assertEqual(self.other_district.province_short_name, "CUSTOM")
        self.assertEqual(custom.province, "Custom Province")
        self.assertEqual(custom.province_short_name, "")

    def test_school_label_and_profile_keep_full_and_short_province_names(self):
        school = self.make_school()
        self.assertIn(self.district.province_display, str(school))
        member = self.create_member(school=school.pk)
        response = self.client.get(reverse("member_detail", args=[member.pk]))
        self.assertContains(response, self.district.province)
        self.assertContains(response, self.district.province_short_name)
        self.district.province_short_name = ""
        self.district.save()
        school = (
            type(school)
            .objects.select_related("district", "zonal_office")
            .get(pk=school.pk)
        )
        self.assertNotIn("()", str(school))

    def test_school_and_lager_are_required_in_registration_and_model_validation(self):
        for missing in [{"school": ""}, {"lager": ""}, {"school": "", "lager": ""}]:
            with self.subTest(missing=missing):
                response = self.client.post(
                    reverse("member_create"), self.payload(**missing)
                )
                self.assertEqual(response.status_code, 200)
                for name in missing:
                    self.assertIn(name, response.context["form"].errors)
        self.assertEqual(Member.objects.count(), 0)
        member = self.create_member()
        member.school = None
        member.lager = None
        with self.assertRaises(ValidationError) as error:
            member.full_clean()
        self.assertIn("school", error.exception.message_dict)
        self.assertIn("lager", error.exception.message_dict)

    def test_school_first_registration_derives_missing_location_fields(self):
        school = self.make_school()
        payload = self.payload(school=school.pk)
        payload.pop("district")
        payload.pop("office_location")
        response = self.client.post(reverse("member_create"), payload)
        member = Member.objects.get()
        self.assertRedirects(response, reverse("member_detail", args=[member.pk]))
        self.assertEqual(member.district_id, school.district_id)
        self.assertEqual(member.office_location_id, school.zonal_office_id)
        self.assertEqual(member.school_id, school.pk)

    def test_school_location_overrides_stale_location_in_prefixed_submission(self):
        school = self.make_school(
            district=self.other_district, zonal_office=self.other_office
        )
        lager = self.make_lager(district=self.other_district)
        payload = self.payload(
            school=school.pk,
            lager=lager.pk,
            province="Wrong",
            province_short_name="BAD",
        )
        form = MemberForm(
            {f"member-{key}": value for key, value in payload.items()}, prefix="member"
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["district"], self.other_district)
        self.assertEqual(form.cleaned_data["office_location"], self.other_office)
        self.assertEqual(form.cleaned_data["province"], self.other_district.province)
        self.assertEqual(
            form.cleaned_data["province_short_name"],
            self.other_district.province_short_name,
        )

    def test_school_choices_available_before_district_and_include_location_metadata(
        self,
    ):
        school = self.make_school()
        other_school = self.make_school(
            code="OTHER", district=self.other_district, zonal_office=self.other_office
        )
        inactive_school = self.make_school(code="INACTIVE", is_active=False)
        response = self.client.get(reverse("member_create"))
        choices = response.context["form"].fields["school"].queryset
        self.assertCountEqual(choices, [school, other_school])
        self.assertNotIn(inactive_school, choices)
        rows = {row["id"]: row for row in response.context["member_schools"]}
        self.assertEqual(rows[other_school.pk]["district_id"], self.other_district.pk)
        self.assertEqual(rows[other_school.pk]["zonal_office_id"], self.other_office.pk)
        self.assertTrue(response.context["form"].fields["school"].required)
        self.assertTrue(response.context["form"].fields["lager"].required)

    def test_school_choices_filter_by_district_then_zone_and_sort_by_name(self):
        another_office = OfficeLocation.objects.create(
            district=self.district, name="Another Office", code="OTHER-ZONE"
        )
        alpha = self.make_school(name="alpha School", code="ALPHA")
        zulu = self.make_school(name="Zulu School", code="ZULU")
        other_zone = self.make_school(
            name="Beta School", code="BETA", zonal_office=another_office
        )
        self.make_school(
            code="OTHER-DISTRICT",
            district=self.other_district,
            zonal_office=self.other_office,
        )
        district_form = MemberForm(initial={"district": self.district.pk})
        self.assertEqual(
            list(district_form.fields["school"].queryset), [alpha, other_zone, zulu]
        )
        zone_form = MemberForm(
            initial={
                "district": self.district.pk,
                "office_location": self.office.pk,
            }
        )
        self.assertEqual(list(zone_form.fields["school"].queryset), [alpha, zulu])

    def test_edit_retains_all_school_metadata_for_changing_district(self):
        school = self.make_school()
        other_school = self.make_school(
            code="OTHER", district=self.other_district, zonal_office=self.other_office
        )
        member = self.create_member(school=school.pk)
        response = self.client.get(reverse("member_edit", args=[member.pk]))
        self.assertEqual(
            list(response.context["form"].fields["school"].queryset), [school]
        )
        rows = {row["id"]: row for row in response.context["member_schools"]}
        self.assertEqual(rows[other_school.pk]["code"], other_school.code)
        self.assertEqual(rows[other_school.pk]["district_id"], self.other_district.pk)
        self.assertContains(response, 'id="id_school"')
        self.assertNotContains(response, 'id="school-search"')

    def test_school_first_registration_autofills_subdivision_but_allows_blank(self):
        from .models import Subdivision

        subdivision = Subdivision.objects.create(
            zonal_office=self.office, name="North", code="NORTH"
        )
        school = self.make_school(subdivision=subdivision)
        payload = self.payload(school=school.pk)
        payload.pop("subdivision")
        form = MemberForm(payload)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["subdivision"], subdivision)
        form = MemberForm({**payload, "subdivision": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["subdivision"])

    def test_legacy_unassigned_member_requires_school_and_lager_when_edited(self):
        member = self.create_member()
        Member.objects.filter(pk=member.pk).update(school=None, lager=None)
        member.refresh_from_db()
        self.assertEqual(
            self.client.get(reverse("member_edit", args=[member.pk])).status_code, 200
        )
        response = self.client.post(
            reverse("member_edit", args=[member.pk]), self.payload(school="", lager="")
        )
        self.assertIn("school", response.context["form"].errors)
        self.assertIn("lager", response.context["form"].errors)
        member.refresh_from_db()
        self.assertIsNone(member.school_id)
        self.assertIsNone(member.lager_id)

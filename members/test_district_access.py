import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    District,
    Lager,
    Member,
    OfficeLocation,
    PaymentPlan,
    PaymentPlanAmount,
    PaymentType,
    School,
    Subdivision,
)
from .payment_forms import PaymentPlanForm
from .access_forms import workspace_permissions
from .permissions import accessible_districts


class DistrictAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "district-admin", password="test-only-pass"
        )
        cls.user = get_user_model().objects.create_user(
            "colombo-user", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.districts = [
            District.objects.get(code=code) for code in ("COL", "KAN", "GAL")
        ]
        cls.user.assigned_districts.add(cls.districts[0])
        cls.rows = []
        for index, district in enumerate(cls.districts):
            office = OfficeLocation.objects.create(
                district=district, name=f"Access Zone {index}", code=f"ACCESS-Z-{index}"
            )
            subdivision = Subdivision.objects.create(
                zonal_office=office,
                name=f"Access Division {index}",
                code=f"ACCESS-SUB-{index}",
            )
            school = School.objects.create(
                district=district,
                zonal_office=office,
                subdivision=subdivision,
                name=f"Access Department {index}",
                code=f"ACCESS-D-{index}",
            )
            lager = Lager.objects.create(
                zonal_office=office,
                name=f"Access Lager {index}",
                code=f"ACCESS-L-{index}",
            )
            member = Member.objects.create(
                district=district,
                office_location=office,
                school=school,
                lager=lager,
                member_no=f"ACCESS-M-{index}",
                full_name=f"District Member {index}",
                salary="1000.00",
                office_address="Manual office address",
                private_address="Private address",
                phone_mobile="0771234567",
                nic_no=f"19901234567{index}",
                gender="female",
            )
            plan = PaymentPlan.objects.create(member=member)
            PaymentPlanAmount.objects.create(
                plan=plan,
                payment_type=PaymentType.objects.first(),
                amount=Decimal("100.00") * (index + 1),
            )
            cls.rows.append(
                {
                    "district": district,
                    "office": office,
                    "subdivision": subdivision,
                    "school": school,
                    "lager": lager,
                    "member": member,
                    "payment_plan": plan,
                }
            )

    def setUp(self):
        self.client.force_login(self.user)

    def member_payload(self, index=0, **overrides):
        from .test_support import officer_for

        row = self.rows[index]
        data = {
            "maintenance_officer": officer_for(row["office"]).pk,
            "district": row["district"].pk,
            "office_location": row["office"].pk,
            "school": row["school"].pk,
            "lager": row["lager"].pk,
            "member_no": "ACCESS-NEW",
            "full_name": "New Assigned Member",
            "salary": "1000",
            "office_address": "Manual office",
            "private_address": "Manual home",
            "phone_mobile": "0771234567",
            "nic_no": "199012345689",
            "gender": "female",
            "admit_date": timezone.localdate().isoformat(),
            "status": "active",
            "subdivision": "",
        }
        data.update(overrides)
        return data

    def test_lists_and_filters_show_only_assigned_district(self):
        cases = [
            ("member_list", "member", "full_name"),
            ("district_list", "district", "name"),
            ("office_list", "office", "name"),
            ("subdivision_list", "subdivision", "name"),
            ("school_list", "school", "name"),
            ("lager_list", "lager", "name"),
            ("payment_plan_list", "member", "full_name"),
        ]
        for view, record, attribute in cases:
            with self.subTest(view=view):
                response = self.client.get(reverse(view))
                self.assertContains(response, getattr(self.rows[0][record], attribute))
                for row in self.rows[1:]:
                    self.assertNotContains(response, getattr(row[record], attribute))
                response = self.client.get(
                    reverse(view), {"district": self.districts[1].pk}
                )
                self.assertNotContains(
                    response, getattr(self.rows[1][record], attribute)
                )

    def test_direct_get_and_post_cannot_view_edit_or_delete_other_district_records(
        self,
    ):
        for prefix in (
            "member",
            "district",
            "office",
            "subdivision",
            "school",
            "lager",
            "payment_plan",
        ):
            for operation in ("edit", "delete") + (
                ("detail",) if prefix in ("member", "payment_plan") else ()
            ):
                url = reverse(f"{prefix}_{operation}", args=[self.rows[1][prefix].pk])
                for method in ("get", "post"):
                    with self.subTest(
                        prefix=prefix, operation=operation, method=method
                    ):
                        self.assertEqual(
                            getattr(self.client, method)(url, {}).status_code, 404
                        )
        self.assertTrue(Member.objects.filter(pk=self.rows[1]["member"].pk).exists())
        self.assertTrue(
            PaymentPlan.objects.filter(pk=self.rows[1]["payment_plan"].pk).exists()
        )

    def test_member_creation_rejects_foreign_district_and_department_ids(self):
        for data in (
            self.member_payload(1),
            self.member_payload(school=self.rows[1]["school"].pk),
            self.member_payload(lager=self.rows[1]["lager"].pk),
        ):
            response = self.client.post(reverse("member_create"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertFalse(Member.objects.filter(member_no="ACCESS-NEW").exists())
        response = self.client.post(reverse("member_create"), self.member_payload())
        saved = Member.objects.get(member_no="ACCESS-NEW")
        self.assertEqual(saved.district, self.districts[0])
        self.assertRedirects(response, reverse("member_detail", args=[saved.pk]))

    def test_member_edit_cannot_move_record_to_unassigned_district(self):
        member = self.rows[0]["member"]
        response = self.client.post(
            reverse("member_edit", args=[member.pk]),
            self.member_payload(1, member_no=member.member_no, nic_no=member.nic_no),
        )
        self.assertTrue(response.context["form"].errors)
        member.refresh_from_db()
        self.assertEqual(member.district, self.districts[0])

    def test_master_creates_reject_foreign_parent_ids(self):
        foreign = self.rows[1]
        cases = [
            (
                "office_create",
                {
                    "district": foreign["district"].pk,
                    "name": "Forbidden office",
                    "code": "FORBIDDEN-O",
                    "is_active": "on",
                },
                OfficeLocation,
            ),
            (
                "lager_create",
                {
                    "district": foreign["district"].pk,
                    "name": "Forbidden lager",
                    "code": "FORBIDDEN-L",
                    "is_active": "on",
                },
                Lager,
            ),
            (
                "school_create",
                {
                    "district": foreign["district"].pk,
                    "zonal_office": foreign["office"].pk,
                    "name": "Forbidden department",
                    "code": "FORBIDDEN-D",
                    "is_active": "on",
                },
                School,
            ),
            (
                "subdivision_create",
                {
                    "district": foreign["district"].pk,
                    "zonal_office": foreign["office"].pk,
                    "name": "Forbidden division",
                    "code": "FORBIDDEN-S",
                    "is_active": "on",
                },
                Subdivision,
            ),
        ]
        for view, data, model in cases:
            with self.subTest(view=view):
                response = self.client.post(reverse(view), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(model.objects.filter(code=data["code"]).exists())

    def test_forms_and_embedded_location_json_do_not_leak_foreign_choices(self):
        for view in (
            "member_create",
            "office_create",
            "lager_create",
            "school_create",
            "subdivision_create",
        ):
            response = self.client.get(
                reverse(view),
                {
                    "district": self.districts[1].pk,
                    "zonal_office": self.rows[1]["office"].pk,
                },
            )
            form = response.context["form"]
            self.assertEqual(
                list(form.fields["district"].queryset), [self.districts[0]]
            )
            self.assertEqual(
                set(response.context["district_provinces"]), {self.districts[0].pk}
            )
            self.assertEqual(response.context["initial_locations"]["offices"], [])
        response = self.client.get(reverse("member_create"))
        self.assertEqual(
            {row["district_id"] for row in response.context["member_schools"]},
            {self.districts[0].pk},
        )
        response = self.client.get(reverse("member_list"))
        self.assertEqual(
            {row["district_id"] for row in response.context["all_lagers"]},
            {self.districts[0].pk},
        )

    def test_lookup_endpoints_reject_foreign_districts(self):
        for view in ("office_options", "location_options"):
            self.assertEqual(
                self.client.get(
                    reverse(view), {"district": self.districts[0].pk}
                ).status_code,
                200,
            )
            for district in self.districts[1:]:
                self.assertEqual(
                    self.client.get(
                        reverse(view), {"district": district.pk}
                    ).status_code,
                    403,
                )

    def test_dashboard_counts_chart_and_recent_members_are_scoped(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["total_members"], 1)
        self.assertEqual(response.context["active_members"], 1)
        self.assertEqual(response.context["district_count"], 1)
        self.assertEqual(response.context["office_count"], 1)
        self.assertEqual(response.context["chart_total"], 1)
        self.assertEqual(
            list(response.context["recent_members"]), [self.rows[0]["member"]]
        )

    def test_exports_cannot_include_other_districts_even_with_forged_filters(self):
        for params in (
            {},
            {"district": self.districts[1].pk},
            {"q": self.rows[1]["member"].full_name},
        ):
            response = self.client.get(reverse("member_export"), params)
            text = response.content.decode("utf-8-sig")
            self.assertNotIn(self.rows[1]["member"].member_no, text)
            rows = list(csv.DictReader(io.StringIO(text)))
            self.assertEqual(len(rows), 0 if params else 1)

    def test_payment_plan_creation_and_editing_are_scoped_to_member_district(self):
        url = reverse("payment_plan_create")
        foreign = self.rows[1]["member"]
        self.assertEqual(self.client.get(url, {"member": foreign.pk}).status_code, 404)
        self.assertEqual(
            self.client.post(
                url,
                {
                    "member": foreign.pk,
                    "agreement_date": timezone.localdate().isoformat(),
                },
            ).status_code,
            404,
        )
        form = PaymentPlanForm(
            {"agreement_date": timezone.localdate().isoformat()},
            member=foreign,
            user=self.user,
        )
        self.assertFalse(form.is_valid())

    def test_multi_district_assignment_allows_both_without_exposing_third(self):
        self.user.assigned_districts.add(self.districts[1])
        response = self.client.get(reverse("member_list"))
        for row in self.rows[:2]:
            self.assertContains(response, row["member"].full_name)
            self.assertEqual(
                self.client.get(
                    reverse("member_edit", args=[row["member"].pk])
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    reverse("office_delete", args=[row["office"].pk])
                ).status_code,
                200,
            )
        self.assertNotContains(response, self.rows[2]["member"].full_name)
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.rows[2]["member"].pk])
            ).status_code,
            404,
        )

    def test_multi_district_user_can_update_and_delete_only_assigned_records(self):
        self.user.assigned_districts.add(self.districts[1])
        for index in (0, 1):
            member = self.rows[index]["member"]
            data = self.member_payload(
                index,
                member_no=member.member_no,
                nic_no=member.nic_no,
                full_name=f"Edited district {index}",
            )
            response = self.client.post(reverse("member_edit", args=[member.pk]), data)
            self.assertRedirects(response, reverse("member_detail", args=[member.pk]))
            disposable = Lager.objects.create(
                zonal_office=self.rows[index]["office"],
                name="Delete within assignment",
                code=f"DELETE-{index}",
            )
            self.client.post(reverse("lager_delete", args=[disposable.pk]))
            self.assertFalse(Lager.objects.filter(pk=disposable.pk).exists())

    def test_no_assigned_districts_denies_all_district_records(self):
        self.user.assigned_districts.clear()
        self.assertFalse(accessible_districts(self.user).exists())
        response = self.client.get(reverse("member_list"))
        self.assertEqual(response.context["total_members"], 0)
        self.assertContains(response, "No districts are assigned")
        self.assertEqual(
            self.client.get(
                reverse("member_detail", args=[self.rows[0]["member"].pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("member_create")).context["member_schools"], []
        )
        self.assertEqual(
            self.client.get(reverse("payment_plan_list"))
            .context["page_obj"]
            .paginator.count,
            0,
        )

    def test_assignment_changes_apply_to_existing_session_on_next_request(self):
        url = reverse("member_edit", args=[self.rows[0]["member"].pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.user.assigned_districts.set([self.districts[1]])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("member_edit", args=[self.rows[1]["member"].pk])
            ).status_code,
            200,
        )

    def test_roles_and_module_permissions_do_not_expand_district_assignments(self):
        role = Group.objects.create(name="All module operations")
        role.permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        self.user.user_permissions.clear()
        self.user.groups.add(role)
        self.assertEqual(
            self.client.get(
                reverse("member_edit", args=[self.rows[0]["member"].pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("member_edit", args=[self.rows[1]["member"].pk])
            ).status_code,
            404,
        )
        self.user.assigned_districts.add(self.districts[1])
        self.user.groups.clear()
        self.assertEqual(
            self.client.get(
                reverse("member_edit", args=[self.rows[1]["member"].pk])
            ).status_code,
            403,
        )

    def test_only_administrator_can_change_district_assignments(self):
        url = reverse("access_user_edit", args=[self.user.pk])
        self.assertEqual(
            self.client.post(
                url, {"districts": [district.pk for district in self.districts]}
            ).status_code,
            403,
        )
        self.assertEqual(list(self.user.assigned_districts.all()), [self.districts[0]])
        self.client.force_login(self.admin)
        response = self.client.post(
            url,
            {
                "username": self.user.username,
                "is_active": "on",
                "districts": [self.districts[0].pk, self.districts[1].pk],
                "module_permissions": list(
                    workspace_permissions().values_list("pk", flat=True)
                ),
            },
        )
        self.assertRedirects(response, reverse("access_users"))
        self.assertCountEqual(self.user.assigned_districts.all(), self.districts[:2])

    def test_admin_can_remove_all_assignments_and_reject_unknown_district_ids(self):
        self.client.force_login(self.admin)
        url = reverse("access_user_edit", args=[self.user.pk])
        data = {
            "username": self.user.username,
            "is_active": "on",
            "districts": [999999],
        }
        response = self.client.post(url, data)
        self.assertIn("districts", response.context["form"].errors)
        self.assertTrue(self.user.assigned_districts.exists())
        self.client.post(url, {**data, "districts": []})
        self.assertFalse(self.user.assigned_districts.exists())

    def test_superuser_has_all_districts_and_actions_without_assignments(self):
        self.client.force_login(self.admin)
        self.assertFalse(self.admin.assigned_districts.exists())
        for row in self.rows:
            self.assertEqual(
                self.client.get(
                    reverse("member_edit", args=[row["member"].pk])
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    reverse("payment_plan_delete", args=[row["payment_plan"].pk])
                ).status_code,
                200,
            )
        self.assertEqual(self.client.get(reverse("district_create")).status_code, 200)
        new = District.objects.create(
            name="New Future District", code="FUTURE", province="Future Province"
        )
        self.assertIn(new, accessible_districts(self.admin))

    def test_users_cannot_expand_access_by_creating_new_district(self):
        self.assertEqual(self.client.get(reverse("district_create")).status_code, 403)
        response = self.client.post(
            reverse("district_create"),
            {
                "name": "Unauthorized New District",
                "code": "UNAUTH",
                "province": "New Province",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(District.objects.filter(code="UNAUTH").exists())
        self.assertNotContains(
            self.client.get(reverse("district_list")),
            f'href="{reverse("district_create")}"',
        )

    def test_staff_cannot_use_advanced_admin_to_bypass_district_scope(self):
        self.user.is_staff = True
        self.user.save()
        self.user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="auth")
        )
        urls = [
            reverse("admin:members_member_changelist"),
            reverse("admin:members_member_change", args=[self.rows[1]["member"].pk]),
            reverse("admin:auth_user_change", args=[self.user.pk]),
            reverse("admin:autocomplete"),
        ]
        for url in urls:
            for method in ("get", "post"):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse("admin:login")))
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("admin:members_member_changelist")).status_code, 200
        )

    def test_shared_payment_types_cannot_be_changed_by_district_users(self):
        payment_type = PaymentType.objects.first()
        for view, args in (
            ("payment_type_create", []),
            ("payment_type_edit", [payment_type.pk]),
            ("payment_type_delete", [payment_type.pk]),
        ):
            for method in ("get", "post"):
                self.assertEqual(
                    getattr(self.client, method)(
                        reverse(view, args=args),
                        {"name": "Unauthorized shared type", "is_active": "on"},
                    ).status_code,
                    403,
                )
        response = self.client.get(reverse("payment_type_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{reverse("payment_type_create")}"')

    def test_shared_payment_type_agreement_counts_are_scoped(self):
        payment_type = PaymentType.objects.first()

        def count():
            response = self.client.get(reverse("payment_type_list"))
            return next(
                item.plan_count
                for item in response.context["page_obj"]
                if item.pk == payment_type.pk
            )

        self.assertEqual(count(), 1)
        self.user.assigned_districts.add(self.districts[1])
        self.assertEqual(count(), 2)
        self.client.force_login(self.admin)
        self.assertEqual(count(), 3)

    def test_administrator_can_assign_multiple_districts_when_creating_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("access_user_create"),
            {
                "username": "multi-district-new",
                "is_active": "on",
                "password1": "New-district-account-strong-82",
                "password2": "New-district-account-strong-82",
                "districts": [self.districts[0].pk, self.districts[1].pk],
                "module_permissions": list(
                    Permission.objects.filter(
                        content_type__app_label="members",
                        codename__in=["view_member", "add_member"],
                    ).values_list("pk", flat=True)
                ),
            },
        )
        self.assertRedirects(response, reverse("access_users"))
        new = get_user_model().objects.get(username="multi-district-new")
        self.assertCountEqual(new.assigned_districts.all(), self.districts[:2])
        self.assertTrue(new.check_password("New-district-account-strong-82"))

    def test_inactive_existing_choices_do_not_bypass_district_restriction(self):
        member = self.rows[1]["member"]
        school = self.rows[1]["school"]
        school.is_active = False
        school.save()
        self.assertEqual(
            self.client.get(reverse("member_edit", args=[member.pk])).status_code, 404
        )
        response = self.client.get(reverse("member_create"))
        self.assertNotIn(
            school.pk, [row["id"] for row in response.context["member_schools"]]
        )

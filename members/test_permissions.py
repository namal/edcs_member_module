"""Authorization boundaries and the custom administration workflow."""

from django.contrib.admin.models import LogEntry
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
    OpeningBalance,
    PaymentPlan,
    PaymentType,
    School,
    Subdivision,
)
from .permissions import MODULES


class WorkspacePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "edcs-test", password="test-only-pass"
        )
        cls.district = District.objects.get(code="COL")
        cls.user.assigned_districts.add(cls.district)
        cls.office = OfficeLocation.objects.create(
            district=cls.district, name="Office", code="OFF"
        )
        cls.subdivision = Subdivision.objects.create(
            zonal_office=cls.office, name="Division", code="DIV"
        )
        cls.school = School.objects.create(
            district=cls.district, zonal_office=cls.office, name="School", code="SCH"
        )
        cls.lager = Lager.objects.create(
            district=cls.district, name="Lager", code="LAG"
        )
        cls.member = Member.objects.create(
            district=cls.district,
            office_location=cls.office,
            school=cls.school,
            lager=cls.lager,
            member_no="M-1",
            full_name="Existing Member",
            salary="1000.00",
            office_address="Office Road",
            private_address="Home Road",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345678",
        )
        plan_member = Member.objects.create(
            district=cls.district,
            office_location=cls.office,
            school=cls.school,
            lager=cls.lager,
            member_no="M-PLAN",
            full_name="Plan Member",
            salary="1000.00",
            office_address="Office Road",
            private_address="Home Road",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345681",
        )
        cls.plan = PaymentPlan.objects.create(member=plan_member)
        cls.records = {
            "member": cls.member,
            "district": cls.district,
            "office": cls.office,
            "subdivision": cls.subdivision,
            "school": cls.school,
            "lager": cls.lager,
            "payment_plan": cls.plan,
            "opening_balance": OpeningBalance.objects.create(member=plan_member),
            "payment_type": PaymentType.objects.get(name="Special Deposit"),
        }

    def setUp(self):
        self.client.force_login(self.user)

    def grant(self, *codenames):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codenames
            )
        )

    def member_payload(self):
        return {
            "district": self.district.pk,
            "office_location": self.office.pk,
            "school": self.school.pk,
            "lager": self.lager.pk,
            "member_no": "M-NEW",
            "full_name": "New Registration",
            "salary": "1000",
            "office_address": "Office Road",
            "private_address": "Home Road",
            "phone_mobile": "0771234567",
            "admit_date": timezone.localdate().isoformat(),
            "gender": "female",
            "nic_no": "199012345679",
            "status": "active",
        }

    def test_edcs_can_view_and_register_members(self):
        self.grant("view_member", "add_member")
        for name, args in [
            ("dashboard", []),
            ("member_list", []),
            ("member_detail", [self.member.pk]),
            ("member_create", []),
            ("member_export", []),
        ]:
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=args)).status_code, 200
                )
        response = self.client.post(reverse("member_create"), self.member_payload())
        new_member = Member.objects.get(member_no="M-NEW")
        self.assertRedirects(response, reverse("member_detail", args=[new_member.pk]))

    def test_edcs_cannot_edit_delete_or_open_any_master_module(self):
        self.grant("view_member", "add_member")
        for model, _, _, prefix, _ in MODULES:
            operations = (
                ("edit", "delete")
                if model is Member
                else ("list", "create", "edit", "delete")
            )
            for operation in operations:
                args = (
                    [self.records[prefix].pk] if operation in ("edit", "delete") else []
                )
                url = reverse(f"{prefix}_{operation}", args=args)
                for method in ("get", "post"):
                    with self.subTest(
                        module=prefix, operation=operation, method=method
                    ):
                        self.assertEqual(
                            getattr(self.client, method)(url).status_code, 403
                        )
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertEqual(
            Member.objects.get(pk=self.member.pk).full_name, "Existing Member"
        )

    def test_edcs_interface_has_no_forbidden_links(self):
        self.grant("view_member", "add_member")
        forbidden = [
            reverse("member_edit", args=[self.member.pk]),
            reverse("member_delete", args=[self.member.pk]),
            reverse("administration"),
            reverse("access_users"),
            reverse("access_roles"),
        ]
        forbidden += [
            reverse(f"{prefix}_{action}")
            for _, _, _, prefix, _ in MODULES[1:]
            for action in ("list", "create")
        ]
        for name, args in [
            ("dashboard", []),
            ("member_list", []),
            ("member_detail", [self.member.pk]),
            ("member_create", []),
        ]:
            response = self.client.get(reverse(name, args=args))
            for url in forbidden:
                with self.subTest(page=name, url=url):
                    self.assertNotContains(response, f'href="{url}')
        self.assertContains(
            self.client.get(reverse("member_list")),
            f'href="{reverse("member_create")}"',
        )

    def test_staff_flag_does_not_grant_workspace_access(self):
        self.user.is_staff = True
        self.user.save()
        for model, _, _, prefix, _ in MODULES:
            for operation in ("list", "create", "edit", "delete"):
                args = (
                    [self.records[prefix].pk] if operation in ("edit", "delete") else []
                )
                with self.subTest(module=prefix, operation=operation):
                    self.assertEqual(
                        self.client.get(
                            reverse(f"{prefix}_{operation}", args=args)
                        ).status_code,
                        403,
                    )
        self.assertEqual(self.client.get(reverse("administration")).status_code, 403)

    def test_each_action_is_checked_independently_for_every_module(self):
        for model, _, _, prefix, _ in MODULES:
            model_name = model._meta.model_name
            for permission, allowed_operation in [
                ("view", "list"),
                ("add", "create"),
                ("change", "edit"),
                ("delete", "delete"),
            ]:
                self.grant(f"{permission}_{model_name}")
                for operation in ("list", "create", "edit", "delete"):
                    args = (
                        [self.records[prefix].pk]
                        if operation in ("edit", "delete")
                        else []
                    )
                    with self.subTest(
                        module=prefix, grant=permission, operation=operation
                    ):
                        self.assertEqual(
                            self.client.get(
                                reverse(f"{prefix}_{operation}", args=args)
                            ).status_code,
                            (
                                200
                                if operation == allowed_operation
                                and not (model is District and operation == "create")
                                and not (model is PaymentType and operation != "list")
                                else 403
                            ),
                        )

    def test_view_only_lists_keep_record_names_and_hide_mutation_links(self):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__startswith="view_"
            )
        )
        for _, _, _, prefix, _ in MODULES:
            response = self.client.get(reverse(f"{prefix}_list"))
            self.assertContains(
                response,
                (
                    self.records[prefix].full_name
                    if prefix == "member"
                    else (
                        self.records[prefix].member.full_name
                        if prefix in ("payment_plan", "opening_balance")
                        else self.records[prefix].name
                    )
                ),
            )
            for operation in ("create", "edit", "delete"):
                args = [self.records[prefix].pk] if operation != "create" else []
                self.assertNotContains(
                    response, f'href="{reverse(f"{prefix}_{operation}", args=args)}'
                )
        self.assertNotContains(
            self.client.get(reverse("member_detail", args=[self.member.pk])),
            f'href="{reverse("member_edit", args=[self.member.pk])}',
        )

    def test_add_only_member_user_can_save_without_view_permission(self):
        self.grant("add_member")
        response = self.client.post(reverse("member_create"), self.member_payload())
        self.assertRedirects(response, reverse("member_create"))
        self.assertTrue(Member.objects.filter(member_no="M-NEW").exists())
        self.assertEqual(self.client.get(reverse("member_list")).status_code, 403)
        self.assertNotContains(
            self.client.get(reverse("member_create")),
            f'href="{reverse("member_list")}"',
        )

    def test_edit_only_member_user_returns_to_allowed_editor_after_save(self):
        self.grant("change_member")
        response = self.client.post(
            reverse("member_edit", args=[self.member.pk]), self.member_payload()
        )
        self.assertRedirects(response, reverse("member_edit", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertEqual(self.member.full_name, "New Registration")
        self.assertEqual(self.client.get(reverse("member_create")).status_code, 403)

    def test_delete_only_member_user_can_delete_without_view_access(self):
        self.grant("delete_member")
        response = self.client.post(reverse("member_delete", args=[self.member.pk]))
        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())

    def test_zero_id_edit_route_never_falls_back_to_add_permission(self):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__startswith="add_"
            )
        )
        for _, _, _, prefix, _ in MODULES:
            self.assertEqual(
                self.client.get(reverse(f"{prefix}_edit", args=[0])).status_code, 403
            )

    def test_member_registration_lookups_do_not_grant_master_access(self):
        self.grant("add_member")
        for name in ("office_options", "location_options"):
            self.assertEqual(
                self.client.get(
                    reverse(name), {"district": self.district.pk}
                ).status_code,
                200,
            )
        self.assertEqual(self.client.get(reverse("office_list")).status_code, 403)
        self.grant()
        for name in ("office_options", "location_options"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_group_grants_and_revocations_apply_on_next_request(self):
        role = Group.objects.create(name="Registration")
        role.permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=["view_member", "add_member"],
            )
        )
        self.user.groups.add(role)
        self.assertEqual(self.client.get(reverse("member_create")).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(reverse("member_create")).status_code, 403)

    def test_anonymous_users_redirect_to_login(self):
        anonymous = Client()
        for name in (
            "member_list",
            "district_create",
            "access_users",
            "access_roles",
            "administration",
        ):
            response = anonymous.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

    def test_inactive_users_lose_access(self):
        self.grant("view_member", "add_member")
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get(reverse("member_list")).status_code, 302)


class AdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "admin-test", password="test-only-pass"
        )
        cls.user = get_user_model().objects.create_user(
            "edcs-test", password="test-only-pass"
        )
        cls.user.assigned_districts.set(District.objects.all())
        cls.view = Permission.objects.get(
            content_type__app_label="members", codename="view_member"
        )
        cls.add = Permission.objects.get(
            content_type__app_label="members", codename="add_member"
        )
        cls.role = Group.objects.create(name="Registration")
        cls.role.permissions.add(cls.view, cls.add)

    def setUp(self):
        self.client.force_login(self.admin)

    def user_payload(self, **overrides):
        data = {
            "username": self.user.username,
            "first_name": "EDCS",
            "last_name": "Operator",
            "email": "edcs@example.com",
            "is_active": "on",
            "module_permissions": [self.view.pk, self.add.pk],
            "districts": list(
                self.user.assigned_districts.values_list("pk", flat=True)
            ),
        }
        data.update(overrides)
        return data

    def test_custom_administration_pages_render_for_admin(self):
        for name, args in [
            ("administration", []),
            ("access_users", []),
            ("access_roles", []),
            ("access_user_create", []),
            ("access_user_edit", [self.user.pk]),
            ("access_user_edit", [self.admin.pk]),
            ("access_user_delete", [self.user.pk]),
            ("access_role_create", []),
            ("access_role_edit", [self.role.pk]),
            ("access_role_delete", [self.role.pk]),
        ]:
            with self.subTest(page=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=args)).status_code, 200
                )

    def test_admin_and_workspace_users_have_distinct_login_destinations(self):
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {"username": self.admin.username, "password": "test-only-pass"},
        )
        self.assertRedirects(response, reverse("administration"))
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "test-only-pass"},
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_login_respects_safe_next_url_and_rejects_external_redirects(self):
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {
                "username": self.admin.username,
                "password": "test-only-pass",
                "next": reverse("access_users"),
            },
        )
        self.assertRedirects(response, reverse("access_users"))
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {
                "username": self.admin.username,
                "password": "test-only-pass",
                "next": "https://example.com/steal",
            },
        )
        self.assertRedirects(response, reverse("administration"))

    def test_non_superusers_cannot_manage_access_even_with_auth_permissions(self):
        self.user.is_staff = True
        self.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="auth")
        )
        self.user.save()
        self.client.force_login(self.user)
        for name, args in [
            ("administration", []),
            ("access_users", []),
            ("access_roles", []),
            ("access_user_create", []),
            ("access_user_edit", [self.admin.pk]),
            ("access_user_delete", [self.admin.pk]),
            ("access_role_create", []),
            ("access_role_edit", [self.role.pk]),
            ("access_role_delete", [self.role.pk]),
        ]:
            for method in ("get", "post"):
                with self.subTest(page=name, method=method):
                    self.assertEqual(
                        getattr(self.client, method)(
                            reverse(name, args=args)
                        ).status_code,
                        403,
                    )

    def test_admin_sets_exact_direct_permissions_and_changes_are_audited(self):
        response = self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.user_payload(is_staff="on", is_superuser="on"),
        )
        self.assertRedirects(response, reverse("access_users"))
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.get_all_permissions(),
            {"members.view_member", "members.add_member"},
        )
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertTrue(
            LogEntry.objects.filter(
                user=self.admin, object_id=str(self.user.pk)
            ).exists()
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("member_list")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("member_edit", args=[999])).status_code, 403
        )

    def test_permission_matrix_renders_selected_permissions(self):
        self.user.user_permissions.add(self.view, self.add)
        response = self.client.get(reverse("access_user_edit", args=[self.user.pk]))
        selected = {
            cell["id"]
            for row in response.context["permission_rows"]
            for cell in row["cells"]
            if cell["checked"]
        }
        self.assertEqual(selected, {self.view.pk, self.add.pk})

    def test_create_account_hashes_password_and_assigns_role(self):
        response = self.client.post(
            reverse("access_user_create"),
            self.user_payload(
                username="new-operator",
                password1="EDCS-strong-test-pass-51",
                password2="EDCS-strong-test-pass-51",
                roles=[self.role.pk],
                module_permissions=[],
            ),
        )
        self.assertRedirects(response, reverse("access_users"))
        user = get_user_model().objects.get(username="new-operator")
        self.assertTrue(user.check_password("EDCS-strong-test-pass-51"))
        self.assertEqual(
            user.get_all_permissions(), {"members.view_member", "members.add_member"}
        )
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_new_password_validation_and_duplicate_username(self):
        for overrides, field in [
            ({"password1": "123", "password2": "123"}, "password1"),
            (
                {"password1": "EDCS-test-strong-pass", "password2": "different"},
                "password2",
            ),
            ({}, "password1"),
        ]:
            with self.subTest(field=field, overrides=overrides):
                response = self.client.post(
                    reverse("access_user_create"),
                    self.user_payload(username="invalid-new", **overrides),
                )
                self.assertFormError(
                    response.context["form"],
                    field,
                    response.context["form"].errors[field],
                )
                self.assertFalse(
                    get_user_model().objects.filter(username="invalid-new").exists()
                )
        response = self.client.post(
            reverse("access_user_create"),
            self.user_payload(
                password1="EDCS-strong-test-pass-51",
                password2="EDCS-strong-test-pass-51",
            ),
        )
        self.assertIn("username", response.context["form"].errors)

    def test_edit_keeps_password_or_can_reset_it(self):
        self.client.post(
            reverse("access_user_edit", args=[self.user.pk]), self.user_payload()
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("test-only-pass"))
        self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.user_payload(
                password1="EDCS-new-strong-pass-62", password2="EDCS-new-strong-pass-62"
            ),
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("EDCS-new-strong-pass-62"))

    def test_deactivate_user_blocks_existing_session(self):
        user_client = Client()
        user_client.force_login(self.user)
        self.user.user_permissions.add(self.view)
        self.assertEqual(user_client.get(reverse("member_list")).status_code, 200)
        response = self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.user_payload(is_active=""),
        )
        self.assertRedirects(response, reverse("access_users"))
        self.assertEqual(user_client.get(reverse("member_list")).status_code, 302)

    def test_self_deactivation_and_superuser_deletion_are_blocked(self):
        response = self.client.post(
            reverse("access_user_edit", args=[self.admin.pk]),
            self.user_payload(username=self.admin.username, is_active=""),
        )
        self.assertIn("is_active", response.context["form"].errors)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        for method in ("get", "post"):
            self.assertEqual(
                getattr(self.client, method)(
                    reverse("access_user_delete", args=[self.admin.pk])
                ).status_code,
                403,
            )

    def test_self_password_change_keeps_admin_logged_in(self):
        response = self.client.post(
            reverse("access_user_edit", args=[self.admin.pk]),
            self.user_payload(
                username=self.admin.username,
                password1="EDCS-admin-new-strong-64",
                password2="EDCS-admin-new-strong-64",
            ),
        )
        self.assertRedirects(response, reverse("access_users"))
        self.assertEqual(self.client.get(reverse("administration")).status_code, 200)

    def test_delete_only_on_post(self):
        self.client.get(reverse("access_user_delete", args=[self.user.pk]))
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        response = self.client.post(reverse("access_user_delete", args=[self.user.pk]))
        self.assertRedirects(response, reverse("access_users"))
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_role_create_update_and_delete_update_effective_access(self):
        response = self.client.post(
            reverse("access_role_create"),
            {"name": "View only", "module_permissions": [self.view.pk]},
        )
        self.assertRedirects(response, reverse("access_roles"))
        role = Group.objects.get(name="View only")
        self.user.groups.add(role)
        operator = Client()
        operator.force_login(self.user)
        self.assertEqual(operator.get(reverse("member_create")).status_code, 403)
        response = self.client.post(
            reverse("access_role_edit", args=[role.pk]),
            {
                "name": "Registration team",
                "module_permissions": [self.view.pk, self.add.pk],
            },
        )
        self.assertRedirects(response, reverse("access_roles"))
        self.assertEqual(operator.get(reverse("member_create")).status_code, 200)
        self.client.get(reverse("access_role_delete", args=[role.pk]))
        self.assertTrue(Group.objects.filter(pk=role.pk).exists())
        self.client.post(reverse("access_role_delete", args=[role.pk]))
        self.assertEqual(operator.get(reverse("member_create")).status_code, 403)

    def test_role_assignment_and_removal_via_user_form(self):
        self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.user_payload(roles=[self.role.pk], module_permissions=[]),
        )
        self.assertTrue(self.user.groups.filter(pk=self.role.pk).exists())
        self.client.post(
            reverse("access_user_edit", args=[self.user.pk]),
            self.user_payload(roles=[], module_permissions=[]),
        )
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(
            get_user_model().objects.get(pk=self.user.pk).get_all_permissions()
        )

    def test_unknown_and_system_permission_ids_are_rejected(self):
        system = Permission.objects.get(
            content_type__app_label="auth", codename="change_user"
        )
        for permission_id in (999999, system.pk):
            response = self.client.post(
                reverse("access_user_edit", args=[self.user.pk]),
                self.user_payload(module_permissions=[permission_id]),
            )
            self.assertIn("module_permissions", response.context["form"].errors)
        self.assertFalse(self.user.user_permissions.exists())

    def test_advanced_existing_permissions_are_preserved(self):
        system = Permission.objects.get(
            content_type__app_label="auth", codename="view_user"
        )
        self.user.user_permissions.add(system)
        self.role.permissions.add(system)
        self.client.post(
            reverse("access_user_edit", args=[self.user.pk]), self.user_payload()
        )
        self.client.post(
            reverse("access_role_edit", args=[self.role.pk]),
            {"name": self.role.name, "module_permissions": [self.view.pk]},
        )
        self.assertTrue(self.user.user_permissions.filter(pk=system.pk).exists())
        self.assertTrue(self.role.permissions.filter(pk=system.pk).exists())

    def test_account_and_role_posts_require_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        for name in ("access_user_create", "access_role_create"):
            self.assertEqual(csrf_client.post(reverse(name), {}).status_code, 403)

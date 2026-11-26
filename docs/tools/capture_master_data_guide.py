#!/usr/bin/env python3
"""Verify master creation and render current screens in a disposable test DB."""

import json
import logging
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
HTML = Path("/tmp/edcs-master-data-guide-html")
ASSETS = ROOT / "docs/master_data_assets"


def main():
    import django
    from django.conf import settings

    database = "edcs_master_guide_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    django.setup()
    logging.getLogger("django.request").setLevel(logging.CRITICAL)

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group, Permission
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment
    from django.urls import reverse
    from members import models as m
    from members.permissions import MODULES

    HTML.mkdir(exist_ok=True, parents=True)
    ASSETS.mkdir(exist_ok=True, parents=True)
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        admin = get_user_model().objects.create_superuser(
            "master.guide.admin", first_name="Training", last_name="Administrator"
        )
        client = Client()
        client.force_login(admin)
        captures, created, field_catalog = [], [], {}

        def screen(
            name,
            route_name,
            *,
            params=None,
            fill=None,
            mode="main",
            height=1100,
            args=None,
        ):
            url = reverse(route_name, args=args)
            response = client.get(url, params or {})
            assert response.status_code == 200, (name, response.status_code)
            (HTML / f"{name}.html").write_bytes(response.content)
            captures.append([name, name, mode, height, fill or {}])
            if "form" in response.context:
                field_catalog[name] = [
                    {
                        "name": key,
                        "label": str(field.label or key.replace("_", " ").capitalize()),
                        "required": field.required,
                        "disabled": field.disabled,
                        "help": str(field.help_text),
                        "max_length": getattr(field, "max_length", None),
                    }
                    for key, field in response.context["form"].fields.items()
                    if not key.startswith("password")
                ]
            return response

        def create(route_name, values, model, lookup):
            response = client.post(reverse(route_name), values)
            if response.status_code != 302:
                errors = (
                    response.context["form"].errors.as_json()
                    if response.context and "form" in response.context
                    else str(response.status_code)
                )
                raise AssertionError((route_name, errors))
            obj = model.objects.get(**lookup)
            created.append(
                {
                    "model": model._meta.label_lower,
                    "route": reverse(route_name),
                    "label": str(obj),
                }
            )
            return obj

        branch_data = {
            "name": "Training Master Branch",
            "code": "MD-BR",
            "province": "Western",
            "province_short_name": "WP",
            "is_active": "on",
        }
        screen("03-add-branch", "district_create", fill=branch_data)
        branch = create("district_create", branch_data, m.District, {"code": "MD-BR"})
        office_data = {
            "district": str(branch.pk),
            "name": "Training Zonal Office",
            "code": "MD-Z01",
            "address": "Training office address",
            "phone": "0771234567",
            "is_active": "on",
        }
        screen(
            "04-add-zonal-office",
            "office_create",
            params={"district": branch.pk},
            fill=office_data,
        )
        office = create(
            "office_create", office_data, m.OfficeLocation, {"code": "MD-Z01"}
        )

        restricted = {"district", "paymenttype", "childdeposittype", "fixdepositpolicy"}
        master_names = {
            "district",
            "officelocation",
            "subdivision",
            "school",
            "lager",
            "gender",
            "paymenttype",
            "paymentplantemplate",
            "childdeposittype",
            "fixdepositpolicy",
        }
        codes = [
            f"{action}_{name}"
            for name in master_names
            for action in ("view", "add")
            if action == "view" or name not in restricted
        ]
        permissions = list(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codes
            ).values_list("pk", flat=True)
        )
        role_data = {
            "name": "Training Master Data Operator",
            "module_permissions": [str(pk) for pk in permissions],
        }
        screen(
            "01-add-role", "access_role_create", fill=role_data, mode="top", height=1150
        )
        role = create(
            "access_role_create", role_data, Group, {"name": role_data["name"]}
        )
        user_data = {
            "username": "training.master.officer",
            "first_name": "Training",
            "last_name": "Master Officer",
            "email": "",
            "epf_number": "MD-EPF-001",
            "roles": [str(role.pk)],
            "districts": [str(branch.pk)],
            "zonal_offices": [str(office.pk)],
            "is_active": "on",
            "password1": "Training-only-" + uuid4().hex,
            "module_permissions": [],
        }
        user_data["password2"] = user_data["password1"]
        screen(
            "05-add-user",
            "access_user_create",
            fill={
                key: value
                for key, value in user_data.items()
                if not key.startswith("password")
            },
            mode="top",
            height=1110,
        )
        officer = create(
            "access_user_create",
            user_data,
            get_user_model(),
            {"username": user_data["username"]},
        )
        screen(
            "06-officer-branch-access",
            "access_user_edit",
            args=[officer.pk],
            mode="officeAccess",
        )

        parent_params = {"district": branch.pk, "zonal_office": office.pk}
        subdivision_data = {
            "district": str(branch.pk),
            "zonal_office": str(office.pk),
            "name": "Training Subdivision",
            "code": "MD-S01",
            "is_active": "on",
        }
        screen(
            "07-add-subdivision",
            "subdivision_create",
            params=parent_params,
            fill=subdivision_data,
        )
        subdivision = create(
            "subdivision_create", subdivision_data, m.Subdivision, {"code": "MD-S01"}
        )
        department_data = {
            "district": str(branch.pk),
            "zonal_office": str(office.pk),
            "subdivision": str(subdivision.pk),
            "name": "Training Department",
            "code": "MD-D01",
            "address": "Training department address",
            "is_active": "on",
        }
        screen(
            "08-add-department",
            "school_create",
            params=parent_params,
            fill=department_data,
        )
        department = create(
            "school_create", department_data, m.School, {"code": "MD-D01"}
        )
        lager_data = {
            "district": str(branch.pk),
            "zonal_office": str(office.pk),
            "name": "Training Member Ledger",
            "code": "MD-L01",
            "maintenance_officer": str(officer.pk),
            "is_active": "on",
        }
        screen("09-add-lager", "lager_create", params=parent_params, fill=lager_data)
        lager = create("lager_create", lager_data, m.Lager, {"code": "MD-L01"})
        screen("10-saved-lager", "lager_list", params={"district": branch.pk})
        gender_data = {
            "code": "training",
            "name": "Training Gender Option",
            "sort_order": "90",
            "is_active": "on",
        }
        screen("11-add-gender", "gender_create", fill=gender_data)
        create("gender_create", gender_data, m.Gender, {"code": "training"})
        savings_data = {
            "name": "Training Savings",
            "interest_rate": "8.00",
            "interest_frequency": "quarterly",
            "sort_order": "1",
            "is_active": "on",
        }
        screen("12-add-payment-type", "payment_type_create", fill=savings_data)
        savings = create(
            "payment_type_create",
            savings_data,
            m.PaymentType,
            {"name": savings_data["name"]},
        )
        fixed_data = {
            **savings_data,
            "name": "Training Fixed Deposit",
            "interest_rate": "4.00",
            "sort_order": "2",
        }
        fixed = create(
            "payment_type_create",
            fixed_data,
            m.PaymentType,
            {"name": fixed_data["name"]},
        )
        custom_data = {
            **savings_data,
            "name": "Training Custom Reference",
            "interest_frequency": "custom",
            "interest_period_type": "repeating",
            "interest_interval_count": "6",
            "interest_interval_unit": "months",
        }
        screen("13-custom-interest-reference", "payment_type_create", fill=custom_data)
        child_data = {
            "name": "Training Child Savings",
            "age_threshold": "18",
            "rate_below_threshold": "8.00",
            "rate_from_threshold": "5.00",
            "sort_order": "1",
            "is_active": "on",
        }
        screen(
            "14-add-child-deposit-type", "child_deposit_type_create", fill=child_data
        )
        create(
            "child_deposit_type_create",
            child_data,
            m.ChildDepositType,
            {"name": child_data["name"]},
        )
        pension = m.PaymentType.objects.get(name="Pension")
        funeral = m.PaymentType.objects.get(name="Funeral")
        plan_data = {
            "name": "Training Monthly Plan",
            "description": "Training example only: agreed monthly contributions.",
            "is_active": "on",
            f"amount_{savings.pk}": "500",
            f"amount_{fixed.pk}": "200",
            f"fixed_{fixed.pk}": "on",
            f"amount_{pension.pk}": "200",
            f"amount_{funeral.pk}": "100",
        }
        screen(
            "15-add-plan-template",
            "payment_plan_template_create",
            fill=plan_data,
            mode="top",
            height=1130,
        )
        template = create(
            "payment_plan_template_create",
            plan_data,
            m.PaymentPlanTemplate,
            {"name": plan_data["name"]},
        )
        assert template.monthly_total == 1000
        screen(
            "16-template-total-and-save",
            "payment_plan_template_create",
            fill=plan_data,
            mode="templateTotal",
        )
        policy_data = {
            "name": "Training 36-Month Rule",
            "payment_type": str(fixed.pk),
            "maturity_months": "36",
            "withdrawal_percent": "75",
            "loan_percent": "90",
            "loan_interval_months": "12",
            "loan_interest_rate": "12.00",
            "is_active": "on",
        }
        screen(
            "17-add-fixed-deposit-rule", "fix_deposit_policy_create", fill=policy_data
        )
        create(
            "fix_deposit_policy_create",
            policy_data,
            m.FixDepositPolicy,
            {"name": policy_data["name"]},
        )
        hierarchy_data = {
            "name": "Training master record edits",
            "module": "all_modules",
            "action": "change",
            "is_active": "on",
            "reason": "Training: review changes to existing master records.",
            "levels-TOTAL_FORMS": "1",
            "levels-INITIAL_FORMS": "0",
            "levels-MIN_NUM_FORMS": "0",
            "levels-MAX_NUM_FORMS": "20",
            "levels-0-position": "1",
            "levels-0-name": "Master data review",
            "levels-0-approvers": [str(admin.pk)],
            "levels-0-allow_rejection": "on",
        }
        screen(
            "18-create-edit-hierarchy",
            "approval_hierarchy_create",
            fill=hierarchy_data,
            mode="top",
            height=1100,
        )
        create(
            "approval_hierarchy_create",
            hierarchy_data,
            m.ApprovalHierarchy,
            {"name": hierarchy_data["name"]},
        )
        screen("19-duplicate-branch", "district_list", params={"q": "MD-BR"})
        screen("20-master-data-menu", "dashboard", mode="sidebar")
        screen(
            "21-master-audit",
            "audit_log_list",
            params={"q": "Training"},
            mode="top",
            height=1100,
        )
        for name, values in (
            ("api-locations", {"district": branch.pk}),
            ("api-offices", {"district": branch.pk}),
        ):
            route = "location_options" if name == "api-locations" else "office_options"
            response = client.get(reverse(route), values)
            assert response.status_code == 200
            (HTML / f"{name}.json").write_bytes(response.content)

        ordinary = Client()
        ordinary.force_login(officer)
        restricted_checks = {}
        for route, model in (
            ("district_create", m.District),
            ("payment_type_create", m.PaymentType),
            ("child_deposit_type_create", m.ChildDepositType),
            ("fix_deposit_policy_create", m.FixDepositPolicy),
        ):
            officer.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="members",
                    codename=f"add_{model._meta.model_name}",
                )
            )
            status = ordinary.get(reverse(route)).status_code
            assert status == 403, (route, status)
            restricted_checks[route] = status
        for route in (
            "office_create",
            "subdivision_create",
            "school_create",
            "lager_create",
            "gender_create",
            "payment_plan_template_create",
        ):
            assert ordinary.get(reverse(route)).status_code == 200, route
        duplicate = client.post(reverse("district_create"), branch_data)
        assert duplicate.status_code == 200 and duplicate.context["form"].errors
        (HTML / "22-duplicate-validation.html").write_bytes(duplicate.content)
        captures.append(
            ["22-duplicate-validation", "22-duplicate-validation", "top", 1000, {}]
        )
        actual_masters = [
            label
            for model, label, section, *_ in MODULES
            if model._meta.model_name in master_names
        ]
        assert len(actual_masters) == 10
        metadata = {
            "prepared_on": "2026-11-26",
            "timezone": "Asia/Colombo",
            "notice": "Current system screenshots with fictional training data in a separate disposable PostgreSQL database. No live master or user data changed.",
            "masters": actual_masters,
            "created": created,
            "fields": field_catalog,
            "captures": captures,
            "restricted_create_status": restricted_checks,
            "verified_template_total": str(template.monthly_total),
            "ledger_officer_epf": officer.ledger_profile.epf_number,
            "temporary_database_removed_on_exit": True,
        }
        (ASSETS / "capture-evidence.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "master_areas": len(actual_masters),
                    "verified_creations": len(created),
                    "screens": len(captures),
                    "admin_restrictions": restricted_checks,
                    "template_total": str(template.monthly_total),
                }
            )
        )
    finally:
        runner.teardown_databases(old)
        teardown_test_environment()


if __name__ == "__main__":
    main()

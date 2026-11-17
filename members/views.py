import csv
from .approval_services import form_checkpoint, checkpoint
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from .forms import (
    DistrictForm,
    LagerForm,
    MemberForm,
    OfficeLocationForm,
    SchoolForm,
    SubdivisionForm,
    school_choices,
    selected_id,
)
from .models import (
    District,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    PaymentPlan,
    School,
    Subdivision,
)
from .permissions import (
    accessible_districts,
    district_records,
    require_district,
    require_location_access,
    require_permission,
)
from .officers import officer_options


def location_data(district_id, instance=None, *, user=None, receiving=False):
    if not str(district_id or "").isdigit() or (
        user is not None
        and not accessible_districts(user).filter(pk=district_id).exists()
    ):
        return {"offices": [], "subdivisions": [], "schools": [], "lagers": []}
    current_office = getattr(instance, "office_location_id", None) or getattr(
        instance, "zonal_office_id", None
    )
    offices = OfficeLocation.objects.filter(district_id=district_id).filter(
        Q(is_active=True, district__is_active=True) | Q(pk=current_office)
    )
    subdivisions = (
        Subdivision.objects.select_related("zonal_office")
        .filter(zonal_office__district_id=district_id)
        .filter(
            Q(
                is_active=True,
                zonal_office__is_active=True,
                zonal_office__district__is_active=True,
            )
            | Q(pk=getattr(instance, "subdivision_id", None))
        )
    )
    schools = (
        School.objects.select_related("district", "zonal_office", "subdivision")
        .filter(district_id=district_id)
        .filter(
            Q(is_active=True, district__is_active=True, zonal_office__is_active=True)
            | Q(pk=getattr(instance, "school_id", None))
        )
    )
    schools = schools.filter(
        Q(subdivision__isnull=True)
        | Q(subdivision__is_active=True)
        | Q(pk=getattr(instance, "school_id", None))
    )
    lagers = Lager.objects.filter(zonal_office__district_id=district_id).filter(
        Q(
            is_active=True,
            zonal_office__is_active=True,
            zonal_office__district__is_active=True,
        )
        | Q(pk=getattr(instance, "lager_id", None))
    )
    if user is not None and not receiving:
        from .permissions import scope_queryset

        lagers = scope_queryset(user, lagers)
    return {
        "officers": officer_options_for_district(district_id),
        "lagers": list(
            lagers.values(
                "id", "name", "code", "zonal_office_id", "maintenance_officer_id"
            ).order_by("name", "pk")
        ),
        "offices": list(
            offices.values("id", "name", "address", "phone").order_by("name")
        ),
        "subdivisions": list(
            subdivisions.values("id", "name", "zonal_office_id").order_by("name")
        ),
        "schools": [
            {
                "id": school.pk,
                "name": str(school),
                "zonal_office_id": school.zonal_office_id,
                "subdivision_id": school.subdivision_id,
                "address": school.address,
            }
            for school in schools
        ],
    }


def officer_options_for_district(district_id):
    from django.contrib.auth import get_user_model
    from django.db.models import F

    return [
        {
            "id": user.pk,
            "name": f"{user.get_full_name() or user.username} · EPF {user.ledger_profile.epf_number}",
            "zonal_office_id": user.ledger_profile.zonal_office_id,
        }
        for user in get_user_model()
        .objects.filter(
            is_active=True,
            ledger_profile__zonal_office__district_id=district_id,
            ledger_profile__epf_number__isnull=False,
        )
        .exclude(ledger_profile__epf_number="")
        .filter(
            Q(is_superuser=True)
            | Q(assigned_districts=F("ledger_profile__zonal_office__district_id"))
        )
        .select_related("ledger_profile")
        .distinct()
    ]


def location_context(form):
    districts = (
        accessible_districts(form.user)
        if form.user is not None
        else District.objects.all()
    )
    provinces = list(districts.values_list("pk", "province", "province_short_name"))
    return {
        "district_provinces": {pk: name for pk, name, short_name in provinces},
        "district_province_short_names": {
            pk: short_name for pk, name, short_name in provinces
        },
        "initial_locations": location_data(
            selected_id(form, "district"),
            form.instance,
            user=form.user,
            receiving=getattr(form, "ledger_receiving_locations", False),
        ),
        "ledger_officers": officer_options() if provinces else [],
    }


def member_school_data(form):
    return [
        {
            "id": school.pk,
            "name": str(school),
            "code": school.code,
            "district_id": school.district_id,
            "zonal_office_id": school.zonal_office_id,
            "zonal_office_name": school.zonal_office.name,
            "subdivision_id": school.subdivision_id,
            "subdivision_name": (
                school.subdivision.name if school.subdivision_id else ""
            ),
            "address": school.address,
            "office_address": school.zonal_office.address,
            "phone": school.zonal_office.phone,
        }
        for school in school_choices(form.instance.school_id, user=form.user)
    ]


def paginated(request, queryset):
    params = request.GET.copy()
    params.pop("page", None)
    return {
        "page_obj": Paginator(queryset, 10).get_page(request.GET.get("page")),
        "query_string": params.urlencode(),
    }


def filtered_members(request):
    queryset = district_records(request.user, Member).select_related(
        "district",
        "lager__zonal_office__district",
        "office_location",
        "subdivision",
        "school__district",
        "school__zonal_office",
        "school__subdivision",
    )
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(short_name__icontains=query)
            | Q(member_no__icontains=query)
            | Q(nic_no__icontains=query)
            | Q(computer_no__icontains=query)
            | Q(phone_mobile__icontains=query)
        )
    for key, field in [
        ("district", "district_id"),
        ("lager", "lager_id"),
        ("office", "office_location_id"),
        ("school", "school_id"),
    ]:
        value = request.GET.get(key, "")
        if value.isdigit():
            queryset = queryset.filter(**{field: value})
    if request.GET.get("status") in Member.Status.values:
        queryset = queryset.filter(status=request.GET["status"])
    if request.GET.get("gender") in Member.Gender.values:
        queryset = queryset.filter(gender=request.GET["gender"])
    for key, lookup in [("from", "admit_date__gte"), ("to", "admit_date__lte")]:
        try:
            value = date.fromisoformat(request.GET.get(key, ""))
        except ValueError:
            continue
        queryset = queryset.filter(**{lookup: value})
    sorting = {"name": "full_name", "oldest": "created_at", "admitted": "-admit_date"}
    return queryset.order_by(sorting.get(request.GET.get("sort"), "-created_at"), "-pk")


@login_required
def dashboard(request):
    if not request.user.has_perm("members.view_member"):
        return render(
            request,
            "members/workspace.html",
            {"section": "dashboard", "title": "Your workspace"},
        )
    today = timezone.localdate()
    months = []
    for offset in range(5, -1, -1):
        index = today.year * 12 + today.month - 1 - offset
        months.append(date(index // 12, index % 12 + 1, 1))
    aggregates = (
        district_records(request.user, Member)
        .filter(admit_date__gte=months[0])
        .annotate(month=TruncMonth("admit_date"))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    )
    counts = {item["month"].strftime("%Y-%m"): item["total"] for item in aggregates}
    peak = max(counts.values(), default=0) or 1
    chart = [
        {
            "label": month.strftime("%b"),
            "count": counts.get(month.strftime("%Y-%m"), 0),
            "height": round(counts.get(month.strftime("%Y-%m"), 0) / peak * 100),
        }
        for month in months
    ]
    total = district_records(request.user, Member).count()
    districts = list(
        district_records(request.user, District)
        .annotate(
            member_count=Count(
                "members", filter=Q(members__in=district_records(request.user, Member))
            )
        )
        .filter(member_count__gt=0)
        .order_by("-member_count", "name")[:5]
    )
    for district in districts:
        district.percent = round(district.member_count / total * 100) if total else 0
    return render(
        request,
        "members/dashboard.html",
        {
            "section": "dashboard",
            "title": "Overview",
            "total_members": total,
            "active_members": district_records(request.user, Member)
            .filter(status="active")
            .count(),
            "district_count": district_records(request.user, District)
            .filter(is_active=True)
            .count(),
            "office_count": district_records(request.user, OfficeLocation)
            .filter(is_active=True, district__is_active=True)
            .count(),
            "this_month": district_records(request.user, Member)
            .filter(admit_date__gte=today.replace(day=1), admit_date__lte=today)
            .count(),
            "recent_members": district_records(request.user, Member).select_related(
                "district", "office_location"
            )[:5],
            "chart": chart,
            "chart_total": sum(row["count"] for row in chart),
            "chart_start": months[0],
            "districts": districts,
            "today": today,
        },
    )


@login_required
def member_list(request):
    require_permission(request.user, Member, "view")
    queryset = filtered_members(request)
    district_id = request.GET.get("district", "")
    offices = district_records(request.user, OfficeLocation).select_related("district")
    lagers = district_records(request.user, Lager).select_related(
        "zonal_office__district"
    )
    if district_id.isdigit():
        offices = offices.filter(district_id=district_id)
        lagers = lagers.filter(zonal_office__district_id=district_id)
    office_id = request.GET.get("office", "")
    if office_id.isdigit():
        lagers = lagers.filter(zonal_office_id=office_id)
    context = {
        "section": "members",
        "title": "Member directory",
        "total_members": district_records(request.user, Member).count(),
        "filtered_count": queryset.count(),
        "districts": district_records(request.user, District).all(),
        "offices": offices,
        "lagers": lagers,
        "all_lagers": [
            {
                "id": lager.pk,
                "name": lager.name,
                "code": lager.code,
                "district_id": lager.district_id,
                "district__name": lager.district.name,
                "zonal_office_id": lager.zonal_office_id,
                "zonal_office__name": lager.zonal_office.name,
            }
            for lager in district_records(request.user, Lager).select_related(
                "zonal_office__district"
            )
        ],
        "statuses": Member.Status.choices,
        "genders": Member.Gender.choices,
    }
    context.update(paginated(request, queryset))
    return render(request, "members/member_list.html", context)


@login_required
def member_form(request, pk=None):
    require_permission(request.user, Member, "change" if pk is not None else "add")
    member = (
        get_object_or_404(district_records(request.user, Member), pk=pk)
        if pk is not None
        else None
    )
    form = MemberForm(
        request.POST if request.method == "POST" else None,
        instance=member,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        member = form.save()
        messages.success(
            request,
            f'{member.full_name} was successfully {"updated" if pk is not None else "registered"}.',
        )
        if (
            pk is None
            and request.POST.get("next_step") == "payment_plan"
            and request.user.has_perm("members.add_paymentplan")
        ):
            return redirect(f'{reverse("payment_plan_create")}?member={member.pk}')
        if request.user.has_perm("members.view_member"):
            return redirect("member_detail", pk=member.pk)
        if pk is not None:
            return redirect("member_edit", pk=member.pk)
        return redirect("member_create")
    return render(
        request,
        "members/member_form.html",
        {
            "section": "members",
            "title": "Edit member" if pk is not None else "Register member",
            "form": form,
            "member": member,
            "today": timezone.localdate(),
            "has_offices": district_records(request.user, OfficeLocation)
            .filter(is_active=True, district__is_active=True)
            .exists(),
            "member_schools": member_school_data(form),
            **location_context(form),
        },
    )


@login_required
def member_detail(request, pk):
    require_permission(request.user, Member, "view")
    member = get_object_or_404(
        district_records(request.user, Member).select_related(
            "district",
            "lager__zonal_office__district",
            "office_location",
            "subdivision",
            "school__district",
            "school__zonal_office",
            "school__subdivision",
        ),
        pk=pk,
    )
    return render(
        request,
        "members/member_detail.html",
        {
            "section": "members",
            "title": "Member profile",
            "member": member,
            "member_payment_plan": (
                PaymentPlan.objects.filter(member=member).first()
                if any(
                    request.user.has_perm(f"members.{action}_paymentplan")
                    for action in ("view", "add", "change", "delete")
                )
                else None
            ),
            "member_opening_balance": (
                OpeningBalance.objects.filter(member=member)
                .prefetch_related("amounts")
                .first()
                if any(
                    request.user.has_perm(f"members.{action}_openingbalance")
                    for action in ("view", "add", "change", "delete")
                )
                else None
            ),
        },
    )


@login_required
def member_export(request):
    require_permission(request.user, Member, "view")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="members-{timezone.localdate().isoformat()}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "Member No",
            "Computer No",
            "Full Name",
            "Monthly Salary (LKR)",
            "District",
            "Zonal Office",
            "Office Address",
            "Private Address",
            "Office Phone",
            "Mobile Phone",
            "Home Phone",
            "Admission Date",
            "Gender",
            "NIC No",
            "Status",
            "Province",
            "Subdivision",
            "Department",
            "Lager Code",
            "Lager",
            "Province Short Name",
            "Title",
            "Short Name",
            "Date of Birth",
            "Retirement Age",
            "Expected Retirement Date",
            "Recorded Retirement Date",
        ]
    )
    for member in filtered_members(request).iterator():
        row = [
            member.member_no,
            member.computer_no or "",
            member.full_name,
            member.salary,
            member.district.name,
            member.office_location.name,
            member.office_address,
            member.private_address,
            member.phone_office,
            member.phone_mobile,
            member.phone_home,
            member.admit_date.isoformat(),
            member.get_gender_display(),
            member.nic_no,
            member.get_status_display(),
            member.district.province,
            member.subdivision.name if member.subdivision_id else "",
            str(member.school) if member.school_id else "",
            member.lager.code if member.lager_id else "",
            member.lager.name if member.lager_id else "",
            member.district.province_short_name,
            member.title,
            member.short_name,
            member.date_of_birth.isoformat() if member.date_of_birth else "",
            member.retirement_age,
            (
                member.expected_retirement_date.isoformat()
                if member.expected_retirement_date
                else ""
            ),
            member.pension_date.isoformat() if member.pension_date else "",
        ]
        writer.writerow(
            [
                (
                    f"'{value}"
                    if isinstance(value, str)
                    and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                )
                for value in row
            ]
        )
    return response


def delete_record(request, obj, back, section):
    if request.method == "POST":
        queued = checkpoint(
            request,
            type(obj),
            "delete",
            f"Delete {obj._meta.verbose_name}: {obj}",
            target=obj,
        )
        if queued is not None:
            return queued
        name = str(obj)
        try:
            obj.delete()
        except ProtectedError:
            messages.error(
                request,
                "This record is in use. Reassign its linked records or mark it inactive instead.",
            )
        else:
            messages.success(request, f"{name} was deleted.")
        if request.user.has_perm(f"{obj._meta.app_label}.view_{obj._meta.model_name}"):
            return redirect(back)
        return redirect("dashboard")
    return render(
        request,
        "members/confirm_delete.html",
        {
            "title": "Delete record",
            "object": obj,
            "back": back,
            "section": section,
            "can_view_list": request.user.has_perm(
                f"{obj._meta.app_label}.view_{obj._meta.model_name}"
            ),
        },
    )


@login_required
def member_delete(request, pk):
    require_permission(request.user, Member, "delete")
    return delete_record(
        request,
        get_object_or_404(district_records(request.user, Member), pk=pk),
        "member_list",
        "members",
    )


@login_required
def district_list(request):
    require_permission(request.user, District, "view")
    queryset = (
        district_records(request.user, District)
        .annotate(
            office_count=Count("offices", distinct=True),
            member_count=Count(
                "members",
                distinct=True,
                filter=Q(members__in=district_records(request.user, Member)),
            ),
            school_count=Count("schools", distinct=True),
            lager_count=Count("offices__lagers", distinct=True),
        )
        .order_by("name", "pk")
    )
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(province__icontains=query)
            | Q(province_short_name__icontains=query)
        )
    context = {
        "section": "districts",
        "title": "Districts",
        "record_count": district_records(request.user, District).count(),
    }
    context.update(paginated(request, queryset))
    return render(request, "members/district_list.html", context)


@login_required
def office_list(request):
    require_permission(request.user, OfficeLocation, "view")
    queryset = (
        district_records(request.user, OfficeLocation)
        .select_related("district")
        .annotate(
            member_count=Count(
                "members",
                distinct=True,
                filter=Q(members__in=district_records(request.user, Member)),
            ),
            subdivision_count=Count("subdivisions", distinct=True),
            lager_count=Count("lagers", distinct=True),
        )
        .order_by("district__name", "name", "pk")
    )
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(address__icontains=query)
        )
    district = request.GET.get("district", "")
    if district.isdigit():
        queryset = queryset.filter(district_id=district)
    context = {
        "section": "offices",
        "title": "Zonal offices",
        "record_count": district_records(request.user, OfficeLocation).count(),
        "districts": district_records(request.user, District).all(),
    }
    context.update(paginated(request, queryset))
    return render(request, "members/office_list.html", context)


def master_form(request, model, form_class, pk, section, singular, destination):
    require_permission(request.user, model, "change" if pk is not None else "add")
    obj = (
        get_object_or_404(district_records(request.user, model), pk=pk)
        if pk is not None
        else None
    )
    initial = {}
    if request.GET.get("district", "").isdigit():
        initial["district"] = request.GET["district"]
    if request.GET.get("zonal_office", "").isdigit():
        initial["zonal_office"] = request.GET["zonal_office"]
        office = (
            district_records(request.user, OfficeLocation)
            .filter(pk=initial["zonal_office"])
            .first()
        )
        if office:
            initial["district"] = office.district_id
    form = form_class(
        request.POST if request.method == "POST" else None,
        instance=obj,
        initial=initial,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        form.save()
        messages.success(
            request,
            f'{singular} {"updated" if pk is not None else "created"} successfully.',
        )
        if request.user.has_perm(f"members.view_{model._meta.model_name}"):
            return redirect(destination)
        return redirect("dashboard")
    return render(
        request,
        "members/master_form.html",
        {
            "section": section,
            "title": f'{"Edit" if pk is not None else "Add"} {singular.lower()}',
            "form": form,
            "back": destination,
            "singular": singular,
            "can_view_list": request.user.has_perm(
                f"members.view_{model._meta.model_name}"
            ),
            **location_context(form),
        },
    )


@login_required
def district_form(request, pk=None):
    return master_form(
        request, District, DistrictForm, pk, "districts", "District", "district_list"
    )


@login_required
def office_form(request, pk=None):
    return master_form(
        request,
        OfficeLocation,
        OfficeLocationForm,
        pk,
        "offices",
        "Zonal office",
        "office_list",
    )


@login_required
def district_delete(request, pk):
    require_permission(request.user, District, "delete")
    return delete_record(
        request,
        get_object_or_404(district_records(request.user, District), pk=pk),
        "district_list",
        "districts",
    )


@login_required
def office_delete(request, pk):
    require_permission(request.user, OfficeLocation, "delete")
    return delete_record(
        request,
        get_object_or_404(district_records(request.user, OfficeLocation), pk=pk),
        "office_list",
        "offices",
    )


@login_required
def office_options(request):
    require_location_access(request.user)
    district = request.GET.get("district", "")
    if district.isdigit():
        require_district(request.user, district)
    queryset = (
        district_records(request.user, OfficeLocation)
        .filter(district_id=district, is_active=True, district__is_active=True)
        .order_by("name")
        if district.isdigit()
        else district_records(request.user, OfficeLocation).none()
    )
    return JsonResponse(
        {"offices": list(queryset.values("id", "name", "address", "phone"))}
    )


@login_required
def location_options(request):
    require_location_access(request.user)
    district = request.GET.get("district", "")
    if district.isdigit():
        require_district(request.user, district)
    receiving = request.GET.get("purpose") == "transfer"
    if receiving:
        from .models import MemberTransfer

        require_permission(request.user, MemberTransfer, "add")
    return JsonResponse(location_data(district, user=request.user, receiving=receiving))


EXTRA_MASTERS = {
    "lagers": (Lager, LagerForm, "Lager", "lager"),
    "subdivisions": (Subdivision, SubdivisionForm, "Subdivision", "subdivision"),
    "schools": (School, SchoolForm, "Department", "school"),
}


@login_required
def extra_master_list(request, kind):
    model, _, singular, prefix = EXTRA_MASTERS[kind]
    require_permission(request.user, model, "view")
    if model in (Subdivision, Lager):
        queryset = district_records(request.user, model).select_related(
            "zonal_office__district"
        )
        district_field = "zonal_office__district_id"
        if model is Lager:
            queryset = queryset.select_related("maintenance_officer__ledger_profile")
            queryset = queryset.annotate(member_count=Count("members")).order_by(
                "zonal_office__district__name", "zonal_office__name", "name", "pk"
            )
    else:
        queryset = district_records(request.user, model).select_related("district")
        district_field = "district_id"
        if model is School:
            queryset = queryset.select_related("zonal_office", "subdivision")
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))
    district = request.GET.get("district", "")
    if district.isdigit():
        queryset = queryset.filter(**{district_field: district})
    office = request.GET.get("zonal_office", "")
    if office.isdigit():
        queryset = queryset.filter(zonal_office_id=office)
    context = {
        "section": kind,
        "title": str(model._meta.verbose_name_plural).capitalize(),
        "singular": singular,
        "list_url": f"{prefix}_list",
        "create_url": f"{prefix}_create",
        "edit_url": f"{prefix}_edit",
        "delete_url": f"{prefix}_delete",
        "record_count": district_records(request.user, model).count(),
        "can_add": request.user.has_perm(f"members.add_{model._meta.model_name}"),
        "can_change": request.user.has_perm(f"members.change_{model._meta.model_name}"),
        "can_delete": request.user.has_perm(f"members.delete_{model._meta.model_name}"),
        "filtered_count": queryset.count(),
        "districts": district_records(request.user, District).all(),
        "zonal_offices": district_records(request.user, OfficeLocation)
        .filter(**({"district_id": district} if district.isdigit() else {}))
        .select_related("district"),
    }
    context.update(paginated(request, queryset))
    return render(request, "members/extra_master_list.html", context)


@login_required
def extra_master_form(request, kind, pk=None):
    model, form_class, singular, prefix = EXTRA_MASTERS[kind]
    return master_form(request, model, form_class, pk, kind, singular, f"{prefix}_list")


@login_required
def extra_master_delete(request, kind, pk):
    model, _, _, prefix = EXTRA_MASTERS[kind]
    require_permission(request.user, model, "delete")
    return delete_record(
        request,
        get_object_or_404(district_records(request.user, model), pk=pk),
        f"{prefix}_list",
        kind,
    )

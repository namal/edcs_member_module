from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count
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
from .notifications import notify_user_created

admin.site.unregister(get_user_model())


class DistrictAccessInline(admin.TabularInline):
    model = District.authorized_users.through
    extra = 0
    verbose_name = "assigned district"
    verbose_name_plural = "assigned districts"


@admin.register(get_user_model())
class WorkspaceUserAdmin(UserAdmin):
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Contact details", {"fields": ("first_name", "last_name", "email")}),
    )

    def get_inlines(self, request, obj=None):
        return [DistrictAccessInline] if obj is not None else []

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            notify_user_created(request, form.instance)


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    exclude = ["authorized_users"]
    list_display = ["name", "code", "province", "province_short_name", "is_active"]
    search_fields = ["name", "code", "province", "province_short_name"]
    list_filter = ["is_active", "province"]


@admin.register(OfficeLocation)
class OfficeLocationAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "district", "is_active"]
    list_filter = ["district", "is_active"]
    search_fields = ["name", "code"]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = [
        "member_no",
        "full_name",
        "district",
        "lager",
        "office_location",
        "status",
        "admit_date",
    ]
    list_filter = ["district", "lager", "status", "gender"]
    search_fields = ["member_no", "full_name", "nic_no", "computer_no"]
    list_select_related = ["district", "lager__district", "office_location"]
    date_hierarchy = "admit_date"


@admin.register(Subdivision)
class SubdivisionAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "zonal_office", "is_active"]
    list_filter = ["zonal_office__district", "zonal_office", "is_active"]
    search_fields = ["name", "code"]
    list_select_related = ["zonal_office__district"]


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "code",
        "district",
        "zonal_office",
        "subdivision",
        "is_active",
    ]
    list_filter = ["district", "zonal_office", "is_active"]
    search_fields = ["name", "code"]
    list_select_related = ["district", "zonal_office", "subdivision"]


@admin.register(Lager)
class LagerAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "district", "member_count", "is_active"]
    list_filter = ["district", "is_active"]
    search_fields = ["name", "code"]
    list_select_related = ["district"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(registered_member_count=Count("members"))
        )

    @admin.display(description="Registered members", ordering="registered_member_count")
    def member_count(self, obj):
        return obj.registered_member_count


@admin.register(PaymentType)
class PaymentTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "sort_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]


class PaymentPlanAmountInline(admin.TabularInline):
    model = PaymentPlanAmount
    extra = 0


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = ["member", "agreement_date", "monthly_total"]
    list_select_related = ["member"]
    search_fields = ["member__member_no", "member__full_name"]
    autocomplete_fields = ["member"]
    inlines = [PaymentPlanAmountInline]

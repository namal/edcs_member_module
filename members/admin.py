from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count
from .models import (
    Child,
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
    District,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentPlan,
    PaymentPlanAmount,
    PaymentTransaction,
    PaymentType,
    School,
    Subdivision,
)
from .notifications import notify_user_created

admin.site.unregister(get_user_model())


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ["name", "member", "date_of_birth", "is_active"]
    list_filter = ["member__district", "is_active"]
    search_fields = ["name", "member__member_no", "member__full_name"]
    autocomplete_fields = ["member"]
    list_select_related = ["member"]


@admin.register(ChildDepositType)
class ChildDepositTypeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "age_threshold",
        "rate_below_threshold",
        "rate_from_threshold",
        "is_active",
    ]
    search_fields = ["name"]
    list_filter = ["is_active"]


@admin.register(ChildDeposit)
class ChildDepositAdmin(admin.ModelAdmin):
    list_display = [
        "account_no",
        "child",
        "deposit_type",
        "opened_on",
        "opening_amount",
        "is_active",
    ]
    list_filter = ["deposit_type", "child__member__district", "is_active"]
    search_fields = ["account_no", "child__name", "child__member__member_no"]
    autocomplete_fields = ["child", "deposit_type"]
    list_select_related = ["child__member", "deposit_type"]
    readonly_fields = ["account_no", "created_at", "updated_at"]


@admin.register(ChildDepositTransaction)
class ChildDepositTransactionAdmin(admin.ModelAdmin):
    list_display = ["deposit", "transaction_date", "debit", "credit", "interest_rate"]
    list_filter = ["transaction_date", "deposit__child__member__district"]
    search_fields = ["deposit__account_no", "deposit__child__name", "reference"]
    autocomplete_fields = ["deposit"]
    list_select_related = ["deposit__child", "deposit__deposit_type"]
    readonly_fields = ["interest_rate", "created_by", "created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        return [*self.readonly_fields, "deposit"] if obj else self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change or "transaction_date" in form.changed_data:
            obj.interest_rate = obj.deposit.rate_on(obj.transaction_date)
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


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
    list_display = [
        "name",
        "interest_rate",
        "interest_period_display",
        "sort_order",
        "is_active",
    ]
    list_filter = ["is_active", "interest_frequency"]
    search_fields = ["name"]


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "transaction_date",
        "member",
        "payment_type",
        "debit",
        "credit",
        "reference",
    ]
    list_filter = ["payment_type", "transaction_date", "member__district"]
    search_fields = ["member__member_no", "member__full_name", "reference"]
    list_select_related = ["member", "payment_type"]
    autocomplete_fields = ["member", "payment_type"]
    readonly_fields = ["created_by", "created_at", "updated_at"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


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


class OpeningBalanceAmountInline(admin.TabularInline):
    model = OpeningBalanceAmount
    extra = 0


@admin.register(OpeningBalance)
class OpeningBalanceAdmin(admin.ModelAdmin):
    list_display = ["member", "balance_date", "total"]
    list_select_related = ["member"]
    search_fields = ["member__member_no", "member__full_name"]
    autocomplete_fields = ["member"]
    inlines = [OpeningBalanceAmountInline]

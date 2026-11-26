from django.contrib import admin
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count
from .models import (
    Child,
    ChildDeposit,
    ChildDepositInterestYear,
    ChildDepositTransaction,
    ChildDepositType,
    District,
    DepositInterestYear,
    FixDepositAccount,
    FixDepositLoan,
    FixDepositPolicy,
    FixDepositWithdrawal,
    FixLoanEntry,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentPlan,
    PaymentPlanAmount,
    PaymentPlanTemplate,
    PaymentPlanTemplateAmount,
    PaymentTransaction,
    PaymentType,
    School,
    Subdivision,
    LedgerOfficerProfile,
    MemberLedger,
    MemberTransfer,
    LedgerOfficerAssignment,
    LedgerRecord,
    LedgerEvent,
)
from .notifications import notify_user_created
from .forms import LagerForm

admin.site.unregister(get_user_model())


@admin.register(DepositInterestYear)
class DepositInterestYearAdmin(admin.ModelAdmin):
    list_display = [
        "member",
        "payment_type",
        "year_start",
        "annual_rate",
        "annual_interest",
        "posted_at",
    ]
    list_filter = ["year_start", "payment_type"]
    search_fields = ["member__member_no", "member__full_name"]
    readonly_fields = [field.name for field in DepositInterestYear._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChildDepositInterestYear)
class ChildDepositInterestYearAdmin(admin.ModelAdmin):
    list_display = ["deposit", "year_start", "annual_interest", "posted_at"]
    list_filter = ["year_start", "deposit__deposit_type"]
    search_fields = [
        "deposit__account_no",
        "deposit__child__name",
        "deposit__child__member__member_no",
    ]
    readonly_fields = [field.name for field in ChildDepositInterestYear._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FixDepositPolicy)
class FixDepositPolicyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "payment_type",
        "maturity_months",
        "withdrawal_percent",
        "loan_percent",
        "loan_interest_rate",
        "is_active",
    ]
    search_fields = ["name", "payment_type__name"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(FixDepositAccount)
class FixDepositAccountAdmin(admin.ModelAdmin):
    list_display = ["member", "policy", "cycle_started_on", "is_active"]
    search_fields = ["member__member_no", "member__full_name"]
    list_select_related = ["member", "policy"]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                ["member", "policy", "cycle_started_on"]
                if obj.loans.exists() or obj.withdrawals.exists()
                else ["member", "policy"]
            )
        return []


class FixFinancialRecordAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FixDepositLoan)
class FixDepositLoanAdmin(FixFinancialRecordAdmin):
    list_display = [
        "reference",
        "account",
        "issued_on",
        "principal",
        "interest_rate",
        "settled_on",
    ]
    search_fields = ["reference", "account__member__member_no"]
    list_select_related = ["account__member"]


@admin.register(FixLoanEntry)
class FixLoanEntryAdmin(FixFinancialRecordAdmin):
    list_display = ["loan", "entry_date", "kind", "principal_amount", "interest_amount"]
    list_select_related = ["loan"]


@admin.register(FixDepositWithdrawal)
class FixDepositWithdrawalAdmin(FixFinancialRecordAdmin):
    list_display = ["account", "requested_on", "amount", "status", "released_on"]
    list_select_related = ["account__member"]


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
    def has_delete_permission(self, request, obj=None):
        return False

    list_display = ["deposit", "transaction_date", "debit", "credit", "interest_rate"]
    list_filter = ["transaction_date", "deposit__child__member__district"]
    search_fields = ["deposit__account_no", "deposit__child__name", "reference"]
    autocomplete_fields = ["deposit"]
    list_select_related = ["deposit__child", "deposit__deposit_type"]
    readonly_fields = ["interest_rate", "created_by", "created_at", "updated_at"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        if obj and hasattr(obj, "receipt_allocation"):
            return [field.name for field in obj._meta.fields]
        return (
            [*self.readonly_fields, "deposit", "reverses"]
            if obj
            else self.readonly_fields
        )

    def save_model(self, request, obj, form, change):
        if not change or "transaction_date" in form.changed_data:
            obj.interest_rate = obj.deposit.rate_on(obj.transaction_date)
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class DistrictAccessInline(admin.TabularInline):
    model = District.authorized_users.through
    extra = 0
    verbose_name = "assigned branch"
    verbose_name_plural = "assigned branches"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "district":
            kwargs["label"] = "Branch"
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class LedgerOfficerProfileInline(admin.StackedInline):
    model = LedgerOfficerProfile
    extra = 0
    max_num = 1
    fields = ["epf_number", "assigned_zonal_offices"]
    readonly_fields = ["assigned_zonal_offices"]

    @admin.display(description="Assigned zonal offices")
    def assigned_zonal_offices(self, obj):
        if obj is None or not obj.pk:
            return "Manage zonal offices in Administration → Users & access."
        return (
            ", ".join(
                str(office) for office in obj.zonal_offices.select_related("district")
            )
            or "Manage zonal offices in Administration → Users & access."
        )


@admin.register(get_user_model())
class WorkspaceUserAdmin(UserAdmin):
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Contact details", {"fields": ("first_name", "last_name", "email")}),
    )

    def get_inlines(self, request, obj=None):
        return (
            [DistrictAccessInline, LedgerOfficerProfileInline]
            if obj is not None
            else []
        )

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
    def has_add_permission(self, request):
        return False  # Registration must create an independent activation request.

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "gender":
            from .models import Gender

            kwargs.pop("request", None)
            return forms.ChoiceField(
                choices=[
                    ("", "Select a gender"),
                    *Gender.objects.values_list("code", "name"),
                ]
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

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
    search_fields = ["member_no", "full_name", "short_name", "nic_no", "computer_no"]
    readonly_fields = [
        "short_name",
        "retirement_age",
        "expected_retirement_date",
        "status",
    ]
    list_select_related = [
        "district",
        "lager__zonal_office__district",
        "office_location",
    ]
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
        "location",
        "code",
        "district",
        "zonal_office",
        "subdivision",
        "is_active",
    ]
    list_filter = ["district", "zonal_office", "is_active"]
    search_fields = ["name", "location", "code"]
    list_select_related = ["district", "zonal_office", "subdivision"]


@admin.register(Lager)
class LagerAdmin(admin.ModelAdmin):
    form = LagerForm
    list_display = [
        "name",
        "code",
        "zonal_office",
        "district",
        "maintenance_officer",
        "officer_epf",
        "member_count",
        "is_active",
    ]
    list_filter = ["zonal_office__district", "zonal_office", "is_active"]
    search_fields = ["name", "code"]
    list_select_related = [
        "zonal_office__district",
        "maintenance_officer__ledger_profile",
    ]

    @admin.display(description="EPF number")
    def officer_epf(self, obj):
        profile = getattr(obj.maintenance_officer, "ledger_profile", None)
        return profile.epf_number if profile else "EPF required"

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


class PaymentTransactionAdminForm(forms.ModelForm):
    class Meta:
        model = PaymentTransaction
        fields = "__all__"


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [*self.readonly_fields, "reverses"] if obj else self.readonly_fields

    form = PaymentTransactionAdminForm
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

    def has_change_permission(self, request, obj=None):
        if obj and hasattr(obj, "receipt_allocation"):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class PaymentPlanAmountInline(admin.TabularInline):
    model = PaymentPlanAmount
    extra = 0
    readonly_fields = ["is_fixed"]

    def has_delete_permission(self, request, obj=None):
        if obj and obj.amounts.filter(is_fixed=True).exists():
            return False
        return super().has_delete_permission(request, obj)


class PaymentPlanTemplateAmountInline(admin.TabularInline):
    model = PaymentPlanTemplateAmount
    extra = 0

    def has_delete_permission(self, request, obj=None):
        if obj and obj.amounts.filter(is_fixed=True).exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(PaymentPlanTemplate)
class PaymentPlanTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "monthly_total"]
    inlines = [PaymentPlanTemplateAmountInline]
    search_fields = ["name"]


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    readonly_fields = ["source_template", "source_template_name"]
    list_display = ["member", "agreement_date", "monthly_total"]
    list_select_related = ["member"]
    search_fields = ["member__member_no", "member__full_name"]
    autocomplete_fields = ["member"]
    inlines = [PaymentPlanAmountInline]


class OpeningBalanceAmountInline(admin.TabularInline):
    model = OpeningBalanceAmount
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        from .fix_deposit_services import locked_payment_types

        if obj and (
            locked_payment_types(obj.member_id)
            or DepositInterestYear.objects.filter(
                member_id=obj.member_id, posted_at__isnull=False
            ).exists()
        ):
            kwargs["can_delete"] = False
        return super().get_formset(request, obj, **kwargs)


@admin.register(OpeningBalance)
class OpeningBalanceAdmin(admin.ModelAdmin):
    list_display = ["member", "balance_date", "total"]
    list_select_related = ["member"]
    search_fields = ["member__member_no", "member__full_name"]
    autocomplete_fields = ["member"]
    inlines = [OpeningBalanceAmountInline]

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        from .fix_deposit_services import locked_payment_types

        if obj and (
            locked_payment_types(obj.member_id)
            or DepositInterestYear.objects.filter(
                member_id=obj.member_id, posted_at__isnull=False
            ).exists()
        ):
            return False
        return super().has_delete_permission(request, obj)


class PermanentLedgerAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import Gender, MemberAuditEvent


@admin.register(Gender)
class GenderAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "sort_order", "is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []


@admin.register(MemberAuditEvent)
class MemberAuditEventAdmin(PermanentLedgerAdmin):
    list_display = ["member_no", "member_name", "action", "actor_name", "occurred_at"]
    search_fields = ["member_no", "member_name", "actor_name"]
    list_filter = ["action", "district", "occurred_at"]


@admin.register(MemberLedger)
class MemberLedgerAdmin(PermanentLedgerAdmin):
    list_display = [
        "reference",
        "member",
        "zonal_office",
        "status",
        "officer_name",
        "officer_epf",
        "opened_on",
        "closed_on",
    ]
    list_filter = ["status", "district", "zonal_office", "legacy_assignment_pending"]
    search_fields = [
        "reference",
        "member__member_no",
        "member__full_name",
        "officer_epf",
    ]
    list_select_related = ["member", "zonal_office"]


@admin.register(MemberTransfer)
class MemberTransferAdmin(PermanentLedgerAdmin):
    list_display = [
        "reference",
        "member",
        "from_ledger",
        "to_ledger",
        "effective_date",
        "performed_by_name",
    ]
    list_select_related = ["member", "from_ledger", "to_ledger"]


admin.site.register(LedgerOfficerAssignment, PermanentLedgerAdmin)
admin.site.register(LedgerRecord, PermanentLedgerAdmin)
admin.site.register(LedgerEvent, PermanentLedgerAdmin)

from .approval_models import (
    ApprovalHierarchy,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalAuditEvent,
)


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(PermanentLedgerAdmin):
    list_display = [
        "reference",
        "description",
        "requester_name",
        "status",
        "created_at",
    ]
    list_filter = ["status", "module"]
    search_fields = ["reference", "requester_name", "description"]


admin.site.register(ApprovalHierarchy, PermanentLedgerAdmin)
admin.site.register(ApprovalLevel, PermanentLedgerAdmin)
admin.site.register(ApprovalAuditEvent, PermanentLedgerAdmin)

from .contribution_models import PaymentReceipt, PaymentReceiptAllocation

admin.site.register(PaymentReceipt, PermanentLedgerAdmin)
admin.site.register(PaymentReceiptAllocation, PermanentLedgerAdmin)
from .audit_models import AuditLog

admin.site.register(AuditLog, PermanentLedgerAdmin)
from .year_end_models import FinancialYearClosing

admin.site.register(FinancialYearClosing, PermanentLedgerAdmin)

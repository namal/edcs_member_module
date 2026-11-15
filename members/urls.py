from django.urls import path
from . import (
    approval_views,
    child_views,
    fix_deposit_views,
    interest_views,
    lager_views,
    opening_balance_views,
    payment_views,
    payment_template_views,
    retirement_views,
    transfer_views,
    transaction_views,
    views,
)

urlpatterns = [
    path("approvals/", approval_views.request_list, name="approval_request_list"),
    path(
        "approvals/<int:pk>/",
        approval_views.request_detail,
        name="approval_request_detail",
    ),
    path(
        "approval-hierarchies/",
        approval_views.hierarchy_list,
        name="approval_hierarchy_list",
    ),
    path(
        "approval-hierarchies/new/",
        approval_views.hierarchy_form,
        name="approval_hierarchy_create",
    ),
    path(
        "approval-hierarchies/<int:pk>/edit/",
        approval_views.hierarchy_form,
        name="approval_hierarchy_edit",
    ),
    path(
        "approval-hierarchies/<int:pk>/history/",
        approval_views.hierarchy_history,
        name="approval_hierarchy_history",
    ),
    path(
        "member-transfers/",
        transfer_views.member_transfer_list,
        name="member_transfer_list",
    ),
    path(
        "member-transfers/new/",
        transfer_views.member_transfer_create,
        name="member_transfer_create",
    ),
    path(
        "member-transfers/<int:pk>/",
        transfer_views.member_transfer_detail,
        name="member_transfer_detail",
    ),
    path(
        "member-transfers/<int:pk>/edit/",
        transfer_views.permanent_history_action,
        name="member_transfer_edit",
    ),
    path(
        "member-transfers/<int:pk>/delete/",
        transfer_views.permanent_history_action,
        name="member_transfer_delete",
    ),
    path(
        "member-ledgers/", transfer_views.member_ledger_list, name="member_ledger_list"
    ),
    path(
        "member-ledgers/new/",
        transfer_views.member_ledger_create,
        name="member_ledger_create",
    ),
    path(
        "member-ledgers/<int:pk>/",
        transfer_views.member_ledger_detail,
        name="member_ledger_detail",
    ),
    path(
        "member-ledgers/<int:pk>/export/",
        transfer_views.member_ledger_detail,
        {"export": True},
        name="member_ledger_export",
    ),
    path(
        "member-ledgers/<int:pk>/officer/",
        transfer_views.member_ledger_officer,
        name="member_ledger_edit",
    ),
    path(
        "member-ledgers/<int:pk>/delete/",
        transfer_views.permanent_history_action,
        name="member_ledger_delete",
    ),
    path(
        "payment-plan-templates/",
        payment_template_views.payment_plan_template_list,
        name="payment_plan_template_list",
    ),
    path(
        "payment-plan-templates/new/",
        payment_template_views.payment_plan_template_form,
        name="payment_plan_template_create",
    ),
    path(
        "payment-plan-templates/<int:pk>/edit/",
        payment_template_views.payment_plan_template_form,
        name="payment_plan_template_edit",
    ),
    path(
        "payment-plan-templates/<int:pk>/delete/",
        payment_template_views.payment_plan_template_delete,
        name="payment_plan_template_delete",
    ),
    path(
        "payment-plan-templates/options/",
        payment_template_views.payment_plan_template_options,
        name="payment_plan_template_options",
    ),
    path(
        "members/<int:pk>/retire/", retirement_views.member_retire, name="member_retire"
    ),
    path(
        "lagers/<int:pk>/activity/", lager_views.lager_activity, name="lager_activity"
    ),
    path(
        "lagers/<int:pk>/activity/export/",
        lager_views.lager_activity,
        {"export": True},
        name="lager_activity_export",
    ),
    path(
        "deposit-interest/children/<int:pk>/",
        interest_views.deposit_interest_detail,
        {"child_account": True},
        name="child_interest_detail",
    ),
    path(
        "deposit-interest/children/<int:pk>/post/",
        interest_views.deposit_interest_edit,
        {"child_account": True},
        name="child_interest_edit",
    ),
    path(
        "deposit-interest/children/<int:pk>/delete/",
        interest_views.deposit_interest_delete,
        {"child_account": True},
        name="child_interest_delete",
    ),
    path(
        "deposit-interest/",
        interest_views.deposit_interest_list,
        name="deposit_interest_list",
    ),
    path(
        "deposit-interest/calculate/",
        interest_views.deposit_interest_create,
        name="deposit_interest_create",
    ),
    path(
        "deposit-interest/<int:pk>/",
        interest_views.deposit_interest_detail,
        name="deposit_interest_detail",
    ),
    path(
        "deposit-interest/<int:pk>/post/",
        interest_views.deposit_interest_edit,
        name="deposit_interest_edit",
    ),
    path(
        "deposit-interest/<int:pk>/delete/",
        interest_views.deposit_interest_delete,
        name="deposit_interest_delete",
    ),
    path("fix-deposits/", fix_deposit_views.fix_deposit_list, name="fix_deposit_list"),
    path(
        "fix-deposits/new/",
        fix_deposit_views.fix_deposit_form,
        name="fix_deposit_create",
    ),
    path(
        "fix-deposits/<int:pk>/",
        fix_deposit_views.fix_deposit_detail,
        name="fix_deposit_detail",
    ),
    path(
        "fix-deposits/<int:pk>/edit/",
        fix_deposit_views.fix_deposit_form,
        name="fix_deposit_edit",
    ),
    path(
        "fix-deposits/<int:pk>/delete/",
        fix_deposit_views.fix_deposit_delete,
        name="fix_deposit_delete",
    ),
    path(
        "fix-deposits/<int:pk>/loans/new/",
        fix_deposit_views.fix_loan_create,
        name="fix_loan_create",
    ),
    path(
        "fix-deposit-loans/<int:pk>/",
        fix_deposit_views.fix_loan_detail,
        name="fix_loan_detail",
    ),
    path(
        "fix-deposit-loans/<int:pk>/entries/new/",
        fix_deposit_views.fix_loan_entry,
        name="fix_loan_entry",
    ),
    path(
        "fix-deposit-loans/<int:pk>/settle/",
        fix_deposit_views.fix_loan_settle,
        name="fix_loan_settle",
    ),
    path(
        "fix-deposits/<int:pk>/withdrawals/new/",
        fix_deposit_views.fix_withdrawal_create,
        name="fix_withdrawal_create",
    ),
    path(
        "fix-deposit-withdrawals/<int:pk>/review/",
        fix_deposit_views.fix_withdrawal_review,
        name="fix_withdrawal_review",
    ),
    path(
        "fix-deposit-rules/",
        fix_deposit_views.fix_deposit_policy_list,
        name="fix_deposit_policy_list",
    ),
    path(
        "fix-deposit-rules/new/",
        fix_deposit_views.fix_deposit_policy_form,
        name="fix_deposit_policy_create",
    ),
    path(
        "fix-deposit-rules/<int:pk>/edit/",
        fix_deposit_views.fix_deposit_policy_form,
        name="fix_deposit_policy_edit",
    ),
    path(
        "fix-deposit-rules/<int:pk>/delete/",
        fix_deposit_views.fix_deposit_policy_delete,
        name="fix_deposit_policy_delete",
    ),
    path(
        "api/fix-deposits/<int:pk>/status/",
        fix_deposit_views.fix_deposit_status,
        name="fix_deposit_status",
    ),
    path("children/", child_views.child_list, name="child_list"),
    path("children/new/", child_views.child_form, name="child_create"),
    path("children/<int:pk>/", child_views.child_detail, name="child_detail"),
    path("children/<int:pk>/edit/", child_views.child_form, name="child_edit"),
    path("children/<int:pk>/delete/", child_views.child_delete, name="child_delete"),
    path(
        "child-deposit-types/",
        child_views.child_deposit_type_list,
        name="child_deposit_type_list",
    ),
    path(
        "child-deposit-types/new/",
        child_views.child_deposit_type_form,
        name="child_deposit_type_create",
    ),
    path(
        "child-deposit-types/<int:pk>/edit/",
        child_views.child_deposit_type_form,
        name="child_deposit_type_edit",
    ),
    path(
        "child-deposit-types/<int:pk>/delete/",
        child_views.child_deposit_type_delete,
        name="child_deposit_type_delete",
    ),
    path("child-deposits/", child_views.child_deposit_list, name="child_deposit_list"),
    path(
        "child-deposits/new/",
        child_views.child_deposit_form,
        name="child_deposit_create",
    ),
    path(
        "child-deposits/<int:pk>/",
        child_views.child_deposit_detail,
        name="child_deposit_detail",
    ),
    path(
        "child-deposits/<int:pk>/edit/",
        child_views.child_deposit_form,
        name="child_deposit_edit",
    ),
    path(
        "child-deposits/<int:pk>/delete/",
        child_views.child_deposit_delete,
        name="child_deposit_delete",
    ),
    path(
        "child-deposits/<int:deposit_pk>/entries/new/",
        child_views.child_entry_form,
        name="child_entry_create",
    ),
    path(
        "child-deposit-entries/<int:pk>/edit/",
        child_views.child_entry_form,
        name="child_entry_edit",
    ),
    path(
        "child-deposit-entries/<int:pk>/delete/",
        child_views.child_entry_delete,
        name="child_entry_delete",
    ),
    path(
        "api/child-deposits/<int:pk>/balance/",
        child_views.child_deposit_status,
        name="child_deposit_status",
    ),
    path(
        "debit-credit/balances/",
        transaction_views.payment_transaction_report,
        name="payment_transaction_report",
    ),
    path(
        "api/debit-credit/balances/",
        transaction_views.payment_transaction_balances,
        name="payment_transaction_balances",
    ),
    path(
        "debit-credit/",
        transaction_views.payment_transaction_list,
        name="payment_transaction_list",
    ),
    path(
        "debit-credit/new/",
        transaction_views.payment_transaction_form,
        name="payment_transaction_create",
    ),
    path(
        "debit-credit/<int:pk>/edit/",
        transaction_views.payment_transaction_form,
        name="payment_transaction_edit",
    ),
    path(
        "debit-credit/<int:pk>/delete/",
        transaction_views.payment_transaction_delete,
        name="payment_transaction_delete",
    ),
    path(
        "opening-balances/",
        opening_balance_views.opening_balance_list,
        name="opening_balance_list",
    ),
    path(
        "opening-balances/new/",
        opening_balance_views.opening_balance_form,
        name="opening_balance_create",
    ),
    path(
        "opening-balances/<int:pk>/",
        opening_balance_views.opening_balance_detail,
        name="opening_balance_detail",
    ),
    path(
        "opening-balances/<int:pk>/edit/",
        opening_balance_views.opening_balance_form,
        name="opening_balance_edit",
    ),
    path(
        "opening-balances/<int:pk>/delete/",
        opening_balance_views.opening_balance_delete,
        name="opening_balance_delete",
    ),
    path("payment-plans/", payment_views.payment_plan_list, name="payment_plan_list"),
    path(
        "payment-plans/new/",
        payment_views.payment_plan_form,
        name="payment_plan_create",
    ),
    path(
        "payment-plans/<int:pk>/",
        payment_views.payment_plan_detail,
        name="payment_plan_detail",
    ),
    path(
        "payment-plans/<int:pk>/edit/",
        payment_views.payment_plan_form,
        name="payment_plan_edit",
    ),
    path(
        "payment-plans/<int:pk>/delete/",
        payment_views.payment_plan_delete,
        name="payment_plan_delete",
    ),
    path("payment-types/", payment_views.payment_type_list, name="payment_type_list"),
    path(
        "payment-types/new/",
        payment_views.payment_type_form,
        name="payment_type_create",
    ),
    path(
        "payment-types/<int:pk>/edit/",
        payment_views.payment_type_form,
        name="payment_type_edit",
    ),
    path(
        "payment-types/<int:pk>/delete/",
        payment_views.payment_type_delete,
        name="payment_type_delete",
    ),
    path("", views.dashboard, name="dashboard"),
    path("members/", views.member_list, name="member_list"),
    path("members/new/", views.member_form, name="member_create"),
    path("members/export/", views.member_export, name="member_export"),
    path("members/<int:pk>/", views.member_detail, name="member_detail"),
    path("members/<int:pk>/edit/", views.member_form, name="member_edit"),
    path("members/<int:pk>/delete/", views.member_delete, name="member_delete"),
    path("districts/", views.district_list, name="district_list"),
    path("districts/new/", views.district_form, name="district_create"),
    path("districts/<int:pk>/edit/", views.district_form, name="district_edit"),
    path("districts/<int:pk>/delete/", views.district_delete, name="district_delete"),
    path("offices/", views.office_list, name="office_list"),
    path("offices/new/", views.office_form, name="office_create"),
    path("offices/<int:pk>/edit/", views.office_form, name="office_edit"),
    path("offices/<int:pk>/delete/", views.office_delete, name="office_delete"),
    path("api/offices/", views.office_options, name="office_options"),
    path("api/locations/", views.location_options, name="location_options"),
]

for kind, prefix in [
    ("subdivisions", "subdivision"),
    ("schools", "school"),
    ("lagers", "lager"),
]:
    urlpatterns += [
        path(
            f"{kind}/", views.extra_master_list, {"kind": kind}, name=f"{prefix}_list"
        ),
        path(
            f"{kind}/new/",
            views.extra_master_form,
            {"kind": kind},
            name=f"{prefix}_create",
        ),
        path(
            f"{kind}/<int:pk>/edit/",
            views.extra_master_form,
            {"kind": kind},
            name=f"{prefix}_edit",
        ),
        path(
            f"{kind}/<int:pk>/delete/",
            views.extra_master_delete,
            {"kind": kind},
            name=f"{prefix}_delete",
        ),
    ]

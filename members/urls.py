from django.urls import path
from . import opening_balance_views, payment_views, views

urlpatterns = [
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

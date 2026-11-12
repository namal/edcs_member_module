from django.urls import path

from . import access_views as views

urlpatterns = [
    path("", views.administration, name="administration"),
    path("users/", views.user_list, name="access_users"),
    path("users/new/", views.user_form, name="access_user_create"),
    path("users/<int:pk>/", views.user_form, name="access_user_edit"),
    path("users/<int:pk>/delete/", views.user_delete, name="access_user_delete"),
    path("roles/", views.role_list, name="access_roles"),
    path("roles/new/", views.role_form, name="access_role_create"),
    path("roles/<int:pk>/", views.role_form, name="access_role_edit"),
    path("roles/<int:pk>/delete/", views.role_delete, name="access_role_delete"),
]

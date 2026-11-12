from django.contrib.admin.apps import AdminConfig


class WorkspaceAdminConfig(AdminConfig):
    default_site = "members.admin_site.WorkspaceAdminSite"

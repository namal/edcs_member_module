from django.conf import settings
from django.db import migrations, models


def copy_assignments(apps, schema_editor):
    alias = schema_editor.connection.alias
    Profile = apps.get_model("members", "LedgerOfficerProfile")
    Office = apps.get_model("members", "OfficeLocation")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    profiles = {
        profile.user_id: profile for profile in Profile.objects.using(alias).all()
    }
    known = {}
    for profile in profiles.values():
        if profile.zonal_office_id:
            known.setdefault(profile.user_id, set()).add(profile.zonal_office_id)
    for user_id, office_id in (
        apps.get_model("members", "Lager")
        .objects.using(alias)
        .exclude(maintenance_officer_id=None)
        .values_list("maintenance_officer_id", "zonal_office_id")
    ):
        known.setdefault(user_id, set()).add(office_id)
    for user_id, office_id in (
        apps.get_model("members", "MemberLedger")
        .objects.using(alias)
        .filter(status="active")
        .exclude(maintenance_officer_id=None)
        .values_list("maintenance_officer_id", "zonal_office_id")
    ):
        known.setdefault(user_id, set()).add(office_id)
    through = Profile.zonal_offices.through
    for user in User.objects.using(alias).all():
        offices = known.get(user.pk)
        if not offices:
            offices = set(
                Office.objects.using(alias)
                .filter(district__authorized_users=user)
                .values_list("pk", flat=True)
            )
        if offices:
            profile = profiles.get(user.pk)
            if profile is None:
                profile = Profile.objects.using(alias).create(user_id=user.pk)
            through.objects.using(alias).bulk_create(
                [
                    through(
                        ledgerofficerprofile_id=profile.pk, officelocation_id=office_id
                    )
                    for office_id in offices
                ],
                ignore_conflicts=True,
            )
    # Validate pending profile foreign keys before removing the legacy column.
    schema_editor.connection.check_constraints()


def restore_single_office(apps, schema_editor):
    Profile = apps.get_model("members", "LedgerOfficerProfile")
    alias = schema_editor.connection.alias
    for profile in Profile.objects.using(alias).all():
        offices = list(
            profile.zonal_offices.using(alias).values_list("pk", flat=True)[:2]
        )
        Profile.objects.using(alias).filter(pk=profile.pk).update(
            zonal_office_id=offices[0] if len(offices) == 1 else None
        )


class Migration(migrations.Migration):
    dependencies = [("members", "0027_template_only_fixed_amounts")]
    operations = [
        migrations.AddField(
            model_name="ledgerofficerprofile",
            name="zonal_offices",
            field=models.ManyToManyField(
                blank=True, related_name="ledger_officers", to="members.officelocation"
            ),
        ),
        migrations.RunPython(copy_assignments, restore_single_office),
        migrations.RemoveField(model_name="ledgerofficerprofile", name="zonal_office"),
    ]

"""Move district lagers to zones without changing members or financial records."""

import json
import os

import django.db.models.deletion
from django.db import migrations, models


def assign_zones(apps, schema_editor):
    Lager = apps.get_model("members", "Lager")
    Office = apps.get_model("members", "OfficeLocation")
    Member = apps.get_model("members", "Member")
    alias = schema_editor.connection.alias
    lagers = Lager.objects.using(alias)
    offices = dict(Office.objects.using(alias).values_list("pk", "district_id"))
    overrides = json.loads(os.environ.get("LAGER_ZONAL_MIGRATION_MAP", "{}"))
    plan = []
    for lager in lagers.order_by("pk"):
        members = Member.objects.using(alias).filter(lager_id=lager.pk)
        zones = sorted(set(members.values_list("office_location_id", flat=True)))
        if members.exclude(district_id=lager.district_id).exists() or any(
            offices.get(zone) != lager.district_id for zone in zones
        ):
            raise RuntimeError(
                f"Lager {lager.pk} has inconsistent member locations. Correct them before migrating."
            )
        if not zones:
            override = overrides.get(str(lager.pk))
            zones = (
                [int(override)]
                if override is not None
                else [
                    pk
                    for pk, district in offices.items()
                    if district == lager.district_id
                ]
            )
            if len(zones) != 1 or offices.get(zones[0]) != lager.district_id:
                raise RuntimeError(
                    f"Lager {lager.pk} requires a zonal office mapping. Set "
                    'LAGER_ZONAL_MIGRATION_MAP to a JSON object {"lager_id": zonal_office_id} '
                    "using a zone in its district, then rerun migrate."
                )
        plan.append((lager, zones))
    # Validate the entire mapping before changing records.
    used_codes = set(lagers.values_list("code", flat=True))
    for lager, zones in plan:
        lagers.filter(pk=lager.pk).update(zonal_office_id=zones[0])
        for zone in zones[1:]:
            suffix = f"-Z{zone}"
            code = f"{lager.code[:20-len(suffix)]}{suffix}"
            index = 1
            while code in used_codes:
                suffix = f"-Z{zone}-{index}"
                code = f"{lager.code[:20-len(suffix)]}{suffix}"
                index += 1
            used_codes.add(code)
            clone = lagers.create(
                district_id=lager.district_id,
                zonal_office_id=zone,
                name=lager.name,
                code=code,
                is_active=lager.is_active,
            )
            Member.objects.using(alias).filter(
                lager_id=lager.pk, office_location_id=zone
            ).update(lager_id=clone.pk)
    # PostgreSQL defers FK checks; finish them before the following schema changes.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def restore_districts(apps, schema_editor):
    Lager = apps.get_model("members", "Lager")
    alias = schema_editor.connection.alias
    seen = set()
    rows = list(Lager.objects.using(alias).select_related("zonal_office"))
    for lager in rows:
        key = (lager.zonal_office.district_id, lager.name)
        if key in seen:
            raise RuntimeError(
                "Cannot restore district lagers: duplicate names within a district must be resolved first."
            )
        seen.add(key)
    for lager in rows:
        Lager.objects.using(alias).filter(pk=lager.pk).update(
            district_id=lager.zonal_office.district_id
        )
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [("members", "0019_exclude_transition_quarters")]

    operations = [
        migrations.AddField(
            model_name="lager",
            name="zonal_office",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lagers",
                to="members.officelocation",
            ),
        ),
        migrations.AlterField(
            model_name="lager",
            name="district",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lagers",
                to="members.district",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="lager", name="unique_lager_in_district"
        ),
        migrations.RunPython(assign_zones, restore_districts),
        migrations.AlterField(
            model_name="lager",
            name="zonal_office",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lagers",
                to="members.officelocation",
            ),
        ),
        migrations.RemoveField(model_name="lager", name="district"),
        migrations.AddConstraint(
            model_name="lager",
            constraint=models.UniqueConstraint(
                fields=("zonal_office", "name"), name="unique_lager_in_zone"
            ),
        ),
        migrations.AlterModelOptions(
            name="lager",
            options={
                "ordering": [
                    "zonal_office__district__name",
                    "zonal_office__name",
                    "name",
                    "pk",
                ]
            },
        ),
    ]

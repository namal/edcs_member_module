"""Copy recorded history with original times and actors, without inventing older changes."""


def backfill_audit_logs(apps, schema_editor):
    from members.audit_tracking import MODULES, safe_value

    alias = schema_editor.connection.alias
    Audit = apps.get_model("members", "AuditLog")
    Member = apps.get_model("members", "Member")
    User = apps.get_model("auth", "User")
    users = {row.pk: row for row in User.objects.using(alias).all()}
    members = {row.pk: row for row in Member.objects.using(alias).all()}
    batch = []

    def add(
        origin,
        source_id,
        source_model,
        action,
        time,
        before,
        after,
        actor_id=None,
        name="",
        epf="",
        member_pk=None,
        record_label="",
        changes=None,
        district_pk=None,
        lager_pk=None,
    ):
        model_name = source_model.split(".")[-1]
        try:
            model_label = str(
                apps.get_model(source_model)._meta.verbose_name_plural
            ).capitalize()
        except LookupError:
            model_label = model_name.replace("_", " ").capitalize()
        module, label = MODULES.get(model_name, (model_name, model_label))
        actor = users.get(actor_id)
        member = members.get(member_pk)
        if member:
            district_pk = district_pk or member.district_id
            lager_pk = lager_pk or member.lager_id
        if changes is None:
            changes = [
                {
                    "field": key,
                    "label": key.replace("_", " "),
                    "before": before.get(key),
                    "after": after.get(key),
                }
                for key in dict.fromkeys([*before, *after])
                if before.get(key) != after.get(key)
            ]
        batch.append(
            Audit(
                origin=origin._meta.label_lower,
                origin_id=origin.pk,
                source_model=source_model,
                source_id=source_id,
                module=module,
                module_label=label,
                action=action,
                occurred_at=time,
                before=safe_value(before),
                after=safe_value(after),
                changes=safe_value(changes),
                actor_id=actor_id,
                actor_username=actor.username if actor else "",
                actor_name=name
                or (
                    (f"{actor.first_name} {actor.last_name}".strip() or actor.username)
                    if actor
                    else ""
                ),
                actor_epf=epf,
                member_pk=member_pk,
                district_pk=district_pk,
                lager_pk=lager_pk,
                zonal_office_pk=member.office_location_id if member else None,
                record_label=(
                    record_label
                    or (
                        f"{member.member_no} · {member.full_name}"
                        if member
                        else f"{label} #{source_id}"
                    )
                )[:600],
            )
        )
        if len(batch) >= 1000:
            Audit.objects.using(alias).bulk_create(batch)
            batch.clear()

    for event in (
        apps.get_model("members", "MemberAuditEvent").objects.using(alias).iterator()
    ):
        add(
            event,
            event.member_pk,
            "members.member",
            event.action,
            event.occurred_at,
            event.before,
            event.after,
            event.actor_id,
            event.actor_name,
            event.actor_epf,
            event.member_pk,
            f"{event.member_no} · {event.member_name}",
            event.changes,
            event.district_id,
            event.lager_id,
        )
    previous = {}
    events = (
        apps.get_model("members", "LedgerEvent")
        .objects.using(alias)
        .select_related("record")
        .order_by("record_id", "recorded_at", "pk")
    )
    for event in events.iterator():
        before = previous.get(event.record_id, {})
        after = event.snapshot.get("data", {}) if event.action != "deleted" else {}
        previous[event.record_id] = after
        add(
            event,
            event.record.source_id,
            event.record.source_model,
            event.action,
            event.recorded_at,
            before,
            after,
            event.actor_id,
            event.actor_name,
            event.actor_epf,
            event.record.member_id,
        )
    for event in (
        apps.get_model("members", "ApprovalAuditEvent").objects.using(alias).iterator()
    ):
        after = {
            field.name: getattr(event, field.attname)
            for field in event._meta.concrete_fields
            if field.name not in ("created_at", "snapshot")
        }
        from django.core.serializers.json import DjangoJSONEncoder
        import json

        after = json.loads(json.dumps(after, cls=DjangoJSONEncoder))
        after = {
            key: {"label": key.replace("_", " "), "value": value}
            for key, value in after.items()
        }
        add(
            event,
            event.pk,
            "members.approvalauditevent",
            event.action,
            event.created_at,
            {},
            after,
            getattr(event, "actor_id", None),
            getattr(event, "actor_name", ""),
            getattr(event, "actor_epf", ""),
            record_label=f"Approval: {event.action}",
        )
    for event in (
        apps.get_model("admin", "LogEntry")
        .objects.using(alias)
        .select_related("content_type")
        .iterator()
    ):
        model = (
            f"{event.content_type.app_label}.{event.content_type.model}"
            if event.content_type
            else "admin.record"
        )
        source_id = int(event.object_id) if str(event.object_id).isdigit() else None
        add(
            event,
            source_id,
            model,
            {1: "created", 2: "updated", 3: "deleted"}.get(
                event.action_flag, "updated"
            ),
            event.action_time,
            {},
            {"message": event.change_message},
            event.user_id,
            record_label=event.object_repr,
        )
    if batch:
        Audit.objects.using(alias).bulk_create(batch)
    apps.get_model("auth", "Permission").objects.using(alias).filter(
        content_type__app_label="members", codename="delete_paymenttransaction"
    ).delete()

"""Member snapshots and append-only audit events, including bulk updates."""

import json

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models.deletion import ProtectedError

from .ledger_tracking import ledger_actor, officer_identity


def snapshot(member):
    data = {}
    for field in member._meta.concrete_fields:
        value = getattr(member, field.attname)
        display = value
        if field.is_relation and value is not None:
            display = str(getattr(member, field.name))
        elif field.choices:
            display = getattr(member, f"get_{field.name}_display")()
        elif field.name == "gender":
            display = member.get_gender_display()
        data[field.name] = {
            "label": str(field.verbose_name),
            "value": value,
            "display": display,
        }
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def record_member_change(member, before, action, *, using=None):
    from .models import MemberAuditEvent

    after = {} if action == "deleted" else snapshot(member)
    changes = [
        {
            "field": name,
            "label": (after.get(name) or before[name])["label"],
            "before": before.get(name, {}).get("display"),
            "after": after.get(name, {}).get("display"),
        }
        for name in dict.fromkeys([*before, *after])
        if name not in ("updated_at", "created_at")
        and before.get(name, {}).get("value") != after.get(name, {}).get("value")
    ]
    if action == "updated" and not changes:
        return
    actor = ledger_actor.get()
    name, epf = officer_identity(actor)
    MemberAuditEvent.objects.using(using).create(
        member_pk=member.pk,
        member_no=member.member_no,
        member_name=member.full_name,
        district_id=member.district_id,
        lager_id=member.lager_id,
        action=action,
        actor=actor,
        actor_name=name,
        actor_epf=epf,
        before=before,
        after=after,
        changes=changes,
    )


class MemberQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from .audit_tracking import snapshot as audit_snapshot, write_event

        with transaction.atomic(using=self.db):
            previous = {
                obj.pk: (snapshot(obj), audit_snapshot(obj))
                for obj in self.select_for_update()
            }
            if "status" in kwargs:
                from .status_services import guard_status_change

                if not isinstance(kwargs["status"], str):
                    raise ValidationError(
                        "Use Membership Status approval for status changes."
                    )

                for obj in self.filter(pk__in=previous):
                    obj.status = kwargs["status"]
                    guard_status_change(obj)
            count = super().update(**kwargs)
            for member in self.model.objects.using(self.db).filter(pk__in=previous):
                record_member_change(
                    member, previous[member.pk][0], "updated", using=self.db
                )
                write_event(
                    member,
                    "updated",
                    previous[member.pk][1],
                    audit_snapshot(member),
                    using=self.db,
                )
            return count

    def bulk_update(self, objs, fields, *args, **kwargs):
        if "status" in fields:
            raise ValidationError("Use Membership Status approval for status changes.")
        return super().bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, *args, **kwargs):
        raise ValidationError(
            "Save each member individually to retain registration and audit history."
        )


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Member audit history cannot be changed.")

    def delete(self):
        raise ProtectedError("Member audit history must be retained.", self)

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Member audit history cannot be changed.")


def audit_member_delete(sender, instance, using, **kwargs):
    record_member_change(instance, snapshot(instance), "deleted", using=using)


def protect_gender_delete(sender, instance, **kwargs):
    from .models import Member

    if Member.objects.filter(gender=instance.code).exists():
        raise ProtectedError(
            "This gender is used by members. Mark it inactive instead.", [instance]
        )

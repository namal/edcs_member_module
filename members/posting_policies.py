"""Keep postings, enforce edit grants, and validate linked opposite-side corrections."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError

from .ledger_tracking import ledger_actor

DELETE_MESSAGE = "Debit / credit entries cannot be deleted. Correct a mistake with an opposite debit or credit entry."


class PostingQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedError(DELETE_MESSAGE, self)

    def update(self, **kwargs):
        raise ValidationError(
            "Save debit / credit entries individually with edit permission and audit history."
        )

    def bulk_update(self, *args, **kwargs):
        raise ValidationError(
            "Save debit / credit entries individually with edit permission and audit history."
        )

    def bulk_create(self, *args, **kwargs):
        raise ValidationError(
            "Save debit / credit entries individually to retain their ledger and audit history."
        )


def guard_posting_edit(entry):
    if not entry.pk:
        return
    actor = ledger_actor.get()
    if actor is not None:
        from .models import ChildDeposit, PaymentTransaction
        from .permissions import require_permission

        require_permission(
            actor,
            (
                PaymentTransaction
                if isinstance(entry, PaymentTransaction)
                else ChildDeposit
            ),
            "change",
        )
    old = type(entry).objects.filter(pk=entry.pk).first()
    if old and old.reverses_id != entry.reverses_id:
        raise ValidationError(
            "Correction links stay fixed after posting. Add a new opposite entry to record a correction."
        )
    if old and (
        old.reverses_id or type(entry).objects.filter(reverses_id=entry.pk).exists()
    ):
        fields = ("debit", "credit", "transaction_date", "reverses_id") + (
            ("member_id", "payment_type_id")
            if hasattr(entry, "member_id")
            else ("deposit_id",)
        )
        if any(getattr(old, name) != getattr(entry, name) for name in fields):
            raise ValidationError(
                "Amounts, dates and accounts on linked corrections stay fixed. Record another opposite entry if needed."
            )


def validate_correction(entry):
    if not entry.reverses_id:
        return
    original = type(entry).objects.filter(pk=entry.reverses_id).first()
    if original is None or original.pk == entry.pk:
        raise ValidationError("Choose a valid original entry to correct.")
    if (
        type(entry)
        .objects.filter(reverses_id=original.pk)
        .exclude(pk=entry.pk)
        .exists()
    ):
        raise ValidationError(
            "This entry already has an opposite correction. It cannot be corrected twice."
        )
    account_fields = (
        ("member_id", "payment_type_id")
        if hasattr(entry, "member_id")
        else ("deposit_id",)
    )
    if (
        any(
            getattr(entry, field) != getattr(original, field)
            for field in account_fields
        )
        or entry.debit != original.credit
        or entry.credit != original.debit
    ):
        raise ValidationError(
            "The correction must use the same account and the exact opposite debit / credit amounts."
        )
    if not entry.transaction_date or entry.transaction_date < original.transaction_date:
        raise ValidationError("The correction date cannot precede the original entry.")
    if not entry.description.strip():
        raise ValidationError({"description": "Enter the reason for this correction."})


def prevent_posting_delete(sender, instance, **kwargs):
    raise ProtectedError(DELETE_MESSAGE, [instance])

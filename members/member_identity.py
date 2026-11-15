"""Shared name and retirement-date calculations for forms and saved members."""

from calendar import monthrange
from django.core.exceptions import ValidationError


def short_name(full_name, title=""):
    parts = (full_name or "").split()
    if not parts:
        return ""
    name = " ".join([*(f"{part[0].upper()}." for part in parts[:-1]), parts[-1]])
    return f"{title} {name}".strip()


def retirement_date(birth_date, age=60):
    if birth_date is None:
        return None
    year = birth_date.year + age
    if year > 9999:
        raise ValidationError({"date_of_birth": "Enter a valid date of birth."})
    return birth_date.replace(
        year=year, day=min(birth_date.day, monthrange(year, birth_date.month)[1])
    )

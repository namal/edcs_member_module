from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db.models import Min
from django.utils import timezone

from members.deposit_interest import (
    current_financial_year,
    financial_year_start,
    interest_report,
    post_interest_year,
    save_interest_year,
)
from members.child_interest import (
    child_interest_report,
    post_child_interest_year,
    save_child_interest_year,
)
from members.models import (
    ChildDeposit,
    ChildDepositInterestYear,
    DepositInterestYear,
    Member,
    PaymentType,
)


class Command(BaseCommand):
    help = "Save quarterly interest calculations and optionally post ended financial years once."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-year",
            type=int,
            help="First financial-year start year to process, including historical years.",
        )
        parser.add_argument(
            "--post-ended",
            action="store_true",
            help="Credit annual interest once for financial years that have ended.",
        )

    def handle(self, *args, **options):
        current = current_financial_year()
        starts = [
            model.objects.aggregate(first=Min("year_start"))["first"]
            for model in (DepositInterestYear, ChildDepositInterestYear)
        ]
        earliest = min((day for day in starts if day), default=None)
        first = options["from_year"] or (earliest.year if earliest else current)
        if not 1900 <= first <= current:
            raise CommandError(
                "Choose a starting year between 1900 and the current financial year."
            )
        members = Member.objects.all()
        types = PaymentType.objects.in_bulk()
        identities = members.in_bulk()
        saved = posted = 0
        failures = []
        for year in range(first, current + 1):
            for row in interest_report(members, financial_year_start(year)):
                try:
                    record = save_interest_year(
                        identities[row["member_id"]],
                        types[row["payment_type_id"]],
                        financial_year_start(year),
                    )
                    saved += 1
                    if (
                        options["post_ended"]
                        and not record.posted_at
                        and record.year_end < timezone.localdate()
                    ):
                        post_interest_year(record.pk)
                        posted += 1
                except ValidationError as error:
                    failures.append(
                        f"{row['member_no']} / {row['payment_type_name']} / {year}: {' '.join(error.messages)}"
                    )
            deposits = ChildDeposit.objects.select_related(
                "child__member", "deposit_type"
            )
            deposit_map = deposits.in_bulk()
            for row in child_interest_report(deposits, financial_year_start(year)):
                try:
                    record = save_child_interest_year(
                        deposit_map[row["deposit_id"]], financial_year_start(year)
                    )
                    saved += 1
                    if (
                        options["post_ended"]
                        and not record.posted_at
                        and record.year_end < timezone.localdate()
                    ):
                        post_child_interest_year(record.pk)
                        posted += 1
                except ValidationError as error:
                    failures.append(
                        f"{row['member_no']} / {row['child_name']} / {row['account_no']} / {year}: {' '.join(error.messages)}"
                    )
        self.stdout.write(
            f"Calculated {saved} account-years; posted {posted} ended account-years."
        )
        if failures:
            raise CommandError("\n".join(failures))

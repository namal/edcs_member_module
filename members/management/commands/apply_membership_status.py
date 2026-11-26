from django.core.management.base import BaseCommand
from members.status_services import apply_due_status_requests


class Command(BaseCommand):
    help = "Apply approved membership status requests whose effective date has arrived."

    def handle(self, *args, **options):
        result = apply_due_status_requests()
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied: {result['applied']}; could not apply: {result['failed']}"
            )
        )

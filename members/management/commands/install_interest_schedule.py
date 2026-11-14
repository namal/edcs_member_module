import shlex
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Show or install this application's hourly interest processor in the current user's crontab."

    def add_arguments(self, parser):
        parser.add_argument(
            "--install",
            action="store_true",
            help="Install the displayed entry, preserving unrelated cron jobs.",
        )

    def handle(self, *args, **options):
        root = settings.BASE_DIR
        marker = "# member-module-quarterly-interest"
        # Cron treats percent signs specially, even within shell quotes.
        if "%" in str(root) or "\n" in str(root):
            raise CommandError(
                "This project path is not supported by the cron installer."
            )
        job = f"cd {shlex.quote(str(root))} && {shlex.quote(str(root / '.venv/bin/python'))} manage.py process_deposit_interest --post-ended"
        entry = f"15 * * * * /usr/bin/flock -n {shlex.quote(str(root / '.interest-scheduler.lock'))} /bin/sh -c {shlex.quote(job)} >> {shlex.quote(str(root / '.interest-scheduler.log'))} 2>&1 {marker}"
        self.stdout.write(entry)
        if not options["install"]:
            return
        try:
            previous = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
            if previous.returncode not in (0, 1) or (
                previous.returncode == 1 and "no crontab" not in previous.stderr.lower()
            ):
                raise CommandError(
                    "Could not read the current user's crontab; no changes were made."
                )
            lines = [
                line for line in previous.stdout.splitlines() if marker not in line
            ]
            result = subprocess.run(
                ["crontab", "-"],
                input="\n".join([*lines, entry, ""]),
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise CommandError(
                    "Could not install the interest schedule: " + result.stderr.strip()
                )
        except FileNotFoundError:
            raise CommandError("The crontab utility is not installed.")
        self.stdout.write(
            self.style.SUCCESS(
                "Hourly interest processing installed for the current user."
            )
        )

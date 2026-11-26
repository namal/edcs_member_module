"""Exercise the real tabbed Add entry screen in Chrome and a disposable database.

Run: .venv/bin/python tests/run_debit_credit_ui.py
No live members, entries, users or settings are changed.
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def main():
    from django.conf import settings

    database = "test_entry_browser_" + uuid4().hex[:12]
    settings.DATABASES["default"]["TEST"] = {"NAME": database}
    settings.DATABASES["default"]["CONN_MAX_AGE"] = 0
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
    settings.DEBUG = True
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    import django

    django.setup()
    from django.contrib.auth.models import Permission
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from members import models as m
    from members.test_child_contribution_allocations import (
        ChildContributionAllocationTests as Fixture,
    )

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    logging.getLogger("django.server").setLevel(logging.ERROR)
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old = runner.setup_databases()
    server = None
    try:
        assert settings.DATABASES["default"]["NAME"] == database
        Fixture.setUpTestData()
        Fixture.operator.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        client = Client()
        client.force_login(Fixture.operator)
        assert client.get("/debit-credit/new/")["X-Frame-Options"] == "DENY"
        server = make_server(
            "127.0.0.1",
            0,
            StaticFilesHandler(get_wsgi_application()),
            handler_class=QuietHandler,
        )

        def serve():
            try:
                server.serve_forever()
            finally:
                connections.close_all()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        config = {
            "base": f"http://127.0.0.1:{server.server_port}",
            "cookie": {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
            },
            "member": Fixture.member.pk,
            "paymentType": Fixture.types["Special Deposit"].pk,
            "childType": Fixture.child_type.pk,
            "childAccount": Fixture.deposits[0].pk,
        }
        subprocess.run(
            ["node", "tests/debit-credit-entry.browser.cjs"],
            input=json.dumps(config),
            text=True,
            cwd=ROOT,
            check=True,
            timeout=150,
        )
        entry = m.PaymentTransaction.objects.get(reference="BROWSER-TABBED-ENTRY")
        assert entry.credit == 1000 and entry.debit == 0
        assert entry.member_id == Fixture.member.pk
        assert entry.payment_type_id == Fixture.types["Special Deposit"].pk
        assert m.PaymentTransaction.objects.count() == 1
        child_entry = m.ChildDepositTransaction.objects.get(
            reference="BROWSER-TABBED-CHILD"
        )
        assert child_entry.credit == 200 and child_entry.debit == 0
        assert child_entry.deposit_id == Fixture.deposits[0].pk
        print(
            "Verified member and child credits on their original posting routes; no duplicate member entry."
        )
        Fixture.operator.user_permissions.remove(
            *Permission.objects.filter(
                codename__in=[
                    "view_childdeposit",
                    "add_childdeposit",
                    "change_childdeposit",
                ]
            )
        )
        page = client.get("/debit-credit/new/")
        assert b'data-entry-tab="entry-panel-child-balance"' not in page.content
        Fixture.operator.user_permissions.remove(
            Permission.objects.get(codename="add_paymenttransaction")
        )
        page = client.get("/debit-credit/")
        assert b"data-entry-shortcut" not in page.content
        assert b"data-add-entry-link" not in page.content
        print(
            "Verified child tabs and Add entry shortcuts respect existing permissions."
        )
    finally:
        if server:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        runner.teardown_databases(old)


if __name__ == "__main__":
    main()

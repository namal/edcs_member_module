import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create an initial admin and save its random password in a local, ignored file.'

    def handle(self, *args, **options):
        user_model = get_user_model()
        if user_model.objects.filter(username='admin').exists():
            self.stdout.write('Admin already exists; password unchanged.')
            return
        password = secrets.token_urlsafe(18)
        user_model.objects.create_superuser(username='admin', password=password, first_name='Workspace', last_name='Admin')
        path = settings.BASE_DIR / '.admin-credentials'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(f'Memberly local administrator\nUsername: admin\nPassword: {password}\n\nChange the password through /admin/ after your first login.\n')
        self.stdout.write(self.style.SUCCESS('Admin created. Read .admin-credentials for the initial login.'))

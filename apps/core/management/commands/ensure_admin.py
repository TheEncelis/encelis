import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Creates or updates the single administrator from ADMIN_LOGIN / ADMIN_EMAIL / ADMIN_PASSWORD."

    def handle(self, *args, **options):
        login = (os.environ.get("ADMIN_LOGIN") or "").strip()
        email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
        password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
        if not (login and email and password):
            raise CommandError("ADMIN_LOGIN, ADMIN_EMAIL and ADMIN_PASSWORD must be set in the environment.")
        User = get_user_model()
        user = User.objects.filter(username=login).first() or User.objects.filter(email=email).first() or User(username=login)
        user.username, user.email = login, email
        user.is_staff = user.is_superuser = user.is_active = True
        user.email_verified = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Administrator '{login}' is ready."))

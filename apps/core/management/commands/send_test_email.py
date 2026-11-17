from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send one test email through the configured backend (Resend when RESEND_API_KEY is set)."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Recipient address")

    def handle(self, *args, to, **options):
        backend = settings.EMAIL_BACKEND.rsplit(".", 1)[-1]
        self.stdout.write(f"Backend: {backend} · From: {settings.DEFAULT_FROM_EMAIL}")
        try:
            sent = send_mail("encelis — test", "Resend orqali yuborilgan test xati.", None, [to], fail_silently=False)
        except Exception as exc:
            raise CommandError(
                f"Delivery failed: {exc}. With the default sender onboarding@resend.dev, Resend only delivers to the "
                "account owner's address; verify a domain in Resend and set DEFAULT_FROM_EMAIL to send to anyone."
            ) from exc
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} message(s) to {to}."))

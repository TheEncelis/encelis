"""Account emails and signed verification tokens."""
import logging

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .tasks import send_email

log = logging.getLogger("encelis.accounts")
VERIFY_SALT = "encelis.email-verify"


def make_verification_token(user):
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=VERIFY_SALT)


def read_verification_token(token):
    """Returns (user_id, email) or None when the token is invalid or expired."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
        return data["uid"], data["email"]
    except (signing.BadSignature, KeyError, TypeError):
        return None


def build_message(to, subject, template, context):
    context = {**context, "site_url": settings.SITE_URL}
    text = render_to_string(f"email/{template}.txt", context)
    html = render_to_string(f"email/{template}.html", context)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    message.attach_alternative(html, "text/html")
    return message


def deliver(to, subject, template, context):
    send_email.delay(to, subject, template, context)


def send_verification_email(user):
    url = settings.SITE_URL + reverse("accounts:verify", args=[make_verification_token(user)])
    deliver(user.email, "encelis — emailingizni tasdiqlang", "verify", {"name": user.display_name, "url": url})


def send_welcome_email(user):
    deliver(user.email, "encelis — xush kelibsiz", "welcome", {"name": user.display_name})

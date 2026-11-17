from django.shortcuts import redirect
from django.urls import reverse

from .models import PlatformSetting

# Pages reachable before email verification / placement is complete.
OPEN_PREFIXES = (
    "/login/", "/logout/", "/register/", "/verify-email/", "/password-reset/", "/settings/",
    "/placement/", "/static/", "/django-admin/", "/studio/",
)  # fmt: skip


class LearnerGateMiddleware:
    """Keeps learners inside the onboarding flow until placement is complete,
    and enforces email verification when the platform requires it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        path = request.path
        if user.is_authenticated and not user.is_staff and path != "/" and not path.startswith(OPEN_PREFIXES):
            if not user.email_verified and PlatformSetting.get("require_email_verification"):
                return redirect(reverse("accounts:verify_notice"))
            if not user.profile.placement_done:
                return redirect(reverse("assessments:start"))
        return self.get_response(request)

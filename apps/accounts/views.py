from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.models import PlatformSetting
from apps.core.ratelimit import ratelimit

from . import services
from .forms import AsyncPasswordResetForm, LoginForm, RegisterForm, SettingsForm
from .forms import StyledPasswordChangeForm as PasswordChangeForm

User = get_user_model()


@ratelimit("register", limit=8, window=3600)
@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("core:landing")
    if not PlatformSetting.get("registration_open"):
        return render(request, "accounts/closed.html")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        services.send_verification_email(user)
        login(request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")
        return redirect("assessments:start")
    return render(request, "accounts/register.html", {"form": form})


@ratelimit("login", limit=10, window=300)
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:landing")
    form = LoginForm(request, request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        target = request.POST.get("next") or request.GET.get("next") or ""
        if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(target)
        return redirect("core:landing")
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("core:landing")


def verify(request, token):
    parsed = services.read_verification_token(token)
    user = User.objects.filter(pk=parsed[0], email=parsed[1]).first() if parsed else None
    if user is None:
        return render(request, "accounts/verify_result.html", {"ok": False})
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        services.send_welcome_email(user)
    return render(request, "accounts/verify_result.html", {"ok": True})


@login_required
def verify_notice(request):
    if request.user.email_verified:
        return redirect("core:landing")
    return render(request, "accounts/verify_notice.html")


@login_required
@require_POST
@ratelimit("resend-verify", limit=3, window=900, per="user")
def resend_verification(request):
    if not request.user.email_verified:
        services.send_verification_email(request.user)
        messages.success(request, "Tasdiqlash xati qayta yuborildi.")
    allowed = {reverse("accounts:settings"), reverse("accounts:verify_notice")}
    return redirect(request.POST.get("back") if request.POST.get("back") in allowed else "accounts:settings")


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    user, profile = request.user, request.user.profile
    form = SettingsForm(
        request.POST if request.POST.get("form") == "profile" else None,
        initial={"first_name": user.first_name, "daily_goal_minutes": profile.daily_goal_minutes, "theme": user.theme},
    )
    password_form = PasswordChangeForm(user, request.POST if request.POST.get("form") == "password" else None)
    if request.method == "POST":
        if request.POST.get("form") == "profile" and form.is_valid():
            user.first_name = form.cleaned_data["first_name"].strip()
            user.theme = form.cleaned_data["theme"]
            user.save(update_fields=["first_name", "theme"])
            profile.daily_goal_minutes = form.cleaned_data["daily_goal_minutes"]
            profile.save(update_fields=["daily_goal_minutes"])
            messages.success(request, "Sozlamalar saqlandi.")
            return redirect("accounts:settings")
        if request.POST.get("form") == "password" and password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, password_form.user)
            messages.success(request, "Parol yangilandi.")
            return redirect("accounts:settings")
    return render(request, "accounts/settings.html", {"form": form, "password_form": password_form})


@method_decorator(ratelimit("pw-reset", limit=5, window=3600), name="dispatch")
class PasswordResetView(auth_views.PasswordResetView):
    form_class = AsyncPasswordResetForm
    template_name = "accounts/password_reset.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

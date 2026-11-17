import re

from django import forms
from django.contrib.auth import authenticate, get_user_model, password_validation
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm

from apps.learning.models import LearnerProfile

User = get_user_model()


class Styled:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.TextInput, forms.EmailInput, forms.PasswordInput)):
                field.widget.attrs.setdefault("class", "input")


class StyledPasswordChangeForm(Styled, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Joriy parol"
        self.fields["new_password1"].label = "Yangi parol"
        self.fields["new_password1"].help_text = "Kamida 8 belgi; juda oddiy yoki faqat raqamlardan iborat bo'lmasin."
        self.fields["new_password2"].label = "Yangi parolni takrorlang"
        self.fields["new_password2"].help_text = ""
        self.error_messages = {**self.error_messages, "password_incorrect": "Joriy parol noto'g'ri.", "password_mismatch": "Parollar mos kelmadi."}


class RegisterForm(Styled, forms.Form):
    first_name = forms.CharField(label="Ismingiz", max_length=60)
    email = forms.EmailField(label="Email", max_length=254)
    password = forms.CharField(label="Parol", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), min_length=8)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Bu email bilan hisob allaqachon mavjud.")
        return email

    def clean(self):
        data = super().clean()
        if data.get("password"):
            candidate = User(email=data.get("email", ""), first_name=data.get("first_name", ""))
            try:
                password_validation.validate_password(data["password"], candidate)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return data

    def save(self):
        email = self.cleaned_data["email"]
        base = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower())[:24] or "user"
        username, n = base, 1
        while User.objects.filter(username=username).exists():
            n += 1
            username = f"{base}{n}"
        return User.objects.create_user(
            username=username, email=email, password=self.cleaned_data["password"], first_name=self.cleaned_data["first_name"].strip()
        )


class LoginForm(Styled, forms.Form):
    identifier = forms.CharField(label="Email yoki login", max_length=254, widget=forms.TextInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(label="Parol", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("identifier") and data.get("password"):
            self.user = authenticate(self.request, username=data["identifier"], password=data["password"])
            if self.user is None:
                raise forms.ValidationError("Email yoki parol noto'g'ri.")
        return data


class AsyncPasswordResetForm(PasswordResetForm):
    """Sends the reset email through the same Resend/Celery pipeline as other account mail."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        from .services import deliver

        from django.conf import settings
        from django.urls import reverse

        # Built from SITE_URL, never from the request's Host header (host-header poisoning).
        url = settings.SITE_URL + reverse("accounts:password_reset_confirm", args=[context["uid"], context["token"]])
        deliver(to_email, "encelis — parolni tiklash", "password_reset", {"name": context["user"].display_name, "url": url})


class SettingsForm(Styled, forms.Form):
    first_name = forms.CharField(label="Ism", max_length=60)
    daily_goal_minutes = forms.TypedChoiceField(label="Kunlik maqsad", coerce=int, choices=LearnerProfile.GOAL_CHOICES)
    theme = forms.ChoiceField(label="Mavzu", choices=User.Theme.choices)

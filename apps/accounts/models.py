from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.functional import cached_property


class User(AbstractUser):
    class Theme(models.TextChoices):
        AUTO = "auto", "Tizimga mos"
        LIGHT = "light", "Yorug'"
        DARK = "dark", "Tungi"

    email = models.EmailField("email", unique=True)
    email_verified = models.BooleanField(default=False)
    theme = models.CharField(max_length=8, choices=Theme.choices, default=Theme.AUTO)

    REQUIRED_FIELDS = ["email"]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.first_name or self.username

    @cached_property
    def profile(self):
        from apps.learning.models import LearnerProfile

        return LearnerProfile.objects.get_or_create(user=self)[0]

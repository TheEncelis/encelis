from django.conf import settings
from django.db import models


class DailyActivity(models.Model):
    """One row per learner per local calendar day; drives goals, streaks and charts."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_activity")
    date = models.DateField()
    active_seconds = models.PositiveIntegerField(default=0)
    answered = models.PositiveIntegerField(default=0)
    correct = models.PositiveIntegerField(default=0)
    xp = models.PositiveIntegerField(default=0)
    goal_met = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="uniq_daily_activity")]
        indexes = [models.Index(fields=["date"])]
        ordering = ["-date"]

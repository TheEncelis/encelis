from django.conf import settings
from django.db import models


class PlacementSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Jarayonda"
        COMPLETED = "completed", "Tugallangan"
        SKIPPED = "skipped", "O'tkazib yuborilgan"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="placements")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    theta = models.FloatField(default=1.5)
    sd = models.FloatField(default=1.6)
    posterior = models.JSONField(default=list)  # probability mass over the ability grid
    current_question = models.ForeignKey("questions.Question", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    served_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]


class PlacementResponse(models.Model):
    session = models.ForeignKey(PlacementSession, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey("questions.Question", on_delete=models.CASCADE, related_name="+")
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="+")
    is_correct = models.BooleanField()
    answer = models.JSONField(default=str, blank=True)
    item_difficulty = models.FloatField()
    theta_after = models.FloatField()
    response_ms = models.PositiveIntegerField(default=0)
    order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["session", "question"], name="uniq_placement_question")]

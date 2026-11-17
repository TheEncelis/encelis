from django.conf import settings
from django.db import models


class ReviewCard(models.Model):
    """FSRS memory state for one learner x concept. Scheduling only — mastery lives elsewhere."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_cards")
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="review_cards")
    fsrs = models.JSONField()  # serialised fsrs.Card
    due = models.DateTimeField(db_index=True)
    stability = models.FloatField(null=True, blank=True)
    difficulty = models.FloatField(null=True, blank=True)
    reps = models.PositiveIntegerField(default=0)
    lapses = models.PositiveIntegerField(default=0)
    consecutive_lapses = models.PositiveSmallIntegerField(default=0)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "topic"], name="uniq_review_card")]
        indexes = [models.Index(fields=["user", "due"])]


class ReviewLog(models.Model):
    card = models.ForeignKey(ReviewCard, on_delete=models.CASCADE, related_name="logs")
    session = models.ForeignKey("learning.StudySession", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rating = models.PositiveSmallIntegerField()  # 1 Again, 2 Hard, 3 Good, 4 Easy
    accuracy = models.FloatField()
    questions = models.PositiveSmallIntegerField()
    due_before = models.DateTimeField()
    due_after = models.DateTimeField()
    reviewed_at = models.DateTimeField()

    class Meta:
        ordering = ["-reviewed_at"]

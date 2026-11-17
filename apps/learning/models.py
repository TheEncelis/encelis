"""Learner state.

Three things are kept deliberately separate:
  * Attempt        — immutable history of everything the learner answered;
  * TopicMastery   — the current, derived knowledge state per concept (topic);
  * reviews.ReviewCard — when a concept should be seen again (FSRS).
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStamped
from apps.questions.models import ErrorTag

CEFR = ["A1", "A2", "B1", "B2", "C1", "C2"]


def cefr_for(ability):
    return CEFR[max(0, min(5, int(ability)))]


class LearnerProfile(TimeStamped):
    GOAL_CHOICES = [(10, "10 daqiqa"), (15, "15 daqiqa"), (20, "20 daqiqa"), (30, "30 daqiqa")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="learner_profile")
    daily_goal_minutes = models.PositiveSmallIntegerField(choices=GOAL_CHOICES, default=15)
    # Continuous ability on the CEFR scale: A1 = 0..1, A2 = 1..2, ... C2 = 5..6.
    ability = models.FloatField(default=0.5)
    ability_sd = models.FloatField(default=1.5)
    placement_done = models.BooleanField(default=False)
    xp = models.PositiveIntegerField(default=0)
    streak_current = models.PositiveIntegerField(default=0)
    streak_longest = models.PositiveIntegerField(default=0)
    last_active_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(ability__gte=0) & Q(ability__lte=6), name="ability_range")]

    @property
    def cefr(self):
        return cefr_for(self.ability)


class Stage(models.IntegerChoices):
    NEW = 0, "Boshlanmagan"
    LEARN = 1, "O'rganish"
    UNDERSTAND = 2, "Tushunish"
    PRACTICE = 3, "Mashq"
    APPLY = 4, "Qo'llash"
    MASTER = 5, "Mustahkamlash"
    RETAIN = 6, "O'zlashtirilgan"


class TopicMastery(TimeStamped):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="masteries")
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="masteries")
    stage = models.PositiveSmallIntegerField(choices=Stage.choices, default=Stage.NEW)
    stage_entered_at = models.DateTimeField(null=True, blank=True)
    # Derived signals, recomputed from Attempt history by learning.engine.
    score = models.FloatField(default=0)  # evidence-weighted accuracy x confidence, 0..1
    confidence = models.FloatField(default=0)  # how much independent evidence backs the score
    recent_accuracy = models.FloatField(default=0)
    fluency = models.FloatField(default=0)  # share of correct answers given at fluent speed
    attempts = models.PositiveIntegerField(default=0)
    distinct_questions = models.PositiveIntegerField(default=0)
    distinct_types = models.PositiveSmallIntegerField(default=0)
    production_correct = models.PositiveIntegerField(default=0)
    delayed_correct = models.PositiveIntegerField(default=0)
    # Flags
    theory_seen_at = models.DateTimeField(null=True, blank=True)
    needs_reteach = models.BooleanField(default=False)
    assumed = models.BooleanField(default=False)  # credited by placement, not yet verified
    is_weak = models.BooleanField(default=False)
    weak_reason = models.CharField(max_length=24, blank=True)
    mastered_at = models.DateTimeField(null=True, blank=True)
    last_practiced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "topic"], name="uniq_mastery_per_topic")]
        indexes = [models.Index(fields=["user", "stage"]), models.Index(fields=["user", "is_weak"])]

    def __str__(self):
        return f"{self.user_id}:{self.topic_id}:{self.get_stage_display()}"

    @property
    def percent(self):
        return round(self.score * 100)

    @property
    def in_progress(self):
        return Stage.LEARN <= self.stage <= Stage.MASTER


class StudySession(TimeStamped):
    class Kind(models.TextChoices):
        DAILY = "daily", "Kunlik mashg'ulot"
        TOPIC = "topic", "Mavzu mashqi"
        REVIEW = "review", "Takrorlash"
        WEAKNESS = "weakness", "Zaif tomonlar"
        TIMED = "timed", "Tezkor sinov"

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        COMPLETED = "completed", "Tugallangan"
        ABANDONED = "abandoned", "To'xtatilgan"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="study_sessions")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    target_seconds = models.PositiveIntegerField(default=0)
    active_seconds = models.PositiveIntegerField(default=0)
    answered = models.PositiveIntegerField(default=0)
    correct = models.PositiveIntegerField(default=0)
    xp_earned = models.PositiveIntegerField(default=0)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]


class SessionItem(models.Model):
    class Kind(models.TextChoices):
        THEORY = "theory", "Nazariya"
        QUESTION = "question", "Savol"

    class Purpose(models.TextChoices):
        NEW = "new", "Yangi mavzu"
        REVIEW = "review", "Takrorlash"
        WEAKNESS = "weakness", "Zaif tomon"
        REMEDIAL = "remedial", "Xato ustida ishlash"
        CHALLENGE = "challenge", "Tezkor sinov"

    class Status(models.TextChoices):
        PENDING = "pending"
        SERVED = "served"
        DONE = "done"
        SKIPPED = "skipped"

    session = models.ForeignKey(StudySession, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField()
    kind = models.CharField(max_length=8, choices=Kind.choices)
    purpose = models.CharField(max_length=10, choices=Purpose.choices)
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="+")
    # The concrete question is chosen only when the item is served, so selection
    # reflects everything the learner has done up to that moment.
    question = models.ForeignKey("questions.Question", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    focus_error = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    served_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["session", "status", "position"])]


class Attempt(models.Model):
    """Immutable record of one answered question."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts")
    question = models.ForeignKey("questions.Question", on_delete=models.CASCADE, related_name="attempts")
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="attempts")
    item = models.OneToOneField(SessionItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="attempt")
    session = models.ForeignKey(StudySession, null=True, blank=True, on_delete=models.SET_NULL, related_name="attempts")
    context = models.CharField(max_length=10, default="practice")  # practice | review | placement | challenge
    stage = models.PositiveSmallIntegerField(default=0)  # learner's stage in the topic when answering
    answer = models.JSONField()
    is_correct = models.BooleanField()
    score = models.FloatField()
    # False when the answer could not be validated (e.g. evaluator offline);
    # such attempts are stored for history but never count as evidence.
    counted = models.BooleanField(default=True)
    evaluated_by = models.CharField(max_length=14, default="deterministic")  # deterministic | stored | ai | none
    error_tag = models.CharField(max_length=16, choices=ErrorTag.choices, blank=True)
    response_ms = models.PositiveIntegerField(default=0)
    expected_ms = models.PositiveIntegerField(default=0)
    timed_out = models.BooleanField(default=False)
    repeat_index = models.PositiveSmallIntegerField(default=0)  # how many times this learner saw the question before
    hours_since_seen = models.FloatField(null=True, blank=True)
    delayed = models.BooleanField(default=False)  # first evidence on this topic after a real break
    q_type = models.CharField(max_length=20)
    q_difficulty = models.PositiveSmallIntegerField(default=3)
    feedback = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "topic", "-created_at"]),
            models.Index(fields=["user", "question"]),
            models.Index(fields=["user", "-created_at"]),
        ]


class MistakePattern(models.Model):
    """Running tally of a learner's error categories per topic."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mistake_patterns")
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="+")
    error_tag = models.CharField(max_length=16, choices=ErrorTag.choices)
    count = models.PositiveIntegerField(default=0)
    last_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "topic", "error_tag"], name="uniq_mistake_pattern")]

from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStamped


class QType(models.TextChoices):
    MCQ = "mcq", "Variant tanlash"
    GAP = "gap", "Bo'sh joyni to'ldirish"
    TRANSFORM = "transform", "Gapni o'zgartirish"
    ERROR = "error_correction", "Xatoni tuzatish"
    CONSTRUCT = "construction", "Gap tuzish"
    TRANSLATE = "translation", "Tarjima"
    CONTEXT = "contextual", "Kontekstli mashq"
    FREE = "free_production", "Erkin yozish"


@dataclass(frozen=True)
class TypeMeta:
    weight: float  # how much evidence a response of this type carries
    seconds: int  # expected fluent response time at difficulty 3
    production: bool  # learner produces language rather than recognising it
    stages: tuple  # learning stages where this type is appropriate


TYPE_META = {
    QType.MCQ: TypeMeta(0.6, 20, False, ("understand", "practice")),
    QType.GAP: TypeMeta(0.8, 25, False, ("understand", "practice", "apply")),
    QType.CONTEXT: TypeMeta(0.9, 40, False, ("understand", "practice", "apply")),
    QType.CONSTRUCT: TypeMeta(0.9, 40, True, ("practice", "apply")),
    QType.ERROR: TypeMeta(1.0, 40, True, ("practice", "apply")),
    QType.TRANSFORM: TypeMeta(1.0, 45, True, ("practice", "apply")),
    QType.TRANSLATE: TypeMeta(1.1, 50, True, ("apply",)),
    QType.FREE: TypeMeta(1.2, 90, True, ("apply",)),
}
QUICK_TYPES = (QType.MCQ, QType.GAP)


class ErrorTag(models.TextChoices):
    VERB_FORM = "verb_form", "Fe'l shakli"
    TENSE = "tense", "Zamon tanlash"
    AGREEMENT = "agreement", "Ega va kesim moslashuvi"
    AUXILIARY = "auxiliary", "Yordamchi fe'l"
    WORD_ORDER = "word_order", "So'z tartibi"
    ARTICLE = "article", "Artikl"
    PREPOSITION = "preposition", "Predlog"
    SPELLING = "spelling", "Imlo"
    VOCABULARY = "vocabulary", "So'z tanlash"
    MISSING_WORD = "missing_word", "Tushib qolgan so'z"
    EXTRA_WORD = "extra_word", "Ortiqcha so'z"
    STRUCTURE = "structure", "Gap qurilishi"
    OTHER = "other", "Boshqa"


class Question(TimeStamped):
    class Status(models.TextChoices):
        DRAFT = "draft", "Qoralama"
        PENDING = "pending", "Tasdiq kutmoqda"
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"
        INACTIVE = "inactive", "O'chirilgan"

    class Source(models.TextChoices):
        SEED = "seed", "Dastlabki to'plam"
        ADMIN = "admin", "Admin"
        AI = "ai", "AI"

    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="questions")
    objective = models.ForeignKey("curriculum.LearningObjective", null=True, blank=True, on_delete=models.SET_NULL, related_name="questions")
    type = models.CharField(max_length=20, choices=QType.choices)
    difficulty = models.PositiveSmallIntegerField(default=3)
    instruction = models.CharField(max_length=240, blank=True)
    prompt = models.TextField()
    # Type-specific structure, validated by questions.validation.validate_question.
    payload = models.JSONField(default=dict)
    explanation = models.TextField(blank=True)
    error_tag = models.CharField(max_length=16, choices=ErrorTag.choices, default=ErrorTag.STRUCTURE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    source = models.CharField(max_length=6, choices=Source.choices, default=Source.ADMIN)
    # Dynamically generated personal questions are only ever served to this learner.
    personalized_for = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    content_hash = models.CharField(max_length=64, unique=True)
    times_shown = models.PositiveIntegerField(default=0)
    times_correct = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    generation_job = models.ForeignKey("ai.GenerationJob", null=True, blank=True, on_delete=models.SET_NULL, related_name="questions")

    class Meta:
        indexes = [
            models.Index(fields=["topic", "status", "type"]),
            models.Index(fields=["status", "source"]),
        ]
        constraints = [models.CheckConstraint(condition=Q(difficulty__gte=1) & Q(difficulty__lte=5), name="question_difficulty_range")]

    def __str__(self):
        return f"[{self.type}] {self.prompt[:60]}"

    @property
    def meta(self):
        return TYPE_META[QType(self.type)]

    @property
    def expected_seconds(self):
        return round(self.meta.seconds * (0.7 + 0.1 * self.difficulty))

    @property
    def absolute_difficulty(self):
        """Difficulty on the learner ability scale (0..6)."""
        return self.topic.difficulty + (self.difficulty - 3) * 0.15

    @property
    def accuracy(self):
        return self.times_correct / self.times_shown if self.times_shown else None

    @property
    def is_servable(self):
        return self.status == self.Status.APPROVED


class AnswerVerdict(models.Model):
    """Persisted verdict for a non-listed answer to a question (validated AI evaluation
    or admin decision), so identical answers are never re-evaluated."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="verdicts")
    normalized = models.CharField(max_length=600)
    is_correct = models.BooleanField()
    score = models.FloatField(default=0)
    error_tag = models.CharField(max_length=16, choices=ErrorTag.choices, blank=True)
    feedback = models.TextField(blank=True)
    corrected = models.TextField(blank=True)
    source = models.CharField(max_length=6, default="ai")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "normalized"], name="uniq_verdict_per_answer")]


class QuestionReport(TimeStamped):
    class Reason(models.TextChoices):
        INCORRECT = "incorrect", "To'g'ri javob noto'g'ri ko'rsatilgan"
        AMBIGUOUS = "ambiguous", "Savol noaniq / bir nechta javob to'g'ri"
        MY_ANSWER = "my_answer", "Mening javobim ham to'g'ri edi"
        TYPO = "typo", "Imlo yoki texnik xato"
        OTHER = "other", "Boshqa"

    class Status(models.TextChoices):
        OPEN = "open", "Yangi"
        REVIEWING = "reviewing", "Ko'rib chiqilmoqda"
        RESOLVED = "resolved", "Hal qilindi"
        DISMISSED = "dismissed", "Rad etildi"

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="reports")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="question_reports")
    attempt = models.ForeignKey("learning.Attempt", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=12, choices=Reason.choices)
    comment = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["question", "user"], condition=Q(status__in=["open", "reviewing"]), name="one_open_report_per_user")
        ]

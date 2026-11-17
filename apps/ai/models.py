from django.conf import settings
from django.db import models


class AIUsageLog(models.Model):
    task = models.CharField(max_length=32, db_index=True)
    model = models.CharField(max_length=32)
    thinking = models.BooleanField(default=False)
    success = models.BooleanField(default=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class GenerationJob(models.Model):
    class Kind(models.TextChoices):
        QUESTIONS = "questions", "Savollar yaratish"
        PERSONAL = "personal", "Shaxsiy savollar"
        THEORY_DRAFT = "theory_draft", "Nazariya qoralamasi"
        THEORY_REVIEW = "theory_review", "Nazariya tahlili"

    class Status(models.TextChoices):
        QUEUED = "queued", "Navbatda"
        RUNNING = "running", "Bajarilmoqda"
        DONE = "done", "Tayyor"
        FAILED = "failed", "Xatolik"

    kind = models.CharField(max_length=14, choices=Kind.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    topic = models.ForeignKey("curriculum.Topic", on_delete=models.CASCADE, related_name="generation_jobs")
    params = models.JSONField(default=dict)
    result = models.JSONField(default=dict)  # counts, rejected items with reasons, or review text
    error = models.CharField(max_length=400, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_finished(self):
        return self.status in (self.Status.DONE, self.Status.FAILED)

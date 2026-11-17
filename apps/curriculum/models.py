"""Curriculum: CEFR Level -> Unit -> Topic -> Learning Objectives.

A Topic is the *concept* unit the learning engine tracks mastery for.
Dependencies between topics are explicit edges of a prerequisite graph.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStamped

CEFR_CODES = ["A1", "A2", "B1", "B2", "C1", "C2"]


class Level(models.Model):
    code = models.CharField(max_length=2, unique=True)
    title = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.code


class Unit(TimeStamped):
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="units")
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["level__order", "order", "id"]
        indexes = [models.Index(fields=["level", "order"])]

    def __str__(self):
        return f"{self.level.code} · {self.title}"


class Topic(TimeStamped):
    class Status(models.TextChoices):
        DRAFT = "draft", "Qoralama"
        PUBLISHED = "published", "E'lon qilingan"
        INACTIVE = "inactive", "O'chirilgan"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="topics")
    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField("Nomi (o'zbekcha)", max_length=140)
    title_en = models.CharField("Grammatik atama", max_length=140)
    summary = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    # Position inside the CEFR band, 0.0 (easiest) .. 1.0 (hardest). Absolute
    # difficulty on the ability scale is derived from level + offset.
    difficulty_offset = models.FloatField(default=0.5)
    mastery_threshold = models.FloatField(default=0.8)
    related = models.ManyToManyField("self", blank=True)
    prerequisites = models.ManyToManyField(
        "self", through="TopicPrerequisite", symmetrical=False, related_name="dependents", through_fields=("topic", "prerequisite")
    )

    class Meta:
        ordering = ["unit__level__order", "unit__order", "order", "id"]
        indexes = [models.Index(fields=["unit", "order"])]
        constraints = [
            models.CheckConstraint(condition=Q(difficulty_offset__gte=0) & Q(difficulty_offset__lte=1), name="topic_offset_range"),
            models.CheckConstraint(condition=Q(mastery_threshold__gte=0.5) & Q(mastery_threshold__lte=0.98), name="topic_threshold_range"),
        ]

    def __str__(self):
        return self.title

    @property
    def level(self):
        return self.unit.level

    @property
    def difficulty(self):
        """Absolute difficulty on the ability scale: A1 spans 0..1, C2 spans 5..6."""
        return (self.unit.level.order - 1) + self.difficulty_offset

    @property
    def is_live(self):
        return self.status == self.Status.PUBLISHED and self.unit.is_published


class LearningObjective(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="objectives")
    text = models.CharField(max_length=240)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class TopicPrerequisite(models.Model):
    class Strength(models.TextChoices):
        HARD = "hard", "Majburiy"
        SOFT = "soft", "Tavsiya etiladi"

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="prerequisite_links")
    prerequisite = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="dependent_links")
    strength = models.CharField(max_length=4, choices=Strength.choices, default=Strength.HARD)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["topic", "prerequisite"], name="uniq_topic_prerequisite"),
            models.CheckConstraint(condition=~Q(topic=F("prerequisite")), name="no_self_prerequisite"),
        ]

    def clean(self):
        from .services import would_create_cycle

        if self.topic_id and self.prerequisite_id and would_create_cycle(self.topic_id, self.prerequisite_id):
            raise ValidationError("Bu bog'lanish aylanma bog'liqlik hosil qiladi.")


class TheoryBlock(TimeStamped):
    """One structured block of a topic's theory. `data` shape depends on `kind`:

    explanation: {title?, text}
    example:     {items: [{en, uz, note?}]}
    formula:     {title?, pattern, note?}
    table:       {title?, headers: [..], rows: [[..]]}
    comparison:  {left_title, right_title, pairs: [{left, right}], note?}
    note:        {text}
    mistake:     {items: [{wrong, right, why}]}
    """

    class Kind(models.TextChoices):
        EXPLANATION = "explanation", "Tushuntirish"
        EXAMPLE = "example", "Misollar"
        FORMULA = "formula", "Formula"
        TABLE = "table", "Jadval"
        COMPARISON = "comparison", "Taqqoslash"
        NOTE = "note", "Muhim eslatma"
        MISTAKE = "mistake", "Keng tarqalgan xatolar"

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="blocks")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    order = models.PositiveIntegerField(default=0)
    data = models.JSONField(default=dict)

    class Meta:
        ordering = ["order", "id"]
        indexes = [models.Index(fields=["topic", "order"])]

    def __str__(self):
        return f"{self.topic.slug}:{self.kind}#{self.order}"

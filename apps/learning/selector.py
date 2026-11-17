"""Question selection: rotation, progressive difficulty, type diversity and error focus."""
import random
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.core.models import PlatformSetting
from apps.questions.models import QUICK_TYPES, TYPE_META, QType, Question

from .models import Attempt, Stage

STAGE_BUCKET = {
    Stage.NEW: "understand",
    Stage.LEARN: "understand",
    Stage.UNDERSTAND: "understand",
    Stage.PRACTICE: "practice",
    Stage.APPLY: "apply",
    Stage.MASTER: "apply",
    Stage.RETAIN: "apply",
}
STAGE_TARGET = {Stage.NEW: 1.5, Stage.LEARN: 1.5, Stage.UNDERSTAND: 1.5, Stage.PRACTICE: 2.7, Stage.APPLY: 3.5, Stage.MASTER: 4.0, Stage.RETAIN: 3.7}


@dataclass
class Selection:
    question: Question | None
    unseen_left: int  # unseen servable questions remaining for this topic after this pick


def free_production_available():
    return settings.AI_ENABLED and PlatformSetting.get("ai_answer_evaluation")


def servable(user, topic):
    return Question.objects.filter(topic=topic, status=Question.Status.APPROVED).filter(Q(personalized_for__isnull=True) | Q(personalized_for=user))


def target_difficulty(stage, recent_accuracy, attempts):
    target = STAGE_TARGET[Stage(stage)]
    if attempts >= 3:
        if recent_accuracy >= 0.8:
            target += 0.6
        elif recent_accuracy <= 0.4:
            target -= 0.7
    return max(1.0, min(5.0, target))


def pick_question(user, topic, tm, *, purpose="new", focus_error="", exclude_ids=(), quick_only=False, rng=random, now=None):
    now = now or timezone.now()
    candidates = list(servable(user, topic))
    if quick_only:
        candidates = [q for q in candidates if q.type in QUICK_TYPES]
    if not free_production_available():
        candidates = [q for q in candidates if q.type != QType.FREE]
    if not candidates:
        return Selection(None, 0)

    history = {
        row["question_id"]: row
        for row in Attempt.objects.filter(user=user, topic=topic).values("question_id").annotate(n=Count("id"), last=Max("created_at"))
    }
    type_counts = {}
    for row in Attempt.objects.filter(user=user, topic=topic).values("q_type").annotate(n=Count("id")):
        type_counts[row["q_type"]] = row["n"]
    total_typed = sum(type_counts.values()) or 1

    bucket = STAGE_BUCKET[Stage(tm.stage)]
    staged = [q for q in candidates if bucket in TYPE_META[QType(q.type)].stages]
    pool = staged or candidates
    fresh = [q for q in pool if q.id not in exclude_ids]
    pool = fresh or pool
    target = target_difficulty(tm.stage, tm.recent_accuracy, tm.attempts)

    def score(question):
        seen = history.get(question.id)
        value = -0.8 * abs(question.difficulty - target)
        if seen is None:
            value += 3.0
        else:
            hours = (now - seen["last"]).total_seconds() / 3600
            value += min(2.0, hours / 72) - 0.4 * min(seen["n"], 4)
        value += 1.0 - type_counts.get(question.type, 0) / total_typed
        if focus_error and (question.error_tag == focus_error or any(o.get("error") == focus_error for o in question.payload.get("options", []))):
            value += 1.5
        if purpose in ("review", "weakness") and tm.stage >= Stage.APPLY and TYPE_META[QType(question.type)].production:
            value += 0.5
        if question.personalized_for_id:
            value += 0.6
        return value + rng.random() * 0.5

    best = max(pool, key=score)
    unseen_left = sum(1 for q in candidates if q.id not in history and q.id != best.id)
    return Selection(best, unseen_left)

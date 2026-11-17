"""Learning engine: applies answers to learner state.

Everything that changes mastery goes through `record_attempt`, and only
validated evaluations (`evaluation.counted`) are allowed to act as evidence.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.questions.models import Question

from . import mastery
from .models import Attempt, MistakePattern, Stage, TopicMastery

DELAY_HOURS = 20


@dataclass
class Transition:
    before: int
    after: int
    reteach: bool = False
    mastered: bool = False

    @property
    def promoted(self):
        return self.after > self.before

    @property
    def demoted(self):
        return self.after < self.before


def get_mastery(user, topic):
    return TopicMastery.objects.get_or_create(user=user, topic=topic)[0]


def _enter(tm, stage, now):
    tm.stage = stage
    tm.stage_entered_at = now


def begin_topic(user, topic, now=None):
    tm = get_mastery(user, topic)
    if tm.stage == Stage.NEW:
        _enter(tm, Stage.LEARN, now or timezone.now())
        tm.save(update_fields=["stage", "stage_entered_at", "updated_at"])
    return tm


@transaction.atomic
def mark_theory_seen(user, topic, now=None):
    """Learn -> Understand: the learner has gone through the theory."""
    now = now or timezone.now()
    tm = TopicMastery.objects.select_for_update().get(pk=get_mastery(user, topic).pk)
    tm.theory_seen_at = now
    tm.needs_reteach = False
    if tm.stage <= Stage.LEARN:
        _enter(tm, Stage.UNDERSTAND, now)
    tm.save()
    return tm


def _evidence(tm):
    rows = list(
        Attempt.objects.filter(user_id=tm.user_id, topic_id=tm.topic_id, counted=True)
        .order_by("-created_at", "-id")
        .values("score", "q_type", "q_difficulty", "response_ms", "expected_ms", "question_id", "repeat_index", "hours_since_seen", "delayed", "error_tag", "created_at")[: mastery.WINDOW]
    )
    entered = tm.stage_entered_at
    return [
        mastery.Evidence(
            score=r["score"],
            q_type=r["q_type"],
            difficulty=r["q_difficulty"],
            response_ms=r["response_ms"],
            expected_ms=r["expected_ms"],
            question_id=r["question_id"],
            repeat_index=r["repeat_index"],
            hours_since_seen=r["hours_since_seen"],
            delayed=r["delayed"],
            in_stage=entered is None or r["created_at"] > entered,
            error_tag=r["error_tag"],
        )
        for r in reversed(rows)
    ]


def refresh_mastery(tm, now=None):
    """Recompute signals from history and take at most one stage step. Returns a Transition."""
    now = now or timezone.now()
    signals = mastery.compute_signals(_evidence(tm))
    before = tm.stage
    decision = mastery.decide_stage(tm.stage, signals, tm.theory_seen_at is not None, tm.topic.mastery_threshold)

    tm.score, tm.confidence = signals.score, signals.confidence
    tm.recent_accuracy, tm.fluency = signals.recent_accuracy, signals.fluency
    tm.attempts = signals.attempts
    tm.distinct_questions, tm.distinct_types = signals.distinct_questions, signals.distinct_types
    tm.production_correct, tm.delayed_correct = signals.production_correct, signals.delayed_correct

    demoted = decision.stage < before
    if decision.stage != before:
        _enter(tm, decision.stage, now)
    if decision.reteach:
        tm.needs_reteach = True
        tm.theory_seen_at = None
    if demoted:
        tm.assumed = False
        tm.mastered_at = None
    if decision.stage == Stage.RETAIN and before != Stage.RETAIN:
        tm.mastered_at = now
        tm.assumed = False
    if tm.assumed and signals.attempts >= 6:
        tm.assumed = False

    reason = mastery.weakness_reason(decision.stage, signals, demoted)
    if reason:
        tm.is_weak, tm.weak_reason = True, reason
    elif tm.is_weak and signals.stage_recent_total >= 3 and signals.stage_recent_correct >= signals.stage_recent_total - 1 and not demoted:
        tm.is_weak, tm.weak_reason = False, ""
    tm.save()
    return Transition(before, decision.stage, decision.reteach, mastered=decision.stage == Stage.RETAIN and before != Stage.RETAIN)


def demote_after_lapses(tm, now=None):
    """Called by the review flow when a retained concept keeps being forgotten."""
    now = now or timezone.now()
    if tm.stage >= Stage.MASTER:
        _enter(tm, Stage.PRACTICE, now)
        tm.is_weak, tm.weak_reason, tm.mastered_at, tm.assumed = True, "forgotten", None, False
        tm.save()


@transaction.atomic
def record_attempt(user, question, evaluation, *, raw_answer, response_ms, item=None, session=None, context="practice", timed_out=False, now=None):
    """Persist one answer and update every piece of learner state that depends on it.

    Returns (attempt, topic_mastery, transition). `transition` is None when the
    answer was not counted as evidence.
    """
    from apps.analytics.services import log_activity
    from apps.reviews.services import ensure_card

    now = now or timezone.now()
    topic = question.topic
    tm = TopicMastery.objects.select_for_update().get(pk=get_mastery(user, topic).pk)
    tm.topic = topic

    previous = Attempt.objects.filter(user=user, question=question).order_by("-created_at").values_list("created_at", flat=True)
    repeat_index = previous.count()
    hours_since_seen = (now - previous[0]).total_seconds() / 3600 if repeat_index else None

    same_session = Attempt.objects.filter(session=session, topic=topic).order_by("created_at").first() if session else None
    if same_session:
        delayed = same_session.delayed
    else:
        delayed = tm.last_practiced_at is not None and now - tm.last_practiced_at >= timedelta(hours=DELAY_HOURS)

    expected_ms = question.expected_seconds * 1000
    response_ms = max(0, min(int(response_ms or 0), 10 * 60 * 1000))
    attempt = Attempt.objects.create(
        user=user,
        question=question,
        topic=topic,
        item=item,
        session=session,
        context=context,
        stage=tm.stage,
        answer=raw_answer,
        is_correct=evaluation.is_correct,
        score=evaluation.score,
        counted=evaluation.counted,
        evaluated_by=evaluation.evaluated_by,
        error_tag=evaluation.error_tag or "",
        response_ms=response_ms,
        expected_ms=expected_ms,
        timed_out=timed_out,
        repeat_index=min(repeat_index, 32000),
        hours_since_seen=hours_since_seen,
        delayed=delayed,
        q_type=question.type,
        q_difficulty=question.difficulty,
        feedback={"text": evaluation.feedback, "expected": evaluation.expected, "corrected": evaluation.corrected},
    )
    Attempt.objects.filter(pk=attempt.pk).update(created_at=now)
    attempt.created_at = now

    transition = None
    xp = 0
    if evaluation.counted:
        if repeat_index == 0:
            Question.objects.filter(pk=question.pk).update(
                times_shown=F("times_shown") + 1, times_correct=F("times_correct") + (1 if evaluation.is_correct else 0)
            )
            profile = user.profile
            guess = 1 / len(question.payload["options"]) if question.type == "mcq" else 0.0
            profile.ability = mastery.updated_ability(profile.ability, question.absolute_difficulty, evaluation.score, guess)
            profile.save(update_fields=["ability", "updated_at"])
        if not evaluation.is_correct and evaluation.error_tag:
            pattern, _ = MistakePattern.objects.get_or_create(user=user, topic=topic, error_tag=evaluation.error_tag)
            MistakePattern.objects.filter(pk=pattern.pk).update(count=F("count") + 1, last_at=now)
        if tm.stage == Stage.NEW and context != "placement":
            _enter(tm, Stage.LEARN, now)
        tm.last_practiced_at = now
        transition = refresh_mastery(tm, now)
        if tm.stage >= Stage.APPLY:
            ensure_card(user, topic, first_due=now + timedelta(days=1))
        xp = round(10 * question.meta.weight * evaluation.score * mastery.novelty(repeat_index, hours_since_seen))

    seconds = min(response_ms // 1000, 150)
    log_activity(user, seconds=seconds, answered=1, correct=1 if evaluation.is_correct else 0, xp=xp, now=now)
    if session:
        type(session).objects.filter(pk=session.pk).update(
            answered=F("answered") + 1,
            correct=F("correct") + (1 if evaluation.is_correct else 0),
            xp_earned=F("xp_earned") + xp,
            active_seconds=F("active_seconds") + seconds,
        )
    return attempt, tm, transition

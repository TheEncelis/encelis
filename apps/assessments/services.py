"""Adaptive placement.

Bayesian ability estimation on a fixed grid over the CEFR scale (0..6). After
each response the posterior is updated with a logistic item model and the next
item is the unused one closest to the current estimate (maximum information),
drawn from a topic not yet probed. The test stops once the estimate is precise
enough or the item cap is reached.

The result is a starting point only: `learning.engine` keeps adjusting ability
after every later answer.
"""
import math
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.curriculum.services import live_topics
from apps.learning import engine
from apps.learning.evaluation import Evaluation
from apps.learning.models import Stage, TopicMastery
from apps.questions import checking
from apps.questions.models import QType, Question
from apps.reviews.services import ensure_card

from .models import PlacementResponse, PlacementSession

GRID = [round(i * 0.1, 1) for i in range(61)]
PRIOR_MEAN, PRIOR_SD = 1.5, 1.6
DISCRIMINATION = 1.4
MIN_ITEMS, MAX_ITEMS = 8, 16
TARGET_SD = 0.5
PLACEMENT_TYPES = (QType.MCQ, QType.GAP, QType.CONSTRUCT)
CREDIT_MARGIN = 0.6  # topics this far below the estimate are assumed known (then verified)


def prior():
    weights = [math.exp(-0.5 * ((g - PRIOR_MEAN) / PRIOR_SD) ** 2) for g in GRID]
    total = sum(weights)
    return [w / total for w in weights]


def p_correct(theta, difficulty, guess=0.0):
    return guess + (1 - guess) / (1 + math.exp(-DISCRIMINATION * (theta - difficulty)))


def update_posterior(posterior, difficulty, correct, guess=0.0):
    updated = []
    for theta, mass in zip(GRID, posterior):
        p = p_correct(theta, difficulty, guess)
        updated.append(mass * (p if correct else 1 - p))
    total = sum(updated) or 1e-12
    return [u / total for u in updated]


def estimate(posterior):
    mean = sum(t * m for t, m in zip(GRID, posterior))
    variance = sum(((t - mean) ** 2) * m for t, m in zip(GRID, posterior))
    return mean, math.sqrt(variance)


def guess_rate(question):
    return 1 / len(question.payload["options"]) if question.type == QType.MCQ else 0.0


def should_stop(count, sd, has_items=True):
    return not has_items or count >= MAX_ITEMS or (count >= MIN_ITEMS and sd <= TARGET_SD)


def candidate_items():
    return Question.objects.filter(
        status=Question.Status.APPROVED,
        personalized_for__isnull=True,
        type__in=PLACEMENT_TYPES,
        topic__status="published",
        topic__unit__is_published=True,
    ).select_related("topic__unit__level")


def choose_item(theta, used_question_ids, used_topic_ids, rng=random):
    """Most informative unused item: difficulty nearest to theta, preferring unprobed topics."""
    best, best_key = None, None
    for question in candidate_items():
        if question.id in used_question_ids:
            continue
        key = (question.topic_id in used_topic_ids, round(abs(question.absolute_difficulty - theta), 1), rng.random())
        if best_key is None or key < best_key:
            best, best_key = question, key
    return best


def start(user):
    session = PlacementSession.objects.filter(user=user, status=PlacementSession.Status.ACTIVE).first()
    if session is None:
        session = PlacementSession.objects.create(user=user, theta=PRIOR_MEAN, sd=PRIOR_SD, posterior=prior())
    return session


def next_question(session, now=None):
    """Current question for the session, or None when the test should end."""
    if session.current_question_id:
        return session.current_question
    responses = list(session.responses.values_list("question_id", "topic_id"))
    if should_stop(len(responses), session.sd):
        return None
    question = choose_item(session.theta, {q for q, _ in responses}, {t for _, t in responses})
    if question is None:
        return None
    session.current_question = question
    session.served_at = now or timezone.now()
    session.save(update_fields=["current_question", "served_at"])
    return question


@transaction.atomic
def answer(session, raw, now=None):
    now = now or timezone.now()
    session = PlacementSession.objects.select_for_update().get(pk=session.pk)
    question = session.current_question
    if question is None or session.status != PlacementSession.Status.ACTIVE:
        return session
    result = checking.check(question, raw)
    correct = result.verdict == checking.CORRECT
    difficulty = question.absolute_difficulty
    session.posterior = update_posterior(session.posterior, difficulty, correct, guess_rate(question))
    session.theta, session.sd = estimate(session.posterior)
    response_ms = int((now - session.served_at).total_seconds() * 1000) if session.served_at else 0
    PlacementResponse.objects.create(
        session=session,
        question=question,
        topic=question.topic,
        is_correct=correct,
        answer=raw if raw is not None else "",
        item_difficulty=difficulty,
        theta_after=session.theta,
        response_ms=min(response_ms, 600000),
        order=session.responses.count() + 1,
    )
    session.current_question = None
    session.save()
    return session


@transaction.atomic
def finish(session, now=None):
    """Turn the estimate into an initial learner state."""
    now = now or timezone.now()
    session = PlacementSession.objects.select_for_update().get(pk=session.pk)
    if session.status != PlacementSession.Status.ACTIVE:
        return session
    user = session.user
    profile = user.profile
    responses = list(session.responses.select_related("question__topic__unit__level"))
    theta = max(0.0, min(6.0, session.theta))

    # History: placement answers are real evidence about each probed concept.
    for response in responses:
        evaluation = Evaluation(response.is_correct, 1.0 if response.is_correct else 0.0, expected="", error_tag="" if response.is_correct else response.question.error_tag)
        engine.record_attempt(user, response.question, evaluation, raw_answer=response.answer, response_ms=response.response_ms, context="placement", now=now)

    profile.ability, profile.ability_sd = theta, session.sd
    profile.placement_done = True
    profile.save()

    missed = {r.topic_id for r in responses if not r.is_correct}
    credited = []
    for topic in live_topics():
        if topic.id in missed:
            if topic.difficulty <= theta + 0.3:
                # A gap below the learner's level: visible weakness, taught first.
                TopicMastery.objects.filter(user=user, topic=topic).update(is_weak=True, weak_reason="placement")
            continue
        if topic.difficulty <= theta - CREDIT_MARGIN:
            credited.append(topic)

    # Credited topics skip the full cycle but are not trusted blindly: each gets a
    # verification review, hardest (least certain) first, spread over the next days.
    credited.sort(key=lambda t: t.difficulty, reverse=True)
    for index, topic in enumerate(credited):
        tm = engine.get_mastery(user, topic)
        tm.stage, tm.stage_entered_at, tm.assumed = Stage.MASTER, now, True
        tm.theory_seen_at = tm.theory_seen_at or now
        tm.save()
        ensure_card(user, topic, first_due=now + timedelta(days=1 + index // 2))

    session.status = PlacementSession.Status.COMPLETED
    session.completed_at = now
    session.save()
    return session


@transaction.atomic
def skip(user, now=None):
    """Learner declares themselves a beginner: start at the bottom of A1."""
    now = now or timezone.now()
    PlacementSession.objects.filter(user=user, status=PlacementSession.Status.ACTIVE).update(status=PlacementSession.Status.SKIPPED, completed_at=now)
    if not PlacementSession.objects.filter(user=user).exists():
        PlacementSession.objects.create(user=user, status=PlacementSession.Status.SKIPPED, theta=0.3, sd=PRIOR_SD, posterior=[], completed_at=now)
    profile = user.profile
    profile.ability, profile.ability_sd, profile.placement_done = 0.3, 1.2, True
    profile.save()

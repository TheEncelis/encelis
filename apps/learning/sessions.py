"""Study session lifecycle: create from a plan, serve items, take answers, finalise."""
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.analytics.services import log_activity, today_activity
from apps.reviews import services as reviews
from apps.reviews.models import ReviewCard

from . import engine, planner
from .evaluation import Evaluation, evaluate
from .models import Attempt, SessionItem, Stage, StudySession
from .selector import pick_question

STALE_AFTER = timedelta(hours=6)
MAX_REMEDIAL_PER_TOPIC = 2
TIME_GRACE_MS = 3000
P = SessionItem.Purpose


class NothingToStudy(Exception):
    pass


@transaction.atomic
def start_session(user, kind, *, topic=None, with_theory=False, now=None):
    now = now or timezone.now()
    close_stale(user, now)
    K = StudySession.Kind
    profile = user.profile
    if kind == K.DAILY:
        done_today = today_activity(user, now).active_seconds
        remaining = max(5, profile.daily_goal_minutes - done_today // 60)
        specs = planner.plan_daily(user, min(remaining, profile.daily_goal_minutes), now)
        target = remaining * 60
    elif kind == K.TOPIC:
        specs, target = planner.plan_topic(user, topic, with_theory), 0
    elif kind == K.REVIEW:
        specs, target = planner.plan_reviews(user, now), 0
    elif kind == K.WEAKNESS:
        specs, target = planner.plan_weakness(user), 0
    elif kind == K.TIMED:
        specs, target = planner.plan_timed(user), 0
    else:
        raise ValueError(kind)
    if not specs:
        raise NothingToStudy(kind)
    session = StudySession.objects.create(user=user, kind=kind, target_seconds=target)
    SessionItem.objects.bulk_create(
        [SessionItem(session=session, position=index, kind=spec.kind, purpose=spec.purpose, topic_id=spec.topic_id) for index, spec in enumerate(specs)]
    )
    for topic_id in {s.topic_id for s in specs if s.purpose in (P.NEW, P.WEAKNESS)}:
        engine.begin_topic(user, session.items.filter(topic_id=topic_id).first().topic, now)
    return session


def active_session(user, kind=None):
    sessions = StudySession.objects.filter(user=user, status=StudySession.Status.ACTIVE)
    if kind:
        sessions = sessions.filter(kind=kind)
    return sessions.order_by("-created_at").first()


def close_stale(user, now=None):
    now = now or timezone.now()
    for session in StudySession.objects.filter(user=user, status=StudySession.Status.ACTIVE, updated_at__lt=now - STALE_AFTER):
        finalize(session, now, abandoned=True)


def progress(session):
    items = list(session.items.values_list("status", flat=True))
    done = sum(1 for s in items if s in (SessionItem.Status.DONE, SessionItem.Status.SKIPPED))
    return done, len(items)


def current_item(session, now=None):
    """Next unfinished item, with its question chosen. Returns None when the session is finished."""
    now = now or timezone.now()
    while True:
        item = (
            session.items.filter(status__in=[SessionItem.Status.PENDING, SessionItem.Status.SERVED])
            .select_related("topic__unit__level", "question")
            .order_by("position", "id")
            .first()
        )
        if item is None:
            return None
        if item.kind == SessionItem.Kind.QUESTION and item.question_id is None:
            tm = engine.get_mastery(session.user, item.topic)
            used = set(session.items.exclude(question=None).values_list("question_id", flat=True))
            selection = pick_question(
                session.user,
                item.topic,
                tm,
                purpose=item.purpose,
                focus_error=item.focus_error,
                exclude_ids=used,
                quick_only=session.kind == StudySession.Kind.TIMED,
                now=now,
            )
            if selection.question is None or (selection.question.id in used and item.purpose == P.REMEDIAL):
                item.status = SessionItem.Status.SKIPPED
                item.save(update_fields=["status"])
                continue
            item.question = selection.question
            if selection.unseen_left < 2:
                _request_personal_questions(session.user, item.topic, item.focus_error)
        if item.status == SessionItem.Status.PENDING:
            item.status = SessionItem.Status.SERVED
            item.served_at = now
        item.save(update_fields=["question", "status", "served_at"])
        return item


def _request_personal_questions(user, topic, focus_error):
    from apps.ai.tasks import request_personal_questions

    request_personal_questions(user.id, topic.id, focus_error)


@transaction.atomic
def complete_theory(item, now=None):
    now = now or timezone.now()
    if item.status != SessionItem.Status.SERVED or item.kind != SessionItem.Kind.THEORY:
        return
    session = item.session
    engine.mark_theory_seen(session.user, item.topic, now)
    seconds = min(int((now - item.served_at).total_seconds()), 300) if item.served_at else 60
    log_activity(session.user, seconds=seconds, now=now)
    StudySession.objects.filter(pk=session.pk).update(active_seconds=F("active_seconds") + seconds, updated_at=now)
    item.status = SessionItem.Status.DONE
    item.save(update_fields=["status"])


@dataclass
class AnswerOutcome:
    attempt: Attempt
    evaluation: Evaluation
    transition: engine.Transition | None
    mastery: object
    notes: list = field(default_factory=list)


def submit_answer(item, raw, client_ms=None, now=None):
    """Evaluate and record the answer for a served question item."""
    now = now or timezone.now()
    session, user, question = item.session, item.session.user, item.question
    if item.status != SessionItem.Status.SERVED or question is None:
        raise ValueError("Item is not awaiting an answer.")

    server_ms = int((now - item.served_at).total_seconds() * 1000) if item.served_at else 0
    response_ms = min(client_ms, server_ms) if client_ms and client_ms > 0 else server_ms
    timed = session.kind == StudySession.Kind.TIMED
    timed_out = timed and server_ms > question.expected_seconds * 1000 + TIME_GRACE_MS

    if timed_out:
        evaluation = Evaluation(False, 0.0, expected=_expected(question), feedback=question.explanation, error_tag="")
    else:
        # Evaluation may call the AI service; keep it outside the DB transaction.
        evaluation = evaluate(question, raw, user=user)

    with transaction.atomic():
        locked = SessionItem.objects.select_for_update().get(pk=item.pk)
        if locked.status != SessionItem.Status.SERVED:
            raise ValueError("Item already answered.")
        context = "review" if item.purpose == P.REVIEW else "challenge" if timed else "practice"
        attempt, tm, transition = engine.record_attempt(
            user, question, evaluation, raw_answer=raw, response_ms=response_ms, item=item, session=session, context=context, timed_out=timed_out, now=now
        )
        locked.status = SessionItem.Status.DONE
        locked.save(update_fields=["status"])
        item.status = locked.status
        StudySession.objects.filter(pk=session.pk).update(updated_at=now)
        notes = _react(session, item, evaluation, transition, tm) if not timed else []
    return AnswerOutcome(attempt, evaluation, transition, tm, notes)


def _expected(question):
    from apps.questions.checking import expected_text

    return expected_text(question)


def _insert_after(session, position, offset, **fields):
    """Insert an item `offset` places after `position`, shifting later items down."""
    target = position + offset
    session.items.filter(position__gte=target).update(position=F("position") + 1)
    return SessionItem.objects.create(session=session, position=target, **fields)


def _react(session, item, evaluation, transition, tm):
    """Error-driven adaptation of the remaining session."""
    notes = []
    if transition and transition.reteach:
        # The learner is not coping: go back to Learn before more questions.
        _insert_after(session, item.position, 1, kind=SessionItem.Kind.THEORY, purpose=P.REMEDIAL, topic=item.topic)
        notes.append("reteach")
    elif evaluation.counted and not evaluation.is_correct:
        remedial = session.items.filter(topic=item.topic, purpose=P.REMEDIAL, kind=SessionItem.Kind.QUESTION).count()
        if remedial < MAX_REMEDIAL_PER_TOPIC:
            _insert_after(
                session, item.position, 2, kind=SessionItem.Kind.QUESTION, purpose=P.REMEDIAL, topic=item.topic, focus_error=evaluation.error_tag or ""
            )
            notes.append("remedial")
    if transition and transition.promoted:
        notes.append("mastered" if transition.mastered else "promoted")
    return notes


@transaction.atomic
def finalize(session, now=None, abandoned=False):
    """Close the session and hand each reviewed concept's results to FSRS."""
    now = now or timezone.now()
    session = StudySession.objects.select_for_update().get(pk=session.pk)
    if session.status != StudySession.Status.ACTIVE:
        return session
    review_results = {}
    for attempt in session.attempts.filter(context="review", counted=True):
        review_results.setdefault(attempt.topic_id, []).append((attempt.score, attempt.response_ms, attempt.expected_ms))
    for topic_id, results in review_results.items():
        if abandoned and len(results) < 2:
            continue  # too little evidence to reschedule; the card simply stays due
        card = ReviewCard.objects.filter(user=session.user, topic_id=topic_id).first()
        if card is None:
            card = reviews.ensure_card(session.user, session.items.filter(topic_id=topic_id).first().topic, first_due=now)
        reviews.apply_review(card, results, now=now, session=session)
        if card.consecutive_lapses >= 2:
            tm = engine.get_mastery(session.user, card.topic)
            engine.demote_after_lapses(tm, now)
    session.items.filter(status__in=[SessionItem.Status.PENDING, SessionItem.Status.SERVED]).update(status=SessionItem.Status.SKIPPED)
    session.status = StudySession.Status.ABANDONED if abandoned else StudySession.Status.COMPLETED
    session.ended_at = now
    session.save(update_fields=["status", "ended_at", "updated_at"])
    return session


def summary(session):
    """Per-topic outcome of a finished session, for the closing screen."""
    from .models import TopicMastery

    rows = {}
    for attempt in session.attempts.select_related("topic").order_by("created_at"):
        row = rows.setdefault(attempt.topic_id, {"topic": attempt.topic, "answered": 0, "correct": 0})
        row["answered"] += 1
        row["correct"] += 1 if attempt.is_correct else 0
    states = {m.topic_id: m for m in TopicMastery.objects.filter(user=session.user, topic_id__in=rows)}
    cards = {c.topic_id: c for c in ReviewCard.objects.filter(user=session.user, topic_id__in=rows)}
    for topic_id, row in rows.items():
        row["state"] = states.get(topic_id)
        row["card"] = cards.get(topic_id)
    return list(rows.values())


__all__ = ["start_session", "current_item", "submit_answer", "complete_theory", "finalize", "summary", "NothingToStudy", "Stage"]

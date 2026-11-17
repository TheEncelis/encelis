"""FSRS spaced-repetition scheduling for concepts.

A "review" of a grammar concept is a short set of varied questions. The
learner's performance on that set is mapped deterministically to an FSRS
rating; FSRS then decides when the concept comes back. This module never
touches mastery — the learning engine updates that from the same attempts.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from fsrs import Card, Rating, Scheduler

from .models import ReviewCard, ReviewLog

REVIEW_SET_SIZE = 3
# Concepts are reviewed in sessions, not minute-level flashcard steps, so the
# scheduler works on day-scale intervals from the first review on.
_scheduler = Scheduler(desired_retention=0.9, learning_steps=(), relearning_steps=(), maximum_interval=365, enable_fuzzing=not settings.TESTING)


def rating_from_performance(results):
    """results: [(score 0..1, response_ms, expected_ms)] for one concept's review set."""
    if not results:
        raise ValueError("A review needs at least one answer.")
    accuracy = sum(r[0] for r in results) / len(results)
    if accuracy < 0.5:
        return Rating.Again
    if accuracy < 0.85:
        return Rating.Hard
    fast = all(ms <= expected * 0.7 for _, ms, expected in results if expected)
    if accuracy >= 0.999 and fast and len(results) >= REVIEW_SET_SIZE:
        return Rating.Easy
    return Rating.Good


def ensure_card(user, topic, first_due=None):
    """Create the concept's card if missing. `first_due` schedules the first delayed check."""
    now = timezone.now()
    due = first_due or now + timedelta(days=1)
    card, _ = ReviewCard.objects.get_or_create(user=user, topic=topic, defaults={"fsrs": Card(due=due).to_dict(), "due": due})
    return card


def apply_review(card, results, now=None, session=None):
    """Advance the card's FSRS state from one review set. Returns the rating used."""
    now = now or timezone.now()
    rating = rating_from_performance(results)
    updated, _ = _scheduler.review_card(Card.from_dict(card.fsrs), rating, review_datetime=now)
    due_before = card.due
    card.fsrs = updated.to_dict()
    card.due = updated.due
    card.stability, card.difficulty = updated.stability, updated.difficulty
    card.reps += 1
    if rating == Rating.Again:
        card.lapses += 1
        card.consecutive_lapses += 1
    else:
        card.consecutive_lapses = 0
    card.last_reviewed_at = now
    card.save()
    ReviewLog.objects.create(
        card=card,
        session=session,
        rating=int(rating),
        accuracy=sum(r[0] for r in results) / len(results),
        questions=len(results),
        due_before=due_before,
        due_after=card.due,
        reviewed_at=now,
    )
    return rating


def retrievability(card, now=None):
    if card.stability is None:
        return 1.0
    return _scheduler.get_card_retrievability(Card.from_dict(card.fsrs), now or timezone.now())


def due_cards(user, now=None):
    """Due cards for live topics, most important first.

    Priority favours concepts that many later topics depend on and those whose
    memory has decayed the most — so after a long break the learner restores
    foundations first instead of facing an undifferentiated backlog.
    """
    from apps.curriculum.models import Topic, TopicPrerequisite

    now = now or timezone.now()
    cards = list(
        ReviewCard.objects.filter(user=user, due__lte=now, topic__status=Topic.Status.PUBLISHED, topic__unit__is_published=True).select_related(
            "topic__unit__level"
        )
    )
    if not cards:
        return []
    dependents = {}
    for prereq_id in TopicPrerequisite.objects.filter(prerequisite_id__in=[c.topic_id for c in cards]).values_list("prerequisite_id", flat=True):
        dependents[prereq_id] = dependents.get(prereq_id, 0) + 1

    def priority(card):
        return dependents.get(card.topic_id, 0) * 0.15 + (1 - retrievability(card, now))

    return sorted(cards, key=priority, reverse=True)


def due_count(user, now=None):
    from apps.curriculum.models import Topic

    return ReviewCard.objects.filter(
        user=user, due__lte=now or timezone.now(), topic__status=Topic.Status.PUBLISHED, topic__unit__is_published=True
    ).count()

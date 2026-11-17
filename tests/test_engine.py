"""Learning engine, prerequisites, planner, FSRS scheduling and sessions."""
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.curriculum.services import ancestors, set_prerequisites, would_create_cycle
from apps.learning import engine, planner, sessions
from apps.learning.evaluation import Evaluation, evaluate
from apps.learning.models import Attempt, SessionItem, Stage, StudySession, TopicMastery
from apps.questions.models import AnswerVerdict
from apps.reviews import services as reviews
from apps.reviews.models import ReviewCard
from fsrs import Rating

from . import factories as f


def correct(q):
    return Evaluation(True, 1.0)


def wrong(q, tag="tense"):
    return Evaluation(False, 0.0, error_tag=tag)


class PrerequisiteGraphTests(TestCase):
    def test_cycle_detection(self):
        a = f.topic()
        b = f.topic(prerequisites=[a])
        c = f.topic(prerequisites=[b])
        self.assertTrue(would_create_cycle(a.id, c.id))
        self.assertFalse(would_create_cycle(c.id, a.id))
        self.assertEqual(ancestors(c.id), {a.id, b.id})
        with self.assertRaises(ValidationError):
            set_prerequisites(a, [(c.id, "hard")])

    def test_locked_until_hard_prerequisite_reaches_apply(self):
        user = f.learner()
        base = f.topic(offset=0.1)
        advanced = f.topic(offset=0.5, prerequisites=[base])
        lmap = planner.LearnerMap(user)
        self.assertFalse(lmap.is_unlocked(advanced.id))
        self.assertEqual([t.id for t in lmap.next_new(5)], [base.id])
        TopicMastery.objects.create(user=user, topic=base, stage=Stage.PRACTICE)
        self.assertFalse(planner.LearnerMap(user).is_unlocked(advanced.id))
        TopicMastery.objects.filter(user=user, topic=base).update(stage=Stage.APPLY)
        self.assertTrue(planner.LearnerMap(user).is_unlocked(advanced.id))

    def test_soft_prerequisite_does_not_lock(self):
        user = f.learner()
        base = f.topic()
        other = f.topic(soft=[base])
        self.assertTrue(planner.LearnerMap(user).is_unlocked(other.id))

    def test_weak_prerequisite_is_surfaced_before_dependent(self):
        user = f.learner()
        base = f.topic()
        dependent = f.topic(prerequisites=[base])
        TopicMastery.objects.create(user=user, topic=base, stage=Stage.APPLY, score=0.4)
        TopicMastery.objects.create(user=user, topic=dependent, stage=Stage.PRACTICE, is_weak=True, score=0.3)
        self.assertEqual(planner.LearnerMap(user).weak(), [base.id, dependent.id])


class EngineTests(TestCase):
    def setUp(self):
        self.user = f.learner()
        self.topic = f.topic()
        self.questions = [f.gap(self.topic) for _ in range(4)] + [f.mcq(self.topic) for _ in range(4)] + [f.production(self.topic) for _ in range(4)]

    def answer(self, q, evaluation, now, ms=8000):
        return engine.record_attempt(self.user, q, evaluation, raw_answer="x", response_ms=ms, now=now)

    def test_uncounted_answer_never_moves_mastery(self):
        now = timezone.now()
        engine.mark_theory_seen(self.user, self.topic, now)
        _, tm, transition = self.answer(self.questions[0], Evaluation(False, 0.0, counted=False), now)
        self.assertIsNone(transition)
        self.assertEqual(tm.attempts, 0)
        self.assertEqual(tm.score, 0)
        self.assertTrue(Attempt.objects.filter(counted=False).exists())

    def test_repeat_index_and_delay_are_tracked(self):
        now = timezone.now()
        q = self.questions[0]
        a1, *_ = self.answer(q, correct(q), now)
        a2, *_ = self.answer(q, correct(q), now + timedelta(minutes=5))
        a3, *_ = self.answer(self.questions[1], correct(q), now + timedelta(days=2))
        self.assertEqual((a1.repeat_index, a2.repeat_index), (0, 1))
        self.assertAlmostEqual(a2.hours_since_seen, 5 / 60, places=3)
        self.assertTrue(a3.delayed)
        self.assertFalse(a2.delayed)

    def test_full_cycle_requires_breadth_and_delayed_recall(self):
        now = timezone.now()
        engine.mark_theory_seen(self.user, self.topic, now)
        tm = None
        for i, q in enumerate(self.questions[:11]):
            _, tm, _ = self.answer(q, correct(q), now + timedelta(minutes=i + 1))
        tm.refresh_from_db()
        self.assertEqual(tm.stage, Stage.MASTER, "same-day evidence can reach Master but never Retain")
        self.assertIn(tm.delayed_correct, (0,))
        later = now + timedelta(days=2)
        _, tm, transition = self.answer(self.questions[11], correct(None), later)
        self.assertEqual(tm.stage, Stage.RETAIN)
        self.assertTrue(transition.mastered)
        self.assertIsNotNone(tm.mastered_at)
        self.assertTrue(ReviewCard.objects.filter(user=self.user, topic=self.topic).exists())

    def test_struggling_triggers_reteach_and_weakness(self):
        now = timezone.now()
        engine.mark_theory_seen(self.user, self.topic, now)
        TopicMastery.objects.filter(user=self.user, topic=self.topic).update(stage=Stage.PRACTICE, stage_entered_at=now)
        transition = None
        for i, q in enumerate(self.questions[:4]):
            _, tm, transition = self.answer(q, wrong(q), now + timedelta(minutes=i + 1))
        self.assertTrue(transition.reteach)
        self.assertEqual(tm.stage, Stage.LEARN)
        self.assertTrue(tm.needs_reteach)
        self.assertTrue(tm.is_weak)
        self.assertIsNone(tm.theory_seen_at)

    def test_mistake_patterns_accumulate(self):
        now = timezone.now()
        for i, q in enumerate(self.questions[:3]):
            self.answer(q, wrong(q, "article"), now + timedelta(minutes=i))
        self.assertEqual(self.user.mistake_patterns.get(error_tag="article").count, 3)


class FSRSTests(TestCase):
    def test_rating_mapping(self):
        self.assertEqual(reviews.rating_from_performance([(0, 5000, 20000), (0.2, 5000, 20000)]), Rating.Again)
        self.assertEqual(reviews.rating_from_performance([(1, 30000, 20000), (0, 5000, 20000), (1, 5000, 20000)]), Rating.Hard)
        self.assertEqual(reviews.rating_from_performance([(1, 30000, 20000)] * 3), Rating.Good)
        self.assertEqual(reviews.rating_from_performance([(1, 5000, 20000)] * 3), Rating.Easy)

    def test_good_reviews_grow_intervals_and_again_shrinks(self):
        user, topic = f.learner(), f.topic()
        now = timezone.now()
        card = reviews.ensure_card(user, topic, first_due=now)
        reviews.apply_review(card, [(1, 30000, 20000)] * 3, now=now)
        first = card.due - now
        reviews.apply_review(card, [(1, 30000, 20000)] * 3, now=card.due)
        second = card.due - card.last_reviewed_at
        self.assertGreater(second, first)
        before = card.stability
        reviews.apply_review(card, [(0, 5000, 20000)] * 3, now=card.due)
        self.assertLess(card.stability, before)
        self.assertEqual((card.lapses, card.consecutive_lapses), (1, 1))
        self.assertEqual(card.logs.count(), 3)

    def test_due_order_prefers_foundational_topics(self):
        user = f.learner()
        base, leaf = f.topic(), f.topic()
        f.topic(prerequisites=[base])
        f.topic(prerequisites=[base])
        now = timezone.now()
        for t in (leaf, base):
            reviews.ensure_card(user, t, first_due=now - timedelta(days=1))
        self.assertEqual(reviews.due_cards(user, now)[0].topic_id, base.id)

    def test_inactive_topics_are_not_due(self):
        user, topic = f.learner(), f.topic(status="inactive")
        reviews.ensure_card(user, topic, first_due=timezone.now() - timedelta(days=1))
        self.assertEqual(reviews.due_count(user), 0)


class SessionTests(TestCase):
    def setUp(self):
        self.user = f.learner(daily_goal_minutes=10)
        self.topic = f.topic()
        for _ in range(4):
            f.gap(self.topic)
            f.mcq(self.topic)
            f.production(self.topic)

    def test_daily_session_starts_with_theory_for_new_topic(self):
        session = sessions.start_session(self.user, StudySession.Kind.DAILY)
        first = sessions.current_item(session)
        self.assertEqual(first.kind, SessionItem.Kind.THEORY)
        self.assertLessEqual(session.items.count() * 40, 10 * 60 + 150)

    def test_wrong_answer_inserts_targeted_remedial_item(self):
        session = sessions.start_session(self.user, StudySession.Kind.TOPIC, topic=self.topic)
        sessions.complete_theory(sessions.current_item(session))
        item = sessions.current_item(session)
        before = session.items.count()
        outcome = sessions.submit_answer(item, f.answer_for(item.question, correct=False), client_ms=5000)
        self.assertFalse(outcome.evaluation.is_correct)
        self.assertIn("remedial", outcome.notes)
        self.assertEqual(session.items.count(), before + 1)
        remedial = session.items.get(purpose="remedial")
        self.assertEqual(remedial.position, item.position + 2)

    def test_questions_are_not_repeated_within_a_session(self):
        session = sessions.start_session(self.user, StudySession.Kind.TOPIC, topic=self.topic)
        sessions.complete_theory(sessions.current_item(session))
        seen = []
        while (item := sessions.current_item(session)) is not None:
            seen.append(item.question_id)
            sessions.submit_answer(item, f.answer_for(item.question), client_ms=4000)
        self.assertEqual(len(seen), len(set(seen)))

    def test_cannot_answer_twice(self):
        session = sessions.start_session(self.user, StudySession.Kind.TOPIC, topic=self.topic)
        sessions.complete_theory(sessions.current_item(session))
        item = sessions.current_item(session)
        sessions.submit_answer(item, f.answer_for(item.question))
        with self.assertRaises(ValueError):
            sessions.submit_answer(item, f.answer_for(item.question))

    def test_review_session_feeds_fsrs_on_finalize(self):
        now = timezone.now()
        card = reviews.ensure_card(self.user, self.topic, first_due=now - timedelta(hours=1))
        TopicMastery.objects.create(user=self.user, topic=self.topic, stage=Stage.RETAIN, theory_seen_at=now)
        session = sessions.start_session(self.user, StudySession.Kind.REVIEW, now=now)
        while (item := sessions.current_item(session, now)) is not None:
            sessions.submit_answer(item, f.answer_for(item.question), client_ms=5000, now=now + timedelta(seconds=5))
        sessions.finalize(session, now)
        card.refresh_from_db()
        self.assertGreater(card.due, now + timedelta(hours=12))
        self.assertEqual(card.reps, 1)

    def test_repeated_review_lapses_demote_mastered_topic(self):
        now = timezone.now()
        card = reviews.ensure_card(self.user, self.topic, first_due=now)
        card.consecutive_lapses = 1
        card.save()
        TopicMastery.objects.create(user=self.user, topic=self.topic, stage=Stage.RETAIN, theory_seen_at=now)
        session = sessions.start_session(self.user, StudySession.Kind.REVIEW, now=now)
        while (item := sessions.current_item(session, now)) is not None:
            sessions.submit_answer(item, f.answer_for(item.question, correct=False), now=now + timedelta(seconds=5))
        sessions.finalize(session, now)
        tm = TopicMastery.objects.get(user=self.user, topic=self.topic)
        self.assertEqual(tm.stage, Stage.PRACTICE)
        self.assertEqual(tm.weak_reason, "forgotten")

    def test_comeback_after_long_break_skips_new_material(self):
        now = timezone.now()
        old = f.topic()
        f.gap(old)
        TopicMastery.objects.create(user=self.user, topic=old, stage=Stage.RETAIN, theory_seen_at=now)
        reviews.ensure_card(self.user, old, first_due=now - timedelta(days=20))
        profile = self.user.profile
        profile.last_active_date = (now - timedelta(days=21)).date()
        profile.save()
        plan = planner.plan_daily(self.user, 10, now)
        self.assertTrue(plan)
        self.assertFalse([s for s in plan if s.purpose == "new"], "no new topics while restoring after a long break")

    def test_timed_challenge_scores_late_answers_as_wrong(self):
        now = timezone.now()
        TopicMastery.objects.create(user=self.user, topic=self.topic, stage=Stage.PRACTICE, theory_seen_at=now)
        session = sessions.start_session(self.user, StudySession.Kind.TIMED, now=now)
        item = sessions.current_item(session, now)
        self.assertIn(item.question.type, ("mcq", "gap"))
        outcome = sessions.submit_answer(item, f.answer_for(item.question), now=now + timedelta(minutes=5))
        self.assertTrue(outcome.attempt.timed_out)
        self.assertFalse(outcome.evaluation.is_correct)


class EvaluationTests(TestCase):
    def setUp(self):
        self.topic = f.topic()
        self.q = f.production(self.topic, qtype="translation")

    def test_unlisted_answer_without_ai_is_not_counted(self):
        result = evaluate(self.q, "Some completely different but maybe valid sentence")
        self.assertFalse(result.counted)
        self.assertEqual(result.evaluated_by, "none")

    def test_stored_verdict_is_reused(self):
        AnswerVerdict.objects.create(question=self.q, normalized="she dislikes tea", is_correct=True, score=1.0, source="admin")
        result = evaluate(self.q, "She dislikes tea.")
        self.assertTrue(result.is_correct)
        self.assertEqual(result.evaluated_by, "stored")

    def test_validated_ai_verdict_is_stored_and_reused(self):
        from apps.ai.schemas import AnswerEvaluation

        verdict = AnswerEvaluation(verdict="correct", feedback_uz="Javob to'g'ri, muqobil ifoda.", corrected="")
        with mock.patch("apps.learning.evaluation.ai_evaluation_available", return_value=True), mock.patch("apps.ai.services.evaluate_answer", return_value=verdict) as call:
            first = evaluate(self.q, "She is not fond of tea.")
            second = evaluate(self.q, "she is NOT fond of tea")
        self.assertTrue(first.is_correct)
        self.assertEqual(first.evaluated_by, "ai")
        self.assertEqual(second.evaluated_by, "stored")
        self.assertEqual(call.call_count, 1)

    def test_failed_ai_call_leaves_answer_uncounted(self):
        with mock.patch("apps.learning.evaluation.ai_evaluation_available", return_value=True), mock.patch("apps.ai.services.evaluate_answer", return_value=None):
            result = evaluate(self.q, "Something else entirely here")
        self.assertFalse(result.counted)
        self.assertFalse(AnswerVerdict.objects.exists())

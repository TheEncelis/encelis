from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.assessments import services as placement
from apps.assessments.models import PlacementSession
from apps.learning.models import Stage, TopicMastery
from apps.questions import checking
from apps.reviews.models import ReviewCard

from . import factories as f


class EstimatorTests(SimpleTestCase):
    def test_posterior_moves_with_responses(self):
        posterior = placement.prior()
        start, _ = placement.estimate(posterior)
        up, _ = placement.estimate(placement.update_posterior(posterior, 2.0, True))
        down, _ = placement.estimate(placement.update_posterior(posterior, 2.0, False))
        self.assertGreater(up, start)
        self.assertLess(down, start)

    def test_guessable_items_carry_less_information(self):
        posterior = placement.prior()
        plain, _ = placement.estimate(placement.update_posterior(posterior, 3.0, True))
        guessed, _ = placement.estimate(placement.update_posterior(posterior, 3.0, True, guess=1 / 3))
        self.assertGreater(plain, guessed)

    def test_uncertainty_shrinks(self):
        posterior = placement.prior()
        _, sd0 = placement.estimate(posterior)
        for d, c in [(1.5, True), (2.5, False), (2.0, True), (2.2, False)]:
            posterior = placement.update_posterior(posterior, d, c)
        _, sd1 = placement.estimate(posterior)
        self.assertLess(sd1, sd0)

    def test_stop_rule(self):
        self.assertFalse(placement.should_stop(5, 0.3))
        self.assertTrue(placement.should_stop(8, 0.4))
        self.assertFalse(placement.should_stop(8, 0.9))
        self.assertTrue(placement.should_stop(16, 0.9))


class PlacementFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_curriculum", verbosity=0)

    def run_placement(self, oracle):
        user = f.learner(placement_done=False)
        session = placement.start(user)
        while (q := placement.next_question(session)) is not None:
            placement.answer(session, oracle(q))
            session.refresh_from_db()
        placement.finish(session)
        user.profile.refresh_from_db()
        return user, session

    def perfect(self, q):
        return f.answer_for(q, correct=True)

    def test_strong_learner_places_higher_and_gets_verified_credit(self):
        strong, session = self.run_placement(self.perfect)
        weak, _ = self.run_placement(lambda q: f.answer_for(q, correct=False))
        self.assertGreater(strong.profile.ability, weak.profile.ability + 2)
        self.assertLessEqual(session.responses.count(), placement.MAX_ITEMS)
        self.assertTrue(strong.profile.placement_done)
        assumed = TopicMastery.objects.filter(user=strong, assumed=True)
        self.assertTrue(assumed.exists())
        self.assertTrue(all(m.stage == Stage.MASTER for m in assumed))
        # every credited topic gets a verification review
        self.assertEqual(ReviewCard.objects.filter(user=strong, topic__in=[m.topic for m in assumed]).count(), assumed.count())
        self.assertEqual(TopicMastery.objects.filter(user=weak, assumed=True).count(), 0)
        # probing a topic is evidence, not the start of learning it
        self.assertFalse(TopicMastery.objects.filter(user__in=[strong, weak], stage__in=[Stage.LEARN, Stage.UNDERSTAND, Stage.PRACTICE]).exists())

    def test_no_item_is_asked_twice(self):
        _, session = self.run_placement(self.perfect)
        ids = list(session.responses.values_list("question_id", flat=True))
        self.assertEqual(len(ids), len(set(ids)))

    def test_missed_easy_topic_becomes_visible_weakness(self):
        import random

        random.seed(3)

        # A learner who misses everything is driven down to A1, so A1 items are
        # always probed — the precondition does not depend on item randomness.
        def miss_a1(q):
            return f.answer_for(q, correct=False)

        user, session = self.run_placement(miss_a1)
        self.assertTrue(session.responses.filter(topic__unit__level__code="A1", is_correct=False).exists(), "precondition: an A1 item was probed")
        self.assertTrue(TopicMastery.objects.filter(user=user, topic__unit__level__code="A1", is_weak=True).exists())

    def test_skip_starts_at_bottom(self):
        user = f.learner(placement_done=False)
        placement.skip(user)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.placement_done)
        self.assertEqual(user.profile.cefr, "A1")
        self.assertEqual(PlacementSession.objects.get(user=user).status, "skipped")

    def test_seed_answers_satisfy_checker(self):
        from apps.questions.models import Question

        for q in Question.objects.exclude(type="free_production"):
            with self.subTest(q=q.prompt):
                self.assertEqual(checking.check(q, f.answer_for(q)).verdict, checking.CORRECT)

"""Pure mastery logic: evidence weighting, anti-inflation, stage transitions."""
from django.test import SimpleTestCase

from apps.learning import mastery
from apps.learning.mastery import Evidence, compute_signals, decide_stage, mastery_gaps
from apps.learning.models import Stage


def ev(score=1.0, q_type="gap", qid=1, repeat=0, hours=None, delayed=False, ms=10000, expected=25000, in_stage=True, tag="", difficulty=3):
    return Evidence(score, q_type, difficulty, ms, expected, qid, repeat, hours, delayed, in_stage, tag)


class SignalsTests(SimpleTestCase):
    def test_no_evidence(self):
        self.assertEqual(compute_signals([]).score, 0)

    def test_few_lucky_answers_do_not_give_high_mastery(self):
        signals = compute_signals([ev(qid=1), ev(qid=2)])
        self.assertEqual(signals.accuracy, 1.0)
        self.assertLess(signals.score, 0.5, "confidence must cap a score backed by little evidence")

    def test_repeating_the_same_question_does_not_inflate_mastery(self):
        repeated = [ev(qid=1)] + [ev(qid=1, repeat=i, hours=0.1) for i in range(1, 15)]
        varied = [ev(qid=i, q_type=t) for i, t in enumerate(["gap", "mcq", "transform", "error_correction", "translation", "gap", "construction"], 1)]
        self.assertLess(compute_signals(repeated).score, compute_signals(varied).score)
        self.assertLess(compute_signals(repeated).confidence, 0.6)
        self.assertEqual(compute_signals(repeated).distinct_questions, 1)

    def test_production_and_types_only_count_first_exposures(self):
        signals = compute_signals([ev(qid=1, q_type="transform"), ev(qid=1, q_type="transform", repeat=1, hours=48)])
        self.assertEqual(signals.production_correct, 1)

    def test_slow_answers_reduce_score_and_fluency(self):
        fast = compute_signals([ev(qid=i, ms=10000) for i in range(8)])
        slow = compute_signals([ev(qid=i, ms=60000) for i in range(8)])
        self.assertLess(slow.score, fast.score)
        self.assertEqual(fast.fluency, 1.0)
        self.assertEqual(slow.fluency, 0.0)

    def test_harder_and_delayed_evidence_weighs_more(self):
        easy = ev(difficulty=1)
        hard_delayed = ev(difficulty=5, delayed=True)
        self.assertGreater(mastery.evidence_weight(hard_delayed), mastery.evidence_weight(easy))

    def test_recent_answers_dominate(self):
        old_bad_new_good = compute_signals([ev(qid=i, score=0) for i in range(6)] + [ev(qid=10 + i) for i in range(6)])
        old_good_new_bad = compute_signals([ev(qid=i) for i in range(6)] + [ev(qid=10 + i, score=0) for i in range(6)])
        self.assertGreater(old_bad_new_good.accuracy, old_good_new_bad.accuracy)

    def test_repeated_error_category_detected(self):
        signals = compute_signals([ev(qid=i, score=0, tag="article") for i in range(3)])
        self.assertEqual(signals.repeated_error, "article")


def signals(**kw):
    base = dict(score=0.9, confidence=1, accuracy=0.9, recent_accuracy=0.9, fluency=0.9, attempts=12, distinct_questions=10, distinct_types=4,
                production_correct=4, delayed_correct=1, stage_attempts=5, stage_recent_correct=4, stage_recent_total=4, repeated_error="")  # fmt: skip
    base.update(kw)
    return mastery.Signals(**base)


class StageTests(SimpleTestCase):
    def test_evidence_alone_never_starts_a_topic(self):
        self.assertEqual(decide_stage(Stage.NEW, signals(), theory_seen=False).stage, Stage.NEW)

    def test_learn_requires_theory(self):
        self.assertEqual(decide_stage(Stage.LEARN, signals(), theory_seen=False).stage, Stage.LEARN)
        self.assertEqual(decide_stage(Stage.LEARN, signals(), theory_seen=True).stage, Stage.UNDERSTAND)

    def test_moves_only_one_stage_at_a_time(self):
        self.assertEqual(decide_stage(Stage.UNDERSTAND, signals(), True).stage, Stage.PRACTICE)
        self.assertEqual(decide_stage(Stage.PRACTICE, signals(), True).stage, Stage.APPLY)

    def test_struggling_in_understand_returns_to_learn_with_reteach(self):
        decision = decide_stage(Stage.PRACTICE, signals(stage_recent_correct=1, stage_recent_total=4), True)
        self.assertEqual(decision.stage, Stage.LEARN)
        self.assertTrue(decision.reteach)

    def test_struggling_in_apply_returns_to_practice(self):
        self.assertEqual(decide_stage(Stage.APPLY, signals(stage_recent_correct=0, stage_recent_total=4), True).stage, Stage.PRACTICE)

    def test_master_requires_every_dimension(self):
        self.assertEqual(decide_stage(Stage.MASTER, signals(), True).stage, Stage.RETAIN)
        for gap, override in [("delayed", {"delayed_correct": 0}), ("types", {"distinct_types": 2}), ("production", {"production_correct": 2}),
                              ("fluency", {"fluency": 0.4}), ("score", {"score": 0.7}), ("repeated_error", {"repeated_error": "tense"})]:  # fmt: skip
            with self.subTest(gap=gap):
                s = signals(**override)
                self.assertIn(gap, mastery_gaps(s))
                self.assertEqual(decide_stage(Stage.MASTER, s, True).stage, Stage.MASTER)

    def test_retained_topic_decays_back_to_practice(self):
        self.assertEqual(decide_stage(Stage.RETAIN, signals(score=0.4, recent_accuracy=0.2, attempts=6), True).stage, Stage.PRACTICE)

    def test_practice_needs_type_variety(self):
        self.assertEqual(decide_stage(Stage.PRACTICE, signals(distinct_types=1), True).stage, Stage.PRACTICE)


class AbilityTests(SimpleTestCase):
    def test_ability_moves_toward_performance(self):
        self.assertGreater(mastery.updated_ability(1.0, 1.0, 1.0), 1.0)
        self.assertLess(mastery.updated_ability(1.0, 1.0, 0.0), 1.0)
        self.assertLessEqual(mastery.updated_ability(6.0, 0.0, 1.0), 6.0)

    def test_guessing_reduces_credit(self):
        plain = mastery.updated_ability(2.0, 2.0, 1.0)
        guessable = mastery.updated_ability(2.0, 2.0, 1.0, guess=1 / 3)
        self.assertLess(guessable, plain)

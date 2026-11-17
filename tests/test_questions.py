"""Deterministic answer checking, mistake classification and question validation."""
from django.test import SimpleTestCase, TestCase

from apps.questions import checking
from apps.questions.models import Question
from apps.questions.services import DuplicateQuestion, save_question
from apps.questions.validation import QuestionInvalid, validate_question

from . import factories as f


class Q:
    """Unsaved question stand-in for pure checking tests."""

    def __init__(self, qtype, prompt, payload, error_tag="structure"):
        self.type, self.prompt, self.payload, self.error_tag = qtype, prompt, payload, error_tag


class NormalizationTests(SimpleTestCase):
    def test_contractions_are_equivalent(self):
        self.assertTrue(checking.same("She doesn't like it.", "she does not like it"))
        self.assertTrue(checking.same("I'm   ready!", "I am ready"))
        self.assertTrue(checking.same("He’s gone", "he has gone"))
        self.assertTrue(checking.same("It's raining", "it is raining"))
        self.assertTrue(checking.same("We can't go", "we cannot go"))
        self.assertFalse(checking.same("She likes it", "she does not like it"))

    def test_classifies_common_mistakes(self):
        self.assertEqual(checking.classify_mistake("I have a apple", ["I have an apple"]), "article")
        self.assertEqual(checking.classify_mistake("she like tea", ["she likes tea"]), "verb_form")
        self.assertEqual(checking.classify_mistake("they is happy", ["they are happy"]), "agreement")
        self.assertEqual(checking.classify_mistake("tea she likes", ["she likes tea"]), "word_order")
        self.assertEqual(checking.classify_mistake("I live at Tashkent", ["I live in Tashkent"]), "preposition")
        self.assertEqual(checking.classify_mistake("the wether is nice", ["the weather is nice"]), "spelling")


class CheckTests(SimpleTestCase):
    def test_mcq(self):
        q = Q("mcq", "x ___", {"options": [{"text": "a"}, {"text": "b", "error": "tense"}, {"text": "c"}], "correct": 0})
        self.assertEqual(checking.check(q, 0).verdict, checking.CORRECT)
        wrong = checking.check(q, 1)
        self.assertEqual((wrong.verdict, wrong.error_tag), (checking.INCORRECT, "tense"))
        self.assertEqual(checking.check(q, "junk").verdict, checking.INCORRECT)

    def test_gap_with_alternatives_and_partial_credit(self):
        q = Q("gap", "She ___ not ___ here.", {"blanks": [["does", "did"], ["live"]]})
        self.assertEqual(checking.check(q, ["Does", " live "]).verdict, checking.CORRECT)
        partial = checking.check(q, ["does", "lives"])
        self.assertEqual(partial.verdict, checking.INCORRECT)
        self.assertEqual(partial.score, 0.25)

    def test_unlisted_text_answer_is_uncertain_not_wrong(self):
        q = Q("translation", "U uyda.", {"answers": ["She is at home."]})
        self.assertEqual(checking.check(q, "she's at home").verdict, checking.CORRECT)
        self.assertEqual(checking.check(q, "She stays at home.").verdict, checking.UNCERTAIN)

    def test_construction_mismatch_is_definitely_wrong(self):
        q = Q("construction", "uz", {"tokens": ["tea", "likes", "she"], "answers": ["She likes tea."]})
        self.assertEqual(checking.check(q, "she likes tea").verdict, checking.CORRECT)
        result = checking.check(q, "tea likes she")
        self.assertEqual((result.verdict, result.error_tag), (checking.INCORRECT, "word_order"))

    def test_unchanged_error_sentence_is_wrong(self):
        q = Q("error_correction", "He go home.", {"answers": ["He goes home."]}, "agreement")
        self.assertEqual(checking.check(q, "He go home").verdict, checking.INCORRECT)

    def test_free_production_precheck(self):
        q = Q("free_production", "task", {"sample": "I can swim well.", "criteria": "c", "required_patterns": [r"\bcan\b"], "min_words": 4})
        self.assertEqual(checking.check(q, "I swim").verdict, checking.INCORRECT)
        self.assertEqual(checking.check(q, "I like to swim every day").verdict, checking.INCORRECT)  # missing pattern
        self.assertEqual(checking.check(q, "Мен сузаман ва юраман").verdict, checking.INCORRECT)
        self.assertEqual(checking.check(q, "I can swim very well").verdict, checking.UNCERTAIN)


class ValidationTests(SimpleTestCase):
    def ok(self, qtype, prompt, payload, **kw):
        return validate_question(qtype, prompt, payload, explanation="Izoh.", **kw)

    def bad(self, qtype, prompt, payload, **kw):
        with self.assertRaises(QuestionInvalid) as ctx:
            validate_question(qtype, prompt, payload, explanation=kw.pop("explanation", "Izoh."), **kw)
        return ctx.exception.errors

    def test_mcq_rules(self):
        self.ok("mcq", "She ___ here.", {"options": ["is", "are", "am"], "correct": 0})
        self.bad("mcq", "She ___ here.", {"options": ["is", "is", "am"], "correct": 0})
        self.bad("mcq", "She ___ here.", {"options": ["is", "are", "am"], "correct": 5})
        self.bad("mcq", "She ___ here.", {"options": ["is", "are"], "correct": 0})

    def test_gap_blank_count_must_match(self):
        self.bad("gap", "She ___ to ___.", {"blanks": [["goes"]]})
        self.bad("gap", "No blank here at all.", {"blanks": [["x"]]})
        self.ok("gap", "She ___ to work.", {"blanks": [["goes"]]})

    def test_construction_tokens_must_form_every_answer(self):
        self.ok("construction", "U choy ichadi.", {"tokens": ["tea", "drinks", "She"], "answers": ["She drinks tea."]})
        self.bad("construction", "U choy ichadi.", {"tokens": ["tea", "drinks", "He"], "answers": ["She drinks tea."]})

    def test_answer_cannot_equal_prompt(self):
        self.bad("error_correction", "He goes home.", {"answers": ["He goes home."]})

    def test_explanation_required_and_english_fields_latin(self):
        self.bad("gap", "She ___ home.", {"blanks": [["is"]]}, explanation="")
        self.bad("transform", "Она дома.", {"answers": ["She is at home."]}, instruction="x")

    def test_free_sample_must_satisfy_patterns(self):
        self.bad("free_production", "Yozing.", {"sample": "I swim.", "criteria": "c", "required_patterns": [r"\bcan\b"]})
        self.bad("free_production", "Yozing.", {"sample": "I can.", "criteria": "c", "required_patterns": ["(unclosed"]})


class PersistenceTests(TestCase):
    def test_duplicates_are_rejected(self):
        t = f.topic()
        f.gap(t, n=1)
        with self.assertRaises(DuplicateQuestion):
            f.gap(t, n=1)

    def test_editing_clears_stored_verdicts(self):
        t = f.topic()
        q = f.production(t)
        q.verdicts.create(normalized="x", is_correct=True, score=1)
        save_question(t, {"type": q.type, "prompt": q.prompt + " Edited.", "payload": q.payload, "explanation": "Yangi izoh.", "instruction": "Inkor qiling."},
                      source=q.source, status=Question.Status.APPROVED, instance=q)  # fmt: skip
        self.assertFalse(q.verdicts.exists())

    def test_seed_curriculum_is_valid_and_idempotent(self):
        from django.core.management import call_command

        call_command("seed_curriculum", verbosity=0)
        count = Question.objects.count()
        self.assertGreater(count, 200)
        call_command("seed_curriculum", verbosity=0)
        self.assertEqual(Question.objects.count(), count)

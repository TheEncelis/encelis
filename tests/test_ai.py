"""AI service layer: transport retries, schema validation, generation pipeline gates."""
import json
from unittest import mock

import httpx
from django.test import TestCase, override_settings

from apps.ai import client, pipeline, schemas
from apps.ai.models import AIUsageLog, GenerationJob
from apps.questions.models import Question

from . import factories as f


def api_response(payload, status=200):
    body = {"choices": [{"message": {"content": payload if isinstance(payload, str) else json.dumps(payload)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"))


GOOD = {"verdict": "correct", "error_category": None, "feedback_uz": "Javobingiz to'g'ri va tabiiy.", "corrected": ""}


@override_settings(DEEPSEEK_API_KEY="test-key")
class ClientTests(TestCase):
    def test_valid_output_is_parsed_and_logged(self):
        with mock.patch("httpx.post", return_value=api_response(GOOD)) as post:
            result = client.complete("evaluate_answer", "sys", "prompt", schemas.AnswerEvaluation)
        self.assertEqual(result.verdict, "correct")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        log = AIUsageLog.objects.get()
        self.assertTrue(log.success)
        self.assertEqual(log.prompt_tokens, 10)

    def test_deep_tasks_use_pro_with_thinking(self):
        with mock.patch("httpx.post", return_value=api_response({"verdict_uz": "Umuman yaxshi nazariya.", "issues": []})) as post:
            client.complete("review_theory", "sys", "prompt", schemas.TheoryReview)
        body = post.call_args.kwargs["json"]
        self.assertEqual((body["model"], body["thinking"]["type"]), ("deepseek-v4-pro", "enabled"))

    def test_invalid_output_is_retried_once_then_rejected(self):
        bad = {"verdict": "maybe", "feedback_uz": "x"}
        with mock.patch("httpx.post", side_effect=[api_response(bad), api_response(GOOD)]):
            self.assertEqual(client.complete("evaluate_answer", "s", "p", schemas.AnswerEvaluation).verdict, "correct")
        with mock.patch("httpx.post", side_effect=[api_response(bad), api_response("not json")]):
            with self.assertRaises(client.AIError):
                client.complete("evaluate_answer", "s", "p", schemas.AnswerEvaluation)
        self.assertFalse(AIUsageLog.objects.latest("id").success)

    def test_transient_errors_are_retried(self):
        with mock.patch("httpx.post", side_effect=[api_response({}, status=503), api_response(GOOD)]), mock.patch("time.sleep"):
            self.assertEqual(client.complete("evaluate_answer", "s", "p", schemas.AnswerEvaluation).verdict, "correct")

    def test_cyrillic_feedback_rejected(self):
        with self.assertRaises(ValueError):
            schemas.AnswerEvaluation(verdict="correct", feedback_uz="Жавоб тўғри, яхши.", corrected="")

    def test_incorrect_verdict_needs_correction(self):
        with self.assertRaises(ValueError):
            schemas.AnswerEvaluation(verdict="incorrect", feedback_uz="Fe'l shakli noto'g'ri.", corrected="")


@override_settings(DEEPSEEK_API_KEY="test-key")
class EvaluateAnswerGuardTests(TestCase):
    def test_answer_equal_to_prompt_is_never_accepted(self):
        from apps.ai.services import evaluate_answer

        t = f.topic()
        q = f.production(t, qtype="error_correction")
        with mock.patch("apps.ai.services.complete", return_value=schemas.AnswerEvaluation(**GOOD)):
            self.assertIsNone(evaluate_answer(q, q.prompt))


@override_settings(DEEPSEEK_API_KEY="test-key")
class PipelineTests(TestCase):
    def setUp(self):
        self.topic = f.topic()

    def candidate(self, n, key=0):
        return {"type": "mcq", "difficulty": 2, "prompt": f"She ___ to work every day {n}.", "instruction": "Tanlang.", "explanation": "3-shaxs birlik.",
                "error_tag": "agreement", "payload": {"options": [{"text": "goes"}, {"text": "go"}, {"text": "going"}], "correct": key}}  # fmt: skip

    def run_pipeline(self, candidates, solved=True):
        job = GenerationJob.objects.create(kind="questions", topic=self.topic, params={"type": "mcq", "count": len(candidates), "difficulty": 2})
        with mock.patch("apps.ai.services.generate_questions", return_value=candidates), mock.patch("apps.ai.services.verify_by_solving", return_value=(solved, "" if solved else "mos kelmadi")):
            return pipeline.run_job(job.id)

    def test_valid_questions_wait_for_approval(self):
        job = self.run_pipeline([self.candidate(1), self.candidate(2)])
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["saved"], 2)
        self.assertEqual(set(Question.objects.values_list("status", flat=True)), {"pending"})

    def test_invalid_and_duplicate_questions_are_rejected(self):
        broken = self.candidate(3)
        broken["payload"]["correct"] = 9
        job = self.run_pipeline([self.candidate(1), self.candidate(1), broken])
        self.assertEqual(job.result["saved"], 1)
        self.assertEqual(len(job.result["rejected"]), 2)

    def test_cross_check_failure_rejects(self):
        job = self.run_pipeline([self.candidate(1)], solved=False)
        self.assertEqual(job.result["saved"], 0)
        self.assertFalse(Question.objects.exists())

    def test_ai_failure_marks_job_failed(self):
        job = GenerationJob.objects.create(kind="questions", topic=self.topic, params={"type": "mcq", "count": 2, "difficulty": 2})
        with mock.patch("apps.ai.services.generate_questions", side_effect=client.AIError("boom")):
            job = pipeline.run_job(job.id)
        self.assertEqual(job.status, "failed")

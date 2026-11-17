"""End-to-end HTTP flows: registration, gating, study session, reports, studio access."""
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.learning.models import SessionItem, StudySession
from apps.questions.models import Question, QuestionReport

from . import factories as f


class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_curriculum", verbosity=0)

    def setUp(self):
        cache.clear()

    def test_register_sends_verification_and_requires_placement(self):
        response = self.client.post(reverse("accounts:register"), {"first_name": "Aziz", "email": "Aziz@Example.com", "password": "Qalampir-2024"})
        self.assertRedirects(response, reverse("assessments:start"))
        user = User.objects.get(email="aziz@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/verify-email/", mail.outbox[0].body)
        # learner pages are gated until placement is done
        self.assertRedirects(self.client.get(reverse("learning:dashboard")), reverse("assessments:start"))
        self.client.post(reverse("assessments:skip"))
        self.assertEqual(self.client.get(reverse("learning:dashboard")).status_code, 200)
        token_url = mail.outbox[0].body.split("http://localhost:8000")[1].split()[0]
        self.client.get(token_url)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_login_with_email_and_rate_limit(self):
        f.learner(email="a@b.uz")
        self.assertEqual(self.client.post(reverse("accounts:login"), {"identifier": "a@b.uz", "password": "Strong-pass-123"}).status_code, 302)
        self.client.logout()
        codes = [self.client.post(reverse("accounts:login"), {"identifier": "a@b.uz", "password": "wrong"}).status_code for _ in range(12)]
        self.assertIn(429, codes)

    def test_placement_through_http(self):
        user = f.learner(placement_done=False)
        self.client.force_login(user)
        self.client.post(reverse("assessments:begin"))
        for _ in range(20):
            response = self.client.get(reverse("assessments:question"))
            if response.status_code == 302:
                break
            q = response.context["question"]
            data = {"question": q.pk}
            raw = f.answer_for(q)
            if q.type == "mcq":
                data["option"] = raw
            elif q.type == "gap" or q.type == "contextual":
                data["blank"] = raw
            else:
                data["text"] = raw
            self.client.post(reverse("assessments:answer"), data)
        self.assertRedirects(response, reverse("assessments:result"))
        self.assertContains(self.client.get(reverse("assessments:result")), "Taxminiy daraja")

    def test_study_session_over_htmx(self):
        user = f.learner()
        self.client.force_login(user)
        response = self.client.post(reverse("learning:start"), {"kind": "daily"})
        session = StudySession.objects.get(user=user)
        self.assertRedirects(response, reverse("learning:session", args=[session.pk]))
        page = self.client.get(reverse("learning:session", args=[session.pk]))
        self.assertContains(page, "Tushundim")
        item = session.items.order_by("position").first()
        response = self.client.post(reverse("learning:theory_done", args=[session.pk, item.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Tekshirish")
        item = session.items.get(status=SessionItem.Status.SERVED)
        q = item.question
        data = {"elapsed_ms": "6000"}
        raw = f.answer_for(q)
        if q.type == "mcq":
            data["option"] = raw
        elif q.type in ("gap", "contextual"):
            data["blank"] = raw
        else:
            data["text"] = raw
        response = self.client.post(reverse("learning:answer", args=[session.pk, item.pk]), data, HTTP_HX_REQUEST="true")
        self.assertContains(response, "To'g'ri")
        self.assertContains(response, "Davom etish")
        # report the question that was just answered
        attempt = item.attempt
        response = self.client.post(reverse("questions:report", args=[q.pk]), {"reason": "ambiguous", "comment": "Ikki javob ham to'g'ri", "attempt": attempt.pk}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Rahmat")
        self.assertEqual(QuestionReport.objects.get().status, "open")
        self.client.post(reverse("learning:finish", args=[session.pk]))
        self.assertContains(self.client.get(reverse("learning:summary", args=[session.pk])), "Mavzular")

    def test_learner_cannot_touch_other_sessions_or_report_unseen_questions(self):
        owner, other = f.learner(), f.learner()
        self.client.force_login(owner)
        self.client.post(reverse("learning:start"), {"kind": "daily"})
        session = StudySession.objects.get(user=owner)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("learning:session", args=[session.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("questions:report", args=[Question.objects.first().pk]), {"reason": "other"}).status_code, 403)

    def test_password_reset_link_ignores_host_header(self):
        f.learner(email="reset@example.com")
        self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"}, HTTP_HOST="localhost")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://localhost:8000/password-reset/", mail.outbox[0].body)

    def test_unsafe_back_redirect_is_ignored(self):
        self.client.force_login(f.learner())
        response = self.client.post(reverse("learning:start"), {"kind": "review", "back": "/\\evil.com"})
        self.assertRedirects(response, reverse("learning:dashboard"))

    def test_learner_pages_render(self):
        user = f.learner()
        self.client.force_login(user)
        topic_url = reverse("curriculum:topic", args=["present-perfect"])
        for url in [reverse("learning:dashboard"), reverse("curriculum:map"), topic_url, reverse("analytics:progress"), reverse("accounts:settings")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_studio_requires_superuser(self):
        learner = f.learner()
        self.client.force_login(learner)
        self.assertEqual(self.client.get(reverse("studio:dashboard")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("studio:dashboard")).status_code, 302)

    def test_studio_pages_and_question_editing(self):
        admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123")
        self.client.force_login(admin)
        topic = Question.objects.first().topic
        for name, args in [("dashboard", []), ("users", []), ("curriculum", []), ("questions", []), ("reports", []), ("ai", []), ("settings", []),
                           ("topic_theory", [topic.pk]), ("topic_edit", [topic.pk]), ("question_new", [])]:  # fmt: skip
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"studio:{name}", args=args)).status_code, 200)
        tree = self.client.get(reverse("studio:curriculum")).context["tree"]
        row = next(t for node in tree for u in node["units"] for t in u["topics"] if t.pk == topic.pk)
        self.assertEqual(row.q_live, topic.questions.filter(status="approved", personalized_for=None).count())
        data = {"topic": topic.pk, "type": "gap", "difficulty": 2, "error_tag": "agreement", "prompt": "My cat ___ fish every day.", "explanation": "3-shaxs birlik.",
                "blanks": "eats", "save_as": "approved"}  # fmt: skip
        self.assertEqual(self.client.post(reverse("studio:question_new"), data).status_code, 302)
        created = Question.objects.get(prompt="My cat ___ fish every day.")
        self.assertEqual((created.status, created.payload), ("approved", {"blanks": [["eats"]]}))
        data["prompt"] = "No blanks in this prompt."
        response = self.client.post(reverse("studio:question_new"), data)
        self.assertContains(response, "tekshiruvdan o'tmadi")

    def test_studio_drag_and_drop_reorder(self):
        from apps.curriculum.models import Topic, Unit

        url = reverse("studio:reorder")
        unit_a, unit_b = Unit.objects.filter(level__code="A1").order_by("order")[:2]
        a_topics = list(unit_a.topics.order_by("order").values_list("pk", flat=True))
        b_topics = list(unit_b.topics.order_by("order").values_list("pk", flat=True))
        # learners may not reorder
        self.client.force_login(f.learner())
        self.assertEqual(self.client.post(url, {"kind": "topic", "parent": unit_a.pk, "ids": a_topics[::-1]}).status_code, 403)

        self.client.force_login(User.objects.create_superuser("boss3", "boss3@example.com", "Admin-pass-123"))
        self.assertEqual(self.client.post(url, {"kind": "topic", "parent": unit_a.pk, "ids": a_topics[::-1]}).status_code, 204)
        self.assertEqual(list(unit_a.topics.order_by("order").values_list("pk", flat=True)), a_topics[::-1])
        # move the first topic of unit A to the top of unit B
        moved = a_topics[0]
        self.assertEqual(self.client.post(url, {"kind": "topic", "parent": unit_b.pk, "ids": [moved, *b_topics]}).status_code, 204)
        self.assertEqual(Topic.objects.get(pk=moved).unit_id, unit_b.pk)
        # blocks of another topic and unknown kinds are rejected
        other_block = Topic.objects.exclude(pk=moved).first().blocks.first()
        self.assertEqual(self.client.post(url, {"kind": "block", "parent": moved, "ids": [other_block.pk]}).status_code, 400)
        self.assertEqual(self.client.post(url, {"kind": "level", "parent": 1, "ids": [1]}).status_code, 400)
        blocks = list(Topic.objects.get(pk=moved).blocks.order_by("order").values_list("pk", flat=True))
        self.assertEqual(self.client.post(url, {"kind": "block", "parent": moved, "ids": blocks[::-1]}).status_code, 204)
        self.assertEqual(list(Topic.objects.get(pk=moved).blocks.order_by("order").values_list("pk", flat=True)), blocks[::-1])
        page = self.client.get(reverse("studio:curriculum"))
        self.assertContains(page, 'data-sortable data-kind="topic"')

    def test_studio_theory_block_editor(self):
        admin = User.objects.create_superuser("boss2", "boss2@example.com", "Admin-pass-123")
        self.client.force_login(admin)
        topic = Question.objects.first().topic
        before = topic.blocks.count()
        response = self.client.post(reverse("studio:block_new", args=[topic.pk]), {"kind": "mistake", "items": "He go. || He goes. || -s kerak"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "He goes.")
        self.assertEqual(topic.blocks.count(), before + 1)
        block = topic.blocks.order_by("-order").first()
        self.assertEqual(block.data["items"][0]["why"], "-s kerak")
        response = self.client.post(reverse("studio:block_new", args=[topic.pk]), {"kind": "table", "headers": "A | B", "rows": "only-one-cell"}, HTTP_HX_REQUEST="true")
        self.assertEqual(topic.blocks.count(), before + 1)
        self.assertContains(response, "flash-error")

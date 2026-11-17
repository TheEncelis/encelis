"""Load the A1–C2 curriculum: topics, theory and the reusable question bank.

Idempotent by default: levels/units/topics are matched by code/title/slug and
questions by content hash, so re-running only adds what is missing.

--reset wipes all curriculum content first (questions — including AI-generated
and personalised ones — theory, objectives, units, topics, generation jobs) and,
because learner history refers to that content, also resets every learner's
learning state (attempts, mastery, review schedule, sessions, placement).
Accounts are kept; learners take the placement test again. Back up first:
`manage.py backup_db`.

Every item passes the same validators as admin- and AI-authored content, so
invalid seed data fails loudly and nothing partial is committed.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.curriculum.models import CEFR_CODES, LearningObjective, Level, TheoryBlock, Topic, Unit
from apps.curriculum.seed import CONTENT
from apps.curriculum.services import clean_block_data, set_prerequisites
from apps.questions.models import Question
from apps.questions.services import DuplicateQuestion, QuestionInvalid, save_question


def wipe_content(stdout=None):
    from apps.ai.models import GenerationJob
    from apps.analytics.models import DailyActivity
    from apps.assessments.models import PlacementSession
    from apps.learning.models import Attempt, LearnerProfile, MistakePattern, StudySession, TopicMastery
    from apps.questions.models import AnswerVerdict, QuestionReport
    from apps.reviews.models import ReviewCard

    counts = {
        "attempts": Attempt.objects.all().delete()[0],
        "sessions": StudySession.objects.all().delete()[0],
        "placements": PlacementSession.objects.all().delete()[0],
        "masteries": TopicMastery.objects.all().delete()[0],
        "review_cards": ReviewCard.objects.all().delete()[0],
        "mistake_patterns": MistakePattern.objects.all().delete()[0],
        "reports": QuestionReport.objects.all().delete()[0],
        "verdicts": AnswerVerdict.objects.all().delete()[0],
        "questions": Question.objects.all().delete()[0],
        "generation_jobs": GenerationJob.objects.all().delete()[0],
        "theory_blocks": TheoryBlock.objects.all().delete()[0],
        "topics": Topic.objects.all().delete()[0],
        "units": Unit.objects.all().delete()[0],
    }
    DailyActivity.objects.update(goal_met=False)
    LearnerProfile.objects.update(placement_done=False, ability=0.5, ability_sd=1.5)
    get_user_model()  # accounts themselves are untouched
    return counts


class Command(BaseCommand):
    help = "Seed the curriculum, theory and question bank (use --reset to replace everything)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete all curriculum content and learner learning state first.")
        parser.add_argument("--refresh-theory", action="store_true", help="Replace theory blocks and objectives of seeded topics.")

    @transaction.atomic
    def handle(self, *args, reset=False, refresh_theory=False, **options):
        if reset:
            counts = wipe_content()
            self.stdout.write("Removed: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))

        for order, code in enumerate(CEFR_CODES, 1):
            meta = next(level for level, _ in CONTENT if level["code"] == code)
            Level.objects.update_or_create(code=code, defaults={"title": meta["title"], "description": meta["description"], "order": order})

        topics, pending_links = {}, []
        created_q = skipped_q = 0
        for level_meta, units in CONTENT:
            level = Level.objects.get(code=level_meta["code"])
            for unit_order, unit_data in enumerate(units, 1):
                unit, _ = Unit.objects.get_or_create(level=level, title=unit_data["title"], defaults={"order": unit_order})
                for topic_order, data in enumerate(unit_data["topics"], 1):
                    if data["slug"] in topics:
                        raise CommandError(f"Duplicate topic slug: {data['slug']}")
                    topic, created = Topic.objects.get_or_create(
                        slug=data["slug"],
                        defaults={
                            "unit": unit,
                            "title": data["title"],
                            "title_en": data["title_en"],
                            "summary": data["summary"],
                            "order": topic_order,
                            "difficulty_offset": data["difficulty_offset"],
                            "mastery_threshold": data["mastery_threshold"],
                            "status": Topic.Status.PUBLISHED,
                        },
                    )
                    topics[data["slug"]] = topic
                    if created or refresh_theory or not topic.blocks.exists():
                        topic.blocks.all().delete()
                        for index, block in enumerate(data["blocks"]):
                            try:
                                cleaned = clean_block_data(block["kind"], block["data"])
                            except Exception as exc:
                                raise CommandError(f"Invalid theory block {index} in {data['slug']}: {exc}") from exc
                            TheoryBlock.objects.create(topic=topic, kind=block["kind"], order=index, data=cleaned)
                        topic.objectives.all().delete()
                        LearningObjective.objects.bulk_create([LearningObjective(topic=topic, text=t, order=i) for i, t in enumerate(data["objectives"])])
                    pending_links.append((topic, data))
                    for fields in data["questions"]:
                        try:
                            save_question(topic, fields, source=Question.Source.SEED, status=Question.Status.APPROVED)
                            created_q += 1
                        except DuplicateQuestion:
                            skipped_q += 1
                        except QuestionInvalid as exc:
                            raise CommandError(f"Invalid seed question in {data['slug']}: {fields['prompt'][:60]!r}: {exc}") from exc

        for topic, data in pending_links:
            if topic.prerequisite_links.exists() and not (refresh_theory or reset):
                continue
            missing = [s for s in data["prerequisites"] + data["soft"] + data["related"] if s not in topics]
            if missing:
                raise CommandError(f"{data['slug']} refers to unknown topics: {missing}")
            pairs = [(topics[s].id, "hard") for s in data["prerequisites"]] + [(topics[s].id, "soft") for s in data["soft"]]
            set_prerequisites(topic, pairs)
            if data["related"]:
                topic.related.set([topics[s] for s in data["related"]])

        blocks = TheoryBlock.objects.filter(topic__in=topics.values()).count()
        self.stdout.write(self.style.SUCCESS(f"Curriculum ready: {len(topics)} topics, {blocks} theory blocks, {created_q} new questions ({skipped_q} already present)."))

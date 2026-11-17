from django.core.management.base import BaseCommand, CommandError

from apps.ai.models import GenerationJob
from apps.ai.pipeline import run_job
from apps.curriculum.models import Topic
from apps.questions.models import QType


class Command(BaseCommand):
    help = "Generate, validate and store reusable questions for a topic (pending admin approval by default)."

    def add_arguments(self, parser):
        parser.add_argument("topic", help="Topic slug")
        parser.add_argument("--type", default="gap", choices=QType.values)
        parser.add_argument("--count", type=int, default=6)
        parser.add_argument("--difficulty", type=int, default=3, choices=range(1, 6))

    def handle(self, *args, **options):
        topic = Topic.objects.filter(slug=options["topic"]).first()
        if topic is None:
            raise CommandError("Topic not found.")
        job = GenerationJob.objects.create(
            kind=GenerationJob.Kind.QUESTIONS,
            topic=topic,
            params={"type": options["type"], "count": options["count"], "difficulty": options["difficulty"]},
        )
        job = run_job(job.id)
        if job.status == GenerationJob.Status.FAILED:
            raise CommandError(job.error)
        result = job.result
        self.stdout.write(self.style.SUCCESS(f"Generated {result['generated']}, saved {result['saved']}, rejected {len(result['rejected'])}."))
        for item in result["rejected"]:
            self.stdout.write(f"  - {item['prompt']}: {'; '.join(item['reasons'])}")

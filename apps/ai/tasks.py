import logging
import threading

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from apps.core.models import PlatformSetting

from .models import GenerationJob

log = logging.getLogger("encelis.ai")


@shared_task
def run_generation_job(job_id):
    from .pipeline import run_job

    run_job(job_id)


def _run_in_thread(job_id):
    try:
        run_generation_job.apply(args=[job_id])
    finally:
        connection.close()


def dispatch_job(job):
    """Queue on Celery when a broker exists; otherwise run in a background thread
    so slow model calls never block a request."""
    if settings.TESTING:
        return
    if settings.CELERY_TASK_ALWAYS_EAGER:
        threading.Thread(target=_run_in_thread, args=[job.id], daemon=True).start()
    else:
        run_generation_job.delay(job.id)


def create_job(kind, topic, params, requested_by=None):
    job = GenerationJob.objects.create(kind=kind, topic=topic, params=params, requested_by=requested_by)
    dispatch_job(job)
    return job


def request_personal_questions(user_id, topic_id, focus_error=""):
    """Error-driven generation when a learner is running out of unseen questions on a topic."""
    if not settings.AI_ENABLED or not PlatformSetting.get("ai_dynamic_generation"):
        return None
    key = f"personal-gen:{user_id}:{topic_id}"
    if not cache.add(key, 1, 60 * 60 * 6):
        return None
    from apps.curriculum.models import Topic
    from apps.learning.models import TopicMastery
    from apps.questions.models import QType

    topic = Topic.objects.get(pk=topic_id)
    state = TopicMastery.objects.filter(user_id=user_id, topic_id=topic_id).first()
    stage = state.stage if state else 0
    qtype = QType.GAP if stage <= 3 else QType.TRANSFORM
    params = {"type": qtype, "count": 4, "difficulty": 2 if stage <= 2 else 3 if stage == 3 else 4, "focus_error": focus_error, "user_id": user_id}
    return create_job(GenerationJob.Kind.PERSONAL, topic, params)

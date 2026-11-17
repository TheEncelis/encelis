"""Generation jobs: generate -> validate -> cross-check -> persist (pending or approved)."""
import logging

from django.utils import timezone

from apps.core.models import PlatformSetting
from apps.questions.models import Question
from apps.questions.services import DuplicateQuestion, QuestionInvalid, save_question

from . import services
from .client import AIError
from .models import GenerationJob

log = logging.getLogger("encelis.ai")


def _run_questions(job):
    params = job.params
    topic = job.topic
    personal_user = None
    if job.kind == GenerationJob.Kind.PERSONAL:
        from django.contrib.auth import get_user_model

        personal_user = get_user_model().objects.get(pk=params["user_id"])
    avoid = list(Question.objects.filter(topic=topic, type=params["type"]).order_by("-id").values_list("prompt", flat=True)[:25])
    candidates = services.generate_questions(
        topic, params["type"], params["count"], params["difficulty"], focus_error=params.get("focus_error", ""), avoid=avoid, user=job.requested_by
    )
    # Personal questions are served without admin review, so they must clear every
    # automatic gate; reusable ones wait for approval unless auto-approve is on.
    auto = personal_user is not None or PlatformSetting.get("ai_auto_approve")
    saved, rejected = 0, []
    for fields in candidates[: params["count"]]:
        try:
            ok, reason = services.verify_by_solving(topic, fields, user=job.requested_by)
            if not ok:
                rejected.append({"prompt": fields.get("prompt", "")[:160], "reasons": [reason]})
                continue
            save_question(
                topic,
                fields,
                source=Question.Source.AI,
                status=Question.Status.APPROVED if auto else Question.Status.PENDING,
                created_by=job.requested_by,
                personalized_for=personal_user,
                generation_job=job,
            )
            saved += 1
        except QuestionInvalid as exc:
            rejected.append({"prompt": str(fields.get("prompt", ""))[:160], "reasons": exc.errors})
        except DuplicateQuestion:
            rejected.append({"prompt": str(fields.get("prompt", ""))[:160], "reasons": ["takroriy savol"]})
    return {"generated": len(candidates), "saved": saved, "rejected": rejected, "auto_approved": bool(auto)}


def run_job(job_id):
    job = GenerationJob.objects.select_related("topic__unit__level", "requested_by").get(pk=job_id)
    if job.status != GenerationJob.Status.QUEUED:
        return job
    job.status = GenerationJob.Status.RUNNING
    job.save(update_fields=["status"])
    try:
        if job.kind in (GenerationJob.Kind.QUESTIONS, GenerationJob.Kind.PERSONAL):
            job.result = _run_questions(job)
        elif job.kind == GenerationJob.Kind.THEORY_DRAFT:
            job.result = services.draft_theory(job.topic, user=job.requested_by)
        elif job.kind == GenerationJob.Kind.THEORY_REVIEW:
            job.result = services.review_theory(job.topic, user=job.requested_by).model_dump()
        job.status = GenerationJob.Status.DONE
    except AIError as exc:
        job.status, job.error = GenerationJob.Status.FAILED, str(exc)[:400]
    except Exception as exc:  # a job must always reach a terminal state
        log.exception("Generation job %s crashed", job_id)
        job.status, job.error = GenerationJob.Status.FAILED, f"Ichki xatolik: {exc.__class__.__name__}"
    job.finished_at = timezone.now()
    job.save()
    return job

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Question, QuestionReport
from .validation import QuestionInvalid, content_hash, validate_question


class DuplicateQuestion(Exception):
    pass


def save_question(topic, fields, *, source, status, instance=None, created_by=None, personalized_for=None, generation_job=None, objective=None):
    """Validate and persist a question. Raises QuestionInvalid or DuplicateQuestion."""
    clean = validate_question(
        fields.get("type"),
        fields.get("prompt"),
        fields.get("payload"),
        instruction=fields.get("instruction", ""),
        explanation=fields.get("explanation", ""),
        difficulty=fields.get("difficulty", 3),
        error_tag=fields.get("error_tag") or "structure",
    )
    digest = content_hash(topic.id, clean["type"], clean["prompt"], clean["payload"])
    clash = Question.objects.filter(content_hash=digest)
    if instance:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        raise DuplicateQuestion("Bu savol allaqachon mavjud.")
    question = instance or Question(source=source, created_by=created_by, personalized_for=personalized_for, generation_job=generation_job)
    question.topic = topic
    question.status = status
    question.content_hash = digest
    if objective is not None:
        question.objective = objective
    for key, value in clean.items():
        setattr(question, key, value)
    try:
        with transaction.atomic():
            question.save()
    except IntegrityError as exc:
        raise DuplicateQuestion("Bu savol allaqachon mavjud.") from exc
    if instance:
        # Stored verdicts were judged against the old content.
        question.verdicts.all().delete()
    return question


def set_status(question, status, reviewer):
    question.status = status
    question.reviewed_by = reviewer
    question.reviewed_at = timezone.now()
    question.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])


def file_report(user, question, reason, comment="", attempt=None):
    """One open report per learner per question; repeat reports update the existing one."""
    report = QuestionReport.objects.filter(question=question, user=user, status__in=["open", "reviewing"]).first()
    if report:
        report.reason, report.comment = reason, comment[:1000]
        report.save(update_fields=["reason", "comment", "updated_at"])
        return report
    return QuestionReport.objects.create(question=question, user=user, reason=reason, comment=comment[:1000], attempt=attempt)


def resolve_report(report, status, note, admin):
    report.status = status
    report.resolution_note = note
    if status in (QuestionReport.Status.RESOLVED, QuestionReport.Status.DISMISSED):
        report.resolved_by, report.resolved_at = admin, timezone.now()
    report.save()


__all__ = ["save_question", "set_status", "file_report", "resolve_report", "DuplicateQuestion", "QuestionInvalid"]

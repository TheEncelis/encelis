import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.analytics.services import current_streak, days_inactive, milestones, today_activity
from apps.core.ratelimit import ratelimit
from apps.curriculum.models import Topic
from apps.questions.models import QType, QuestionReport
from apps.reviews.services import due_count

from . import sessions
from .models import Attempt, SessionItem, Stage, StudySession
from .planner import LearnerMap


def _learner_session(request, pk):
    session = get_object_or_404(StudySession, pk=pk, user=request.user)
    return session


@login_required
def dashboard(request):
    user = request.user
    profile = user.profile
    lmap = LearnerMap(user)
    today = today_activity(user)
    goal_seconds = profile.daily_goal_minutes * 60
    weak_ids = lmap.weak()[:4]
    weak = [{"topic": lmap.by_id[t], "state": lmap.states.get(t)} for t in weak_ids]

    levels = []
    for topic in lmap.topics:
        code = topic.unit.level.code
        if not levels or levels[-1]["code"] != code:
            levels.append({"code": code, "total": 0, "mastered": 0, "started": 0})
        levels[-1]["total"] += 1
        stage = lmap.stage(topic.id)
        if stage >= Stage.RETAIN:
            levels[-1]["mastered"] += 1
        elif stage > Stage.NEW:
            levels[-1]["started"] += 1
    for level in levels:
        level["mastered_pct"] = round(level["mastered"] / level["total"] * 100) if level["total"] else 0
        level["started_pct"] = round(level["started"] / level["total"] * 100) if level["total"] else 0

    recommendation = lmap.recommendation()
    due = due_count(user)
    parts = []
    if due:
        parts.append(f"{due} ta takrorlash")
    if recommendation and recommendation["kind"] != "weak":
        parts.append("joriy mavzu" if recommendation["kind"] == "continue" else "yangi mavzu")
    if weak:
        parts.append("zaif tomonlar ustida ishlash")
    composition = ", ".join(parts) or "o'zlashtirilgan mavzularni mustahkamlash"

    context = {
        "profile": profile,
        "recommendation": recommendation,
        "composition": composition,
        "active": [{"topic": lmap.by_id[m.topic_id], "state": m} for m in lmap.active()[:3]],
        "today": today,
        "goal_seconds": goal_seconds,
        "goal_pct": min(100, round(today.active_seconds / goal_seconds * 100)) if goal_seconds else 0,
        "goal_minutes_done": today.active_seconds // 60,
        "due": due,
        "weak": weak,
        "levels": levels,
        "streak": current_streak(profile),
        "away_days": days_inactive(profile),
        "open_session": sessions.active_session(user),
        "milestones": [m for m in milestones(user) if m["done"]][-2:],
    }
    return render(request, "learning/dashboard.html", context)


@login_required
@require_POST
@ratelimit("start-session", limit=30, window=300, per="user")
def start(request):
    kind = request.POST.get("kind", StudySession.Kind.DAILY)
    if kind not in StudySession.Kind.values:
        raise Http404
    topic = None
    if kind == StudySession.Kind.TOPIC:
        topic = get_object_or_404(Topic, slug=request.POST.get("topic"), status=Topic.Status.PUBLISHED, unit__is_published=True)
    existing = sessions.active_session(request.user)
    if existing and request.POST.get("resume") == "1":
        return redirect("learning:session", pk=existing.pk)
    if existing:
        sessions.finalize(existing, abandoned=True)
    try:
        session = sessions.start_session(request.user, kind, topic=topic, with_theory=request.POST.get("theory") == "1")
    except sessions.NothingToStudy:
        texts = {
            StudySession.Kind.REVIEW: "Hozircha takrorlash vaqti kelgan mavzular yo'q. Ajoyib!",
            StudySession.Kind.WEAKNESS: "Zaif tomonlar topilmadi. Mashq qilishda davom eting.",
            StudySession.Kind.TIMED: "Tezkor sinov uchun avval kamida bitta mavzuni mashq bosqichigacha o'rganing.",
        }
        messages.info(request, texts.get(kind, "Hozircha o'rganish uchun material topilmadi."))
        back = request.POST.get("back", "")
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(back)
        return redirect("learning:dashboard")
    return redirect("learning:session", pk=session.pk)


def _item_context(session, item):
    done, total = sessions.progress(session)
    context = {"session": session, "item": item, "done": done, "total": total, "pct": round(done / total * 100) if total else 0}
    if item is None:
        return context
    if item.kind == SessionItem.Kind.THEORY:
        context["blocks"] = item.topic.blocks.all()
        context["objectives"] = item.topic.objectives.all()
        context["remedial"] = item.purpose == SessionItem.Purpose.REMEDIAL
    else:
        question = item.question
        context["question"] = question
        context["timed"] = session.kind == StudySession.Kind.TIMED
        if question.type == QType.CONSTRUCT:
            import random

            tokens = list(question.payload["tokens"])
            random.Random(item.pk).shuffle(tokens)
            context["tokens_json"] = json.dumps(tokens)
        if question.type in (QType.GAP, QType.CONTEXT):
            context["prompt_parts"] = _split_blanks(question.prompt)
    return context


def _split_blanks(prompt):
    import re

    parts = re.split(r"_{3,}", prompt)
    return [{"text": text, "blank": index < len(parts) - 1, "index": index} for index, text in enumerate(parts)]


@login_required
def session_view(request, pk):
    session = _learner_session(request, pk)
    if session.status != StudySession.Status.ACTIVE:
        return redirect("learning:summary", pk=session.pk)
    item = sessions.current_item(session)
    if item is None:
        sessions.finalize(session)
        return redirect("learning:summary", pk=session.pk)
    template = "learning/partials/item.html" if request.htmx else "learning/session.html"
    return render(request, template, _item_context(session, item))


@login_required
@require_POST
def theory_done(request, pk, item_id):
    session = _learner_session(request, pk)
    item = get_object_or_404(SessionItem, pk=item_id, session=session)
    sessions.complete_theory(item)
    return _next(request, session)


def _next(request, session):
    item = sessions.current_item(session)
    if item is None:
        sessions.finalize(session)
        if request.htmx:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("learning:summary", args=[session.pk])
            return response
        return redirect("learning:summary", pk=session.pk)
    if request.htmx:
        return render(request, "learning/partials/item.html", _item_context(session, item))
    return redirect("learning:session", pk=session.pk)


@login_required
@require_POST
@ratelimit("answer", limit=120, window=60, per="user")
def answer(request, pk, item_id):
    session = _learner_session(request, pk)
    if session.status != StudySession.Status.ACTIVE:
        return redirect("learning:summary", pk=session.pk)
    item = get_object_or_404(SessionItem.objects.select_related("question__topic__unit__level", "topic"), pk=item_id, session=session)
    if item.status != SessionItem.Status.SERVED or item.question is None:
        return _next(request, session)
    question = item.question
    if question.type == QType.MCQ:
        raw = request.POST.get("option")
        raw = int(raw) if raw is not None and raw.isdigit() else None
    elif question.type in (QType.GAP, QType.CONTEXT):
        raw = [value[:120] for value in request.POST.getlist("blank")][:6]
    else:
        raw = request.POST.get("text", "")[:600]
    try:
        client_ms = int(request.POST.get("elapsed_ms", "0"))
    except ValueError:
        client_ms = 0
    try:
        outcome = sessions.submit_answer(item, raw, client_ms=client_ms)
    except ValueError:
        return _next(request, session)
    done, total = sessions.progress(session)
    context = {
        "session": session,
        "item": item,
        "question": question,
        "outcome": outcome,
        "evaluation": outcome.evaluation,
        "attempt": outcome.attempt,
        "state": outcome.mastery,
        "done": done,
        "total": total,
        "pct": round(done / total * 100) if total else 0,
        "given": _given_text(question, raw),
        "report_reasons": QuestionReport.Reason.choices,
        "timed": session.kind == StudySession.Kind.TIMED,
    }
    template = "learning/partials/feedback.html"
    if not request.htmx:
        template = "learning/feedback_page.html"
    return render(request, template, context)


def _given_text(question, raw):
    if question.type == QType.MCQ:
        options = question.payload["options"]
        return options[raw]["text"] if isinstance(raw, int) and 0 <= raw < len(options) else "—"
    if isinstance(raw, list):
        return " · ".join(v or "—" for v in raw)
    return raw or "—"


@login_required
@require_POST
def next_item(request, pk):
    session = _learner_session(request, pk)
    if session.status != StudySession.Status.ACTIVE:
        return redirect("learning:summary", pk=session.pk)
    return _next(request, session)


@login_required
@require_POST
def finish(request, pk):
    session = _learner_session(request, pk)
    sessions.finalize(session, abandoned=sessions.progress(session)[0] == 0)
    return redirect("learning:summary", pk=session.pk)


@login_required
def summary(request, pk):
    session = _learner_session(request, pk)
    if session.status == StudySession.Status.ACTIVE:
        return redirect("learning:session", pk=session.pk)
    rows = sessions.summary(session)
    accuracy = round(session.correct / session.answered * 100) if session.answered else 0
    mistakes = Attempt.objects.filter(session=session, is_correct=False, counted=True).select_related("question", "topic")[:8]
    return render(
        request,
        "learning/summary.html",
        {"session": session, "rows": rows, "accuracy": accuracy, "mistakes": mistakes, "minutes": max(1, round(session.active_seconds / 60))},
    )


@login_required
@require_POST
@ratelimit("explain", limit=20, window=3600, per="user")
def explain(request, attempt_id):
    """Personalised AI explanation of a mistake (HTMX)."""
    from django.conf import settings

    attempt = get_object_or_404(Attempt.objects.select_related("question__topic__unit__level", "user"), pk=attempt_id, user=request.user)
    if attempt.is_correct:
        raise Http404
    cached = attempt.feedback.get("ai_explanation")
    if cached is None and settings.AI_ENABLED:
        from apps.ai.services import explain_mistake

        result = explain_mistake(attempt)
        if result is not None:
            cached = result.model_dump()
            attempt.feedback = {**attempt.feedback, "ai_explanation": cached}
            Attempt.objects.filter(pk=attempt.pk).update(feedback=attempt.feedback)
    return render(request, "learning/partials/explanation.html", {"explanation": cached})

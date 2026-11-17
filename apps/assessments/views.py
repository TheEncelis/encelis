from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.learning.models import cefr_for
from apps.learning.planner import LearnerMap
from apps.questions.models import QType

from . import services
from .models import PlacementSession


@login_required
def start(request):
    if request.user.is_staff:
        return redirect("studio:dashboard")
    if request.user.profile.placement_done:
        return redirect("learning:dashboard")
    active = PlacementSession.objects.filter(user=request.user, status=PlacementSession.Status.ACTIVE).first()
    return render(request, "assessments/start.html", {"active": active})


@login_required
@require_POST
def begin(request):
    if request.user.profile.placement_done:
        return redirect("learning:dashboard")
    services.start(request.user)
    return redirect("assessments:question")


@login_required
def question(request):
    if request.user.profile.placement_done:
        return redirect("learning:dashboard")
    session = PlacementSession.objects.filter(user=request.user, status=PlacementSession.Status.ACTIVE).first()
    if session is None:
        return redirect("assessments:start")
    q = services.next_question(session)
    if q is None:
        services.finish(session)
        return redirect("assessments:result")
    count = session.responses.count()
    context = {"session": session, "question": q, "number": count + 1, "max": services.MAX_ITEMS, "pct": round(count / services.MAX_ITEMS * 100)}
    if q.type in (QType.GAP, QType.CONTEXT):
        from apps.learning.views import _split_blanks

        context["prompt_parts"] = _split_blanks(q.prompt)
    if q.type == QType.CONSTRUCT:
        import json
        import random

        tokens = list(q.payload["tokens"])
        random.Random(session.pk * 1000 + count).shuffle(tokens)
        context["tokens_json"] = json.dumps(tokens)
    return render(request, "assessments/question.html", context)


@login_required
@require_POST
def answer(request):
    session = PlacementSession.objects.filter(user=request.user, status=PlacementSession.Status.ACTIVE).first()
    if session is None or session.current_question is None:
        return redirect("assessments:question")
    q = session.current_question
    if str(q.pk) != request.POST.get("question"):
        return redirect("assessments:question")
    if q.type == QType.MCQ:
        raw = request.POST.get("option")
        raw = int(raw) if raw and raw.isdigit() else None
    elif q.type in (QType.GAP, QType.CONTEXT):
        raw = [v[:120] for v in request.POST.getlist("blank")][:6]
    else:
        raw = request.POST.get("text", "")[:600]
    if request.POST.get("dont_know") == "1":
        raw = None if q.type == QType.MCQ else ""
    services.answer(session, raw)
    return redirect("assessments:question")


@login_required
@require_POST
def skip(request):
    if not request.user.profile.placement_done:
        services.skip(request.user)
    return redirect("learning:dashboard")


@login_required
def result(request):
    session = PlacementSession.objects.filter(user=request.user).exclude(status=PlacementSession.Status.ACTIVE).order_by("-started_at").first()
    if session is None:
        return redirect("assessments:start")
    profile = request.user.profile
    responses = list(session.responses.select_related("topic__unit__level"))
    strong = sorted({r.topic for r in responses if r.is_correct}, key=lambda t: t.difficulty, reverse=True)[:4]
    weak = sorted({r.topic for r in responses if not r.is_correct}, key=lambda t: t.difficulty)[:4]
    lmap = LearnerMap(request.user)
    return render(
        request,
        "assessments/result.html",
        {
            "session": session,
            "profile": profile,
            "cefr": cefr_for(profile.ability),
            "within": round((profile.ability % 1) * 100),
            "strong": strong,
            "weak": weak,
            "answered": len(responses),
            "correct": sum(1 for r in responses if r.is_correct),
            "credited": sum(1 for m in lmap.states.values() if m.assumed),
            "recommendation": lmap.recommendation(),
        },
    )

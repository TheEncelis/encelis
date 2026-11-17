"""Admin Studio — a focused content and learner-insight workspace for the single administrator."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ai.models import GenerationJob
from apps.ai.tasks import create_job
from apps.analytics.models import DailyActivity
from apps.analytics.services import platform_overview
from apps.core.models import SETTING_DEFINITIONS, PlatformSetting
from apps.curriculum import services as curriculum
from apps.curriculum.models import LearningObjective, Level, TheoryBlock, Topic, Unit
from apps.learning.models import Attempt, MistakePattern, StudySession, TopicMastery
from apps.questions.models import QType, Question, QuestionReport
from apps.questions.services import DuplicateQuestion, QuestionInvalid, resolve_report, save_question, set_status

from . import blocks as block_fields
from .forms import GenerateForm, QuestionForm, TopicForm, UnitForm

User = get_user_model()


def admin_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), "accounts:login")
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def _counts():
    return {
        "pending_questions": Question.objects.filter(status=Question.Status.PENDING).count(),
        "open_reports": QuestionReport.objects.filter(status__in=["open", "reviewing"]).count(),
    }


def _render(request, template, context=None):
    context = {**(context or {}), "studio_counts": _counts()}
    return render(request, template, context)


# --- Dashboard -----------------------------------------------------------------
@admin_only
def dashboard(request):
    context = platform_overview()
    context["jobs"] = GenerationJob.objects.select_related("topic")[:5]
    context["reports"] = QuestionReport.objects.filter(status="open").select_related("question__topic", "user")[:5]
    return _render(request, "studio/dashboard.html", context)


# --- Learners ------------------------------------------------------------------
@admin_only
def users(request):
    q = request.GET.get("q", "").strip()
    qs = User.objects.filter(is_staff=False).select_related("learner_profile").annotate(answers=Count("attempts"), last=Max("attempts__created_at"))
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(username__icontains=q))
    page = Paginator(qs.order_by("-date_joined"), 30).get_page(request.GET.get("page"))
    return _render(request, "studio/users.html", {"page": page, "q": q})


@admin_only
def user_detail(request, pk):
    learner = get_object_or_404(User.objects.select_related("learner_profile"), pk=pk, is_staff=False)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "toggle_active":
            learner.is_active = not learner.is_active
            learner.save(update_fields=["is_active"])
            messages.success(request, "Hisob holati o'zgartirildi.")
        elif action == "redo_placement":
            profile = learner.profile
            profile.placement_done = False
            profile.save(update_fields=["placement_done"])
            messages.success(request, "O'quvchi keyingi kirishda daraja testini qayta topshiradi.")
        return redirect("studio:user", pk=pk)
    masteries = TopicMastery.objects.filter(user=learner).select_related("topic__unit__level").order_by("topic__unit__level__order", "topic__unit__order", "topic__order")
    return _render(
        request,
        "studio/user_detail.html",
        {
            "learner": learner,
            "profile": learner.profile,
            "masteries": masteries,
            "attempts": Attempt.objects.filter(user=learner).select_related("question", "topic").order_by("-created_at")[:25],
            "mistakes": MistakePattern.objects.filter(user=learner).select_related("topic").order_by("-count")[:10],
            "sessions": StudySession.objects.filter(user=learner).order_by("-created_at")[:10],
            "activity": DailyActivity.objects.filter(user=learner)[:14],
            "placements": learner.placements.order_by("-started_at")[:3],
        },
    )


# --- Curriculum ----------------------------------------------------------------
@admin_only
def curriculum_view(request):
    levels = Level.objects.prefetch_related("units").order_by("order")
    topics = Topic.objects.annotate(
        q_live=Count("questions", filter=Q(questions__status="approved", questions__personalized_for=None), distinct=True),
        q_pending=Count("questions", filter=Q(questions__status="pending"), distinct=True),
        n_blocks=Count("blocks", distinct=True),
    ).order_by("order", "id")
    by_unit = {}
    for topic in topics:
        by_unit.setdefault(topic.unit_id, []).append(topic)
    tree = [{"level": level, "units": [{"unit": u, "topics": by_unit.get(u.id, [])} for u in level.units.order_by("order", "id")]} for level in levels]
    return _render(request, "studio/curriculum.html", {"tree": tree})


@admin_only
def unit_form(request, pk=None):
    unit = get_object_or_404(Unit, pk=pk) if pk else None
    form = UnitForm(request.POST or None, instance=unit, initial={"level": request.GET.get("level")})
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if not unit:
            obj.order = (Unit.objects.filter(level=obj.level).aggregate(m=Max("order"))["m"] or 0) + 1
        obj.save()
        messages.success(request, "Bo'lim saqlandi.")
        return redirect("studio:curriculum")
    return _render(request, "studio/unit_form.html", {"form": form, "unit": unit})


@admin_only
@require_POST
def unit_toggle(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    unit.is_published = not unit.is_published
    unit.save(update_fields=["is_published", "updated_at"])
    return redirect("studio:curriculum")


@admin_only
def topic_form(request, pk=None):
    topic = get_object_or_404(Topic, pk=pk) if pk else None
    form = TopicForm(request.POST or None, instance=topic, initial={"unit": request.GET.get("unit")})
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                if not topic:
                    obj.order = (Topic.objects.filter(unit=obj.unit).aggregate(m=Max("order"))["m"] or 0) + 1
                obj.save()
                form.save_m2m()
                pairs = [(t.id, "hard") for t in form.cleaned_data["hard_prerequisites"]] + [(t.id, "soft") for t in form.cleaned_data["soft_prerequisites"]]
                curriculum.set_prerequisites(obj, pairs)
                obj.objectives.all().delete()
                LearningObjective.objects.bulk_create([LearningObjective(topic=obj, text=text, order=i) for i, text in enumerate(form.objectives())])
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, "Mavzu saqlandi.")
            return redirect("studio:topic_theory", pk=obj.pk)
    return _render(request, "studio/topic_form.html", {"form": form, "topic": topic})


@admin_only
@require_POST
def topic_status(request, pk):
    topic = get_object_or_404(Topic, pk=pk)
    status = request.POST.get("status")
    if status in Topic.Status.values:
        if status == Topic.Status.PUBLISHED and not topic.questions.filter(status="approved").exists():
            messages.error(request, "E'lon qilishdan oldin mavzuda kamida bitta tasdiqlangan savol bo'lishi kerak.")
        else:
            topic.status = status
            topic.save(update_fields=["status", "updated_at"])
            messages.success(request, f"“{topic.title}” — {topic.get_status_display().lower()}.")
    return redirect(request.POST.get("back") if request.POST.get("back", "").startswith("/studio/") else "studio:curriculum")


@admin_only
@require_POST
def reorder(request):
    """Drag-and-drop ordering for units, topics and theory blocks."""
    try:
        curriculum.reorder(request.POST.get("kind"), int(request.POST.get("parent", 0)), request.POST.getlist("ids"))
    except (ValueError, ValidationError, Unit.DoesNotExist, Level.DoesNotExist, Topic.DoesNotExist):
        return HttpResponse("Tartib saqlanmadi.", status=400)
    return HttpResponse(status=204)


# --- Theory editor -------------------------------------------------------------
@admin_only
def topic_theory(request, pk):
    topic = get_object_or_404(Topic.objects.select_related("unit__level"), pk=pk)
    drafts = GenerationJob.objects.filter(topic=topic, kind__in=["theory_draft", "theory_review"]).order_by("-created_at")[:4]
    return _render(
        request,
        "studio/theory.html",
        {"topic": topic, "blocks": topic.blocks.all(), "kinds": TheoryBlock.Kind.choices, "drafts": drafts, "objectives": topic.objectives.all()},
    )


def _block_editor(request, topic, block=None, kind=None, errors=None, values=None):
    kind = block.kind if block else kind
    return render(
        request,
        "studio/partials/block_form.html",
        {"topic": topic, "block": block, "kind": kind, "kind_label": dict(TheoryBlock.Kind.choices).get(kind), "v": values or (block_fields.fields_for(kind, block.data) if block else {}), "errors": errors},
    )


@admin_only
def block_new(request, pk):
    topic = get_object_or_404(Topic, pk=pk)
    kind = request.GET.get("kind") or request.POST.get("kind")
    if kind not in TheoryBlock.Kind.values:
        return HttpResponse(status=400)
    if request.method == "POST":
        try:
            data = curriculum.clean_block_data(kind, block_fields.data_from_post(kind, request.POST))
        except ValidationError as exc:
            return _block_editor(request, topic, kind=kind, errors=exc.messages, values=request.POST)
        order = (topic.blocks.aggregate(m=Max("order"))["m"] or 0) + 1
        block = TheoryBlock.objects.create(topic=topic, kind=kind, data=data, order=order)
        return render(request, "studio/partials/block_view.html", {"block": block, "topic": topic, "fresh": True})
    return _block_editor(request, topic, kind=kind)


@admin_only
def block_edit(request, pk, block_id):
    topic = get_object_or_404(Topic, pk=pk)
    block = get_object_or_404(TheoryBlock, pk=block_id, topic=topic)
    if request.method == "POST":
        try:
            block.data = curriculum.clean_block_data(block.kind, block_fields.data_from_post(block.kind, request.POST))
        except ValidationError as exc:
            return _block_editor(request, topic, block, errors=exc.messages, values=request.POST)
        block.save()
        return render(request, "studio/partials/block_view.html", {"block": block, "topic": topic})
    if request.GET.get("view"):
        return render(request, "studio/partials/block_view.html", {"block": block, "topic": topic})
    return _block_editor(request, topic, block)


@admin_only
@require_POST
def block_delete(request, pk, block_id):
    get_object_or_404(TheoryBlock, pk=block_id, topic_id=pk).delete()
    return HttpResponse("")


@admin_only
@require_POST
def theory_apply(request, pk, job_id):
    """Apply a validated AI theory draft: replace or append blocks."""
    topic = get_object_or_404(Topic, pk=pk)
    job = get_object_or_404(GenerationJob, pk=job_id, topic=topic, kind="theory_draft", status="done")
    mode = request.POST.get("mode", "append")
    draft = job.result
    with transaction.atomic():
        if mode == "replace":
            topic.blocks.all().delete()
            if draft.get("summary"):
                topic.summary = draft["summary"]
                topic.save(update_fields=["summary", "updated_at"])
            if draft.get("objectives"):
                topic.objectives.all().delete()
                LearningObjective.objects.bulk_create([LearningObjective(topic=topic, text=t, order=i) for i, t in enumerate(draft["objectives"])])
        start = (topic.blocks.aggregate(m=Max("order"))["m"] or 0) + 1
        created = 0
        for offset, item in enumerate(draft.get("blocks", [])):
            try:
                data = curriculum.clean_block_data(item["kind"], item["data"])
            except (ValidationError, KeyError):
                continue
            TheoryBlock.objects.create(topic=topic, kind=item["kind"], data=data, order=start + offset)
            created += 1
    messages.success(request, f"Qoralamadan {created} ta blok qo'shildi. Ularni ko'rib chiqing va tahrirlang.")
    return redirect("studio:topic_theory", pk=pk)


# --- Questions -----------------------------------------------------------------
@admin_only
def questions(request):
    qs = Question.objects.select_related("topic__unit__level").annotate(open_reports=Count("reports", filter=Q(reports__status__in=["open", "reviewing"])))
    f = {k: request.GET.get(k, "") for k in ("status", "type", "topic", "source", "q", "personal")}
    if f["status"]:
        qs = qs.filter(status=f["status"])
    if f["type"]:
        qs = qs.filter(type=f["type"])
    if f["topic"]:
        qs = qs.filter(topic_id=f["topic"])
    if f["source"]:
        qs = qs.filter(source=f["source"])
    if f["q"]:
        qs = qs.filter(Q(prompt__icontains=f["q"]) | Q(explanation__icontains=f["q"]))
    if f["personal"] != "1":
        qs = qs.filter(personalized_for__isnull=True)
    page = Paginator(qs.order_by("-created_at"), 40).get_page(request.GET.get("page"))
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    return _render(
        request,
        "studio/questions.html",
        {
            "page": page,
            "f": f,
            "query": query,
            "statuses": Question.Status.choices,
            "types": QType.choices,
            "sources": Question.Source.choices,
            "topics": Topic.objects.select_related("unit__level").order_by("unit__level__order", "unit__order", "order"),
        },
    )


@admin_only
def question_form(request, pk=None):
    question = get_object_or_404(Question, pk=pk) if pk else None
    initial = QuestionForm.initial_for(question) if question else {"topic": request.GET.get("topic"), "type": request.GET.get("type", "mcq")}
    form = QuestionForm(request.POST or None, initial=initial)
    errors = []
    if request.method == "POST" and form.is_valid():
        status = request.POST.get("save_as", Question.Status.APPROVED)
        if status not in (Question.Status.APPROVED, Question.Status.DRAFT, Question.Status.PENDING):
            status = Question.Status.DRAFT
        try:
            saved = save_question(
                form.cleaned_data["topic"],
                form.to_fields(),
                source=question.source if question else Question.Source.ADMIN,
                status=status,
                instance=question,
                created_by=request.user,
            )
        except QuestionInvalid as exc:
            errors = exc.errors
        except DuplicateQuestion as exc:
            errors = [str(exc)]
        else:
            if status == Question.Status.APPROVED:
                set_status(saved, status, request.user)
            messages.success(request, "Savol saqlandi.")
            return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/studio/") else "studio:questions")
    stats = None
    if question:
        attempts = Attempt.objects.filter(question=question, counted=True)
        stats = {"n": attempts.count(), "correct": attempts.filter(is_correct=True).count(), "ai": attempts.filter(evaluated_by="ai").count()}
    return _render(
        request,
        "studio/question_form.html",
        {
            "form": form,
            "question": question,
            "errors": errors,
            "stats": stats,
            "verdicts": question.verdicts.order_by("-created_at")[:12] if question else [],
            "reports": question.reports.select_related("user")[:10] if question else [],
            "next": request.GET.get("next", ""),
        },
    )


@admin_only
@require_POST
def question_status(request, pk):
    question = get_object_or_404(Question, pk=pk)
    status = request.POST.get("status")
    if status in Question.Status.values:
        set_status(question, status, request.user)
    if request.htmx:
        return render(request, "studio/partials/question_row.html", {"q": question})
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/studio/") else "studio:questions")


@admin_only
@require_POST
def questions_bulk(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    status = request.POST.get("status")
    if ids and status in (Question.Status.APPROVED, Question.Status.REJECTED, Question.Status.INACTIVE):
        Question.objects.filter(pk__in=ids).update(status=status, reviewed_by=request.user, reviewed_at=timezone.now())
        messages.success(request, f"{len(ids)} ta savol yangilandi.")
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/studio/") else "studio:questions")


@admin_only
@require_POST
def verdict_override(request, pk, verdict_id):
    """Admin decides an answer verdict; the decision becomes authoritative for future answers."""
    question = get_object_or_404(Question, pk=pk)
    verdict = get_object_or_404(question.verdicts, pk=verdict_id)
    if request.POST.get("decision") == "accept":
        verdict.is_correct, verdict.score, verdict.error_tag = True, 1.0, ""
        if request.POST.get("add") == "1" and "answers" in question.payload:
            question.payload["answers"].append(verdict.normalized)
            question.save(update_fields=["payload", "updated_at"])
    else:
        verdict.is_correct, verdict.score = False, 0.0
    verdict.source = "admin"
    verdict.save()
    messages.success(request, "Javob bahosi yangilandi.")
    return redirect("studio:question_edit", pk=pk)


# --- Reports -------------------------------------------------------------------
@admin_only
def reports(request):
    status = request.GET.get("status", "active")
    qs = QuestionReport.objects.select_related("question__topic__unit__level", "user", "attempt")
    qs = qs.filter(status__in=["open", "reviewing"]) if status == "active" else qs.filter(status=status)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return _render(request, "studio/reports.html", {"page": page, "status": status, "statuses": QuestionReport.Status.choices})


@admin_only
def report_detail(request, pk):
    report = get_object_or_404(QuestionReport.objects.select_related("question__topic", "user", "attempt"), pk=pk)
    if request.method == "POST":
        status = request.POST.get("status")
        if status in QuestionReport.Status.values:
            resolve_report(report, status, request.POST.get("note", "").strip()[:1000], request.user)
            if request.POST.get("deactivate") == "1":
                set_status(report.question, Question.Status.INACTIVE, request.user)
            messages.success(request, "Xabar yangilandi.")
            return redirect("studio:reports")
    others = QuestionReport.objects.filter(question=report.question).exclude(pk=report.pk).select_related("user")[:10]
    return _render(request, "studio/report_detail.html", {"report": report, "others": others, "statuses": QuestionReport.Status.choices})


# --- AI ------------------------------------------------------------------------
@admin_only
def ai_studio(request):
    form = GenerateForm(request.POST or None, initial={"topic": request.GET.get("topic")})
    if request.method == "POST":
        action = request.POST.get("action", "questions")
        if action == "questions" and form.is_valid():
            d = form.cleaned_data
            job = create_job(GenerationJob.Kind.QUESTIONS, d["topic"], {"type": d["type"], "count": d["count"], "difficulty": d["difficulty"]}, request.user)
            return redirect("studio:job", pk=job.pk)
        if action in ("theory_draft", "theory_review"):
            topic = get_object_or_404(Topic, pk=request.POST.get("topic"))
            job = create_job(action, topic, {}, request.user)
            return redirect("studio:job", pk=job.pk)
    from django.conf import settings

    return _render(
        request,
        "studio/ai.html",
        {"form": form, "jobs": GenerationJob.objects.select_related("topic", "requested_by").exclude(kind="personal")[:20], "ai_enabled": settings.AI_ENABLED},
    )


@admin_only
def job_detail(request, pk):
    job = get_object_or_404(GenerationJob.objects.select_related("topic"), pk=pk)
    context = {"job": job, "questions": job.questions.all() if job.is_finished else []}
    template = "studio/partials/job_body.html" if request.htmx else "studio/job.html"
    return _render(request, template, context)


# --- Settings ------------------------------------------------------------------
@admin_only
def settings_view(request):
    if request.method == "POST":
        for key, (default, _, _) in SETTING_DEFINITIONS.items():
            PlatformSetting.put(key, request.POST.get(key) == "on")
        messages.success(request, "Sozlamalar saqlandi.")
        return redirect("studio:settings")
    from django.conf import settings as dj

    items = [{"key": key, "label": label, "help": help_text, "value": PlatformSetting.get(key)} for key, (_, label, help_text) in SETTING_DEFINITIONS.items()]
    env = {"ai": bool(dj.DEEPSEEK_API_KEY), "email": bool(dj.RESEND_API_KEY), "redis": bool(dj.REDIS_URL), "db": dj.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]}
    return _render(request, "studio/settings.html", {"items": items, "env": env})

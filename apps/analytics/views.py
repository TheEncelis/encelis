from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from apps.learning.models import Attempt, MistakePattern, Stage
from apps.learning.planner import LearnerMap
from apps.questions.models import ErrorTag, QType
from apps.reviews.models import ReviewCard

from .services import current_streak, milestones, recent_days


@login_required
def progress(request):
    user = request.user
    profile = user.profile
    lmap = LearnerMap(user)
    days = recent_days(user, 28)
    goal = profile.daily_goal_minutes * 60
    peak = max([d.active_seconds for d in days] + [goal]) or 1
    chart = [{"day": d, "height": round(d.active_seconds / peak * 100), "goal_met": d.goal_met} for d in days]

    topics = []
    for topic in lmap.topics:
        state = lmap.states.get(topic.id)
        if state and state.stage > Stage.NEW:
            topics.append({"topic": topic, "state": state})
    stage_counts = {s: 0 for s in Stage if s != Stage.NEW}
    for row in topics:
        stage_counts[Stage(row["state"].stage)] += 1

    mistakes = (
        MistakePattern.objects.filter(user=user, topic_id__in=lmap.by_id).values("error_tag").annotate(total=Sum("count")).order_by("-total")[:6]
    )
    labels = dict(ErrorTag.choices)
    top = max([m["total"] for m in mistakes] + [1])
    mistake_rows = [{"label": labels.get(m["error_tag"], m["error_tag"]), "total": m["total"], "width": round(m["total"] / top * 100)} for m in mistakes]

    type_rows = []
    attempts = Attempt.objects.filter(user=user, counted=True).exclude(context="placement")
    for qtype, label in QType.choices:
        qs = attempts.filter(q_type=qtype)
        n = qs.count()
        if n:
            type_rows.append({"label": label, "n": n, "acc": round(qs.filter(is_correct=True).count() / n * 100)})
    upcoming = ReviewCard.objects.filter(user=user, due__gt=timezone.now(), topic_id__in=lmap.by_id).select_related("topic").order_by("due")[:6]
    return render(
        request,
        "analytics/progress.html",
        {
            "profile": profile,
            "chart": chart,
            "goal_height": round(goal / peak * 100),
            "topics": topics,
            "stage_counts": stage_counts.items(),
            "mistakes": mistake_rows,
            "types": type_rows,
            "upcoming": upcoming,
            "streak": current_streak(profile),
            "milestones": milestones(user),
            "total_minutes": sum(d.active_seconds for d in days) // 60,
            "ability_pct": round(profile.ability / 6 * 100),
        },
    )

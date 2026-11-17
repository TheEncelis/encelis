from datetime import timedelta

from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from .models import DailyActivity

STREAK_MIN_ANSWERS = 5


def log_activity(user, *, seconds=0, answered=0, correct=0, xp=0, now=None):
    """Accumulate today's activity; maintains goal state, XP and the streak."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    profile = user.profile
    day, _ = DailyActivity.objects.get_or_create(user=user, date=today)
    day.active_seconds += seconds
    day.answered += answered
    day.correct += correct
    day.xp += xp
    day.goal_met = day.active_seconds >= profile.daily_goal_minutes * 60
    day.save()

    changed = []
    if xp:
        profile.xp += xp
        changed.append("xp")
    if day.answered >= STREAK_MIN_ANSWERS and profile.last_active_date != today:
        yesterday = today - timedelta(days=1)
        profile.streak_current = profile.streak_current + 1 if profile.last_active_date == yesterday else 1
        profile.streak_longest = max(profile.streak_longest, profile.streak_current)
        profile.last_active_date = today
        changed += ["streak_current", "streak_longest", "last_active_date"]
    if changed:
        profile.save(update_fields=changed + ["updated_at"])
    return day


def today_activity(user, now=None):
    today = timezone.localtime(now or timezone.now()).date()
    return DailyActivity.objects.filter(user=user, date=today).first() or DailyActivity(user=user, date=today)


def current_streak(profile, now=None):
    """Streak as it stands today (0 once a day has been missed)."""
    today = timezone.localtime(now or timezone.now()).date()
    if profile.last_active_date and (today - profile.last_active_date).days <= 1:
        return profile.streak_current
    return 0


def days_inactive(profile, now=None):
    if not profile.last_active_date:
        return 0
    return (timezone.localtime(now or timezone.now()).date() - profile.last_active_date).days


def recent_days(user, days=14, now=None):
    today = timezone.localtime(now or timezone.now()).date()
    rows = {a.date: a for a in DailyActivity.objects.filter(user=user, date__gt=today - timedelta(days=days))}
    return [rows.get(today - timedelta(days=offset)) or DailyActivity(user=user, date=today - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]


def milestones(user):
    """Quiet, mastery-centred milestones (secondary to actual progress)."""
    from apps.learning.models import Attempt, Stage, TopicMastery

    profile = user.profile
    mastered = TopicMastery.objects.filter(user=user, stage=Stage.RETAIN).count()
    answered = Attempt.objects.filter(user=user).exclude(context="placement").count()
    items = [
        ("Birinchi o'zlashtirilgan mavzu", mastered >= 1),
        ("5 ta mavzu o'zlashtirildi", mastered >= 5),
        ("15 ta mavzu o'zlashtirildi", mastered >= 15),
        ("100 ta savolga javob", answered >= 100),
        ("500 ta savolga javob", answered >= 500),
        ("7 kunlik izchillik", profile.streak_longest >= 7),
        ("30 kunlik izchillik", profile.streak_longest >= 30),
    ]
    return [{"title": title, "done": done} for title, done in items]


# --- Admin Studio analytics ---------------------------------------------------
def platform_overview(now=None):
    from django.contrib.auth import get_user_model

    from apps.ai.models import AIUsageLog
    from apps.learning.models import Attempt, Stage, TopicMastery
    from apps.questions.models import Question, QuestionReport

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    week = today - timedelta(days=6)
    User = get_user_model()
    learners = User.objects.filter(is_staff=False)
    days = {
        row["date"]: row
        for row in DailyActivity.objects.filter(date__gte=today - timedelta(days=13))
        .values("date")
        .annotate(users=Count("user", distinct=True), answered=Sum("answered"), correct=Sum("correct"), seconds=Sum("active_seconds"))
    }
    series = []
    for offset in range(13, -1, -1):
        date = today - timedelta(days=offset)
        row = days.get(date, {})
        series.append({"date": date, "users": row.get("users", 0), "answered": row.get("answered") or 0, "correct": row.get("correct") or 0})
    peak = max((d["answered"] for d in series), default=0) or 1
    for d in series:
        d["height"] = round(d["answered"] / peak * 100)

    week_attempts = Attempt.objects.filter(created_at__date__gte=week, counted=True).exclude(context="placement")
    totals = week_attempts.aggregate(n=Count("id"), acc=Avg("score"))
    hardest = (
        TopicMastery.objects.filter(attempts__gte=4, topic__status="published")
        .values("topic_id", "topic__title", "topic__unit__level__code")
        .annotate(learners=Count("id"), avg=Avg("score"), weak=Count("id", filter=Q(is_weak=True)))
        .filter(learners__gte=1)
        .order_by("avg")[:6]
    )
    levels = (
        learners.filter(learner_profile__placement_done=True)
        .values("learner_profile__ability")
        .values_list("learner_profile__ability", flat=True)
    )
    from apps.learning.models import CEFR, cefr_for

    distribution = {code: 0 for code in CEFR}
    for ability in levels:
        distribution[cefr_for(ability)] += 1
    top = max(distribution.values(), default=0) or 1
    ai_week = AIUsageLog.objects.filter(created_at__date__gte=week).aggregate(
        calls=Count("id"), failed=Count("id", filter=Q(success=False)), tokens=Sum(F("prompt_tokens") + F("completion_tokens"))
    )
    return {
        "learners_total": learners.count(),
        "learners_active_week": DailyActivity.objects.filter(date__gte=week, answered__gt=0).values("user").distinct().count(),
        "learners_new_week": learners.filter(date_joined__date__gte=week).count(),
        "answers_week": totals["n"] or 0,
        "accuracy_week": round((totals["acc"] or 0) * 100),
        "mastered_total": TopicMastery.objects.filter(stage=Stage.RETAIN).count(),
        "series": series,
        "hardest": list(hardest),
        "distribution": [{"code": c, "count": n, "width": round(n / top * 100)} for c, n in distribution.items()],
        "questions_live": Question.objects.filter(status="approved", personalized_for=None).count(),
        "questions_pending": Question.objects.filter(status="pending").count(),
        "reports_open": QuestionReport.objects.filter(status__in=["open", "reviewing"]).count(),
        "ai": {"calls": ai_week["calls"] or 0, "failed": ai_week["failed"] or 0, "tokens": ai_week["tokens"] or 0},
    }

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from apps.learning.models import MistakePattern, Stage
from apps.learning.planner import LearnerMap
from apps.reviews.models import ReviewCard

from .models import Topic, TopicPrerequisite


@login_required
def curriculum_map(request):
    lmap = LearnerMap(request.user)
    levels = []
    for topic in lmap.topics:
        level, unit = topic.unit.level, topic.unit
        if not levels or levels[-1]["level"].id != level.id:
            levels.append({"level": level, "units": [], "total": 0, "mastered": 0})
        group = levels[-1]
        if not group["units"] or group["units"][-1]["unit"].id != unit.id:
            group["units"].append({"unit": unit, "topics": []})
        state = lmap.states.get(topic.id)
        unmet = lmap.unmet_prerequisites(topic.id)
        group["units"][-1]["topics"].append({"topic": topic, "state": state, "locked": bool(unmet) and (state is None or state.stage == Stage.NEW)})
        group["total"] += 1
        group["mastered"] += 1 if state and state.stage == Stage.RETAIN else 0
    current = request.user.profile.cefr
    return render(request, "curriculum/map.html", {"levels": levels, "current": current})


@login_required
def topic_detail(request, slug):
    topic = get_object_or_404(Topic.objects.select_related("unit__level"), slug=slug, status=Topic.Status.PUBLISHED, unit__is_published=True)
    lmap = LearnerMap(request.user)
    state = lmap.states.get(topic.id)
    prereq_links = TopicPrerequisite.objects.filter(topic=topic, prerequisite__status=Topic.Status.PUBLISHED).select_related("prerequisite")
    prerequisites = [{"topic": link.prerequisite, "strength": link.strength, "state": lmap.states.get(link.prerequisite_id)} for link in prereq_links]
    unmet = lmap.unmet_prerequisites(topic.id)
    mistakes = MistakePattern.objects.filter(user=request.user, topic=topic).order_by("-count")[:4]
    from apps.learning.mastery import mastery_gaps, compute_signals
    from apps.learning.engine import _evidence

    gaps = mastery_gaps(compute_signals(_evidence(state)), topic.mastery_threshold) if state and state.stage == Stage.MASTER else []
    return render(
        request,
        "curriculum/topic.html",
        {
            "topic": topic,
            "state": state,
            "blocks": topic.blocks.all(),
            "objectives": topic.objectives.all(),
            "prerequisites": prerequisites,
            "unmet": [lmap.by_id[t] for t in unmet if t in lmap.by_id],
            "dependents": topic.dependent_links.filter(topic__status=Topic.Status.PUBLISHED).select_related("topic")[:6],
            "related": topic.related.filter(status=Topic.Status.PUBLISHED)[:6],
            "card": ReviewCard.objects.filter(user=request.user, topic=topic).first(),
            "mistakes": mistakes,
            "gaps": gaps,
            "stages": [s for s in Stage if s != Stage.NEW],
        },
    )

"""Session planning: what the learner should work on next, and in what mix.

A daily session blends three sources inside the learner's time budget:
  * scheduled reviews (FSRS due cards, most important first),
  * the current/new topic (the adaptive learning cycle),
  * weakness-focused practice (including weak prerequisites — the root cause).
After a long break the mix shifts toward restoring important overdue concepts
and no new material is introduced until the backlog is under control.
"""
from dataclasses import dataclass

from django.utils import timezone

from apps.analytics.services import days_inactive
from apps.curriculum.models import TopicPrerequisite
from apps.curriculum.services import live_topics, prerequisite_map
from apps.reviews.services import REVIEW_SET_SIZE, due_cards

from .models import SessionItem, Stage, TopicMastery

QUESTION_SECONDS = 40
THEORY_SECONDS = 150
MAX_ACTIVE_TOPICS = 3
COMEBACK_DAYS = 7
PREREQ_READY_STAGE = Stage.APPLY

P = SessionItem.Purpose


@dataclass(frozen=True)
class ItemSpec:
    kind: str
    purpose: str
    topic_id: int

    @property
    def seconds(self):
        return THEORY_SECONDS if self.kind == "theory" else QUESTION_SECONDS


def theory(topic_id, purpose):
    return ItemSpec("theory", purpose, topic_id)


def questions(topic_id, purpose, count):
    return [ItemSpec("question", purpose, topic_id)] * count


class LearnerMap:
    """Snapshot of a learner's state across the live curriculum."""

    def __init__(self, user):
        self.user = user
        self.topics = list(live_topics())
        self.by_id = {t.id: t for t in self.topics}
        self.states = {m.topic_id: m for m in TopicMastery.objects.filter(user=user, topic_id__in=self.by_id)}
        self.hard = prerequisite_map(TopicPrerequisite.Strength.HARD)
        self.all = prerequisite_map()

    def stage(self, topic_id):
        state = self.states.get(topic_id)
        return state.stage if state else Stage.NEW

    def unmet_prerequisites(self, topic_id, hard_only=True):
        """Live prerequisites the learner has not yet brought to the Apply stage."""
        graph = self.hard if hard_only else self.all
        return [p for p in graph.get(topic_id, ()) if p in self.by_id and self.stage(p) < PREREQ_READY_STAGE]

    def is_unlocked(self, topic_id):
        return not self.unmet_prerequisites(topic_id)

    def active(self):
        """Topics being actively learned (Learn..Apply), most recently practised first.

        Master-stage topics are excluded: what they still need is delayed recall,
        which the FSRS review schedule provides."""
        rows = [m for m in self.states.values() if Stage.LEARN <= m.stage <= Stage.APPLY and not m.assumed and not m.is_weak and m.topic_id in self.by_id]
        return sorted(rows, key=lambda m: (m.last_practiced_at or m.updated_at), reverse=True)

    def next_new(self, limit=1):
        """Next unstarted topics in curriculum order whose hard prerequisites are ready."""
        found = []
        for topic in self.topics:
            if self.stage(topic.id) == Stage.NEW and self.is_unlocked(topic.id):
                found.append(topic)
                if len(found) == limit:
                    break
        return found

    def weak(self):
        """Weak concepts, with weak prerequisites (root causes) placed first."""
        weak = sorted((m for m in self.states.values() if m.is_weak), key=lambda m: m.score)
        ordered, seen = [], set()
        for state in weak:
            for prereq_id in self.hard.get(state.topic_id, ()):
                prereq = self.states.get(prereq_id)
                if prereq_id in self.by_id and prereq_id not in seen and (prereq is None or (prereq.stage < Stage.MASTER and prereq.score < 0.6)):
                    seen.add(prereq_id)
                    ordered.append(prereq_id)
            if state.topic_id not in seen:
                seen.add(state.topic_id)
                ordered.append(state.topic_id)
        return ordered

    def recommendation(self):
        """The single most useful next activity, for the dashboard."""
        active = self.active()
        if active:
            return {"kind": "continue", "topic": self.by_id[active[0].topic_id], "state": active[0]}
        new = self.next_new()
        if new:
            return {"kind": "new", "topic": new[0], "state": self.states.get(new[0].id)}
        weak = self.weak()
        if weak:
            return {"kind": "weak", "topic": self.by_id[weak[0]], "state": self.states.get(weak[0])}
        return None


def _topic_block(lmap, topic_id, purpose, count):
    """Theory first when the learner has not been (re)taught, then questions."""
    state = lmap.states.get(topic_id)
    needs_theory = state is None or state.stage <= Stage.LEARN or state.needs_reteach
    return ([theory(topic_id, purpose)] if needs_theory else []) + questions(topic_id, purpose, count)


def plan_daily(user, minutes, now=None):
    now = now or timezone.now()
    budget = minutes * 60
    lmap = LearnerMap(user)
    due = due_cards(user, now)
    comeback = days_inactive(user.profile, now) >= COMEBACK_DAYS and bool(due)

    plan = []
    spent = 0

    # 1. Scheduled reviews, capped so a backlog never swallows the session.
    review_budget = budget * (0.65 if comeback else 0.4)
    review_topics = []
    for card in due:
        cost = REVIEW_SET_SIZE * QUESTION_SECONDS
        if spent + cost > review_budget and review_topics:
            break
        review_topics.append(card.topic_id)
        plan += questions(card.topic_id, P.REVIEW, REVIEW_SET_SIZE)
        spent += cost

    # 2. Weakness practice (reserved share).
    weak_ids = [t for t in lmap.weak() if t not in review_topics][: 1 if minutes <= 15 else 2]
    weak_plan = []
    for topic_id in weak_ids:
        weak_plan += _topic_block(lmap, topic_id, P.WEAKNESS, 3)
    weak_cost = sum(i.seconds for i in weak_plan)

    # 3. Current topics, then new material if there is room for it.
    core_budget = budget - spent - weak_cost
    core_topics = [m.topic_id for m in lmap.active()[:2]]
    if not comeback and len(lmap.active()) < MAX_ACTIVE_TOPICS and len(core_topics) < 2:
        core_topics += [t.id for t in lmap.next_new(2 - len(core_topics)) if t.id not in weak_ids]
    core_plan = []
    for index, topic_id in enumerate(core_topics):
        block = _topic_block(lmap, topic_id, P.NEW, 5)
        cost = sum(i.seconds for i in block)
        if index > 0 and cost > core_budget:
            break
        core_plan += block
        core_budget -= cost

    plan += core_plan + weak_plan
    spent = sum(i.seconds for i in plan)

    # 4. Use leftover time on the most relevant topic rather than ending early.
    filler_topic = (core_topics or weak_ids or review_topics or [None])[0]
    while filler_topic and budget - spent >= QUESTION_SECONDS * 2 and len(plan) < 60:
        purpose = P.NEW if filler_topic in core_topics else P.WEAKNESS if filler_topic in weak_ids else P.REVIEW
        plan += questions(filler_topic, purpose, 2)
        spent += QUESTION_SECONDS * 2

    # 5. Nothing scheduled at all: keep retained knowledge warm.
    if not plan:
        retained = sorted((m for m in lmap.states.values() if m.stage >= Stage.MASTER), key=lambda m: m.last_practiced_at or m.updated_at)
        for state in retained[: max(1, budget // (REVIEW_SET_SIZE * QUESTION_SECONDS))]:
            plan += questions(state.topic_id, P.REVIEW, REVIEW_SET_SIZE)
    return plan


def plan_topic(user, topic, with_theory=False):
    lmap = LearnerMap(user)
    state = lmap.states.get(topic.id)
    purpose = P.WEAKNESS if state and state.is_weak else P.NEW
    block = _topic_block(lmap, topic.id, purpose, 6)
    if with_theory and block[0].kind != "theory":
        block.insert(0, theory(topic.id, purpose))
    return block


def plan_reviews(user, now=None, max_topics=8):
    plan = []
    for card in due_cards(user, now)[:max_topics]:
        plan += questions(card.topic_id, P.REVIEW, REVIEW_SET_SIZE)
    return plan


def plan_weakness(user, max_topics=3):
    lmap = LearnerMap(user)
    plan = []
    for topic_id in lmap.weak()[:max_topics]:
        plan += _topic_block(lmap, topic_id, P.WEAKNESS, 4)
    return plan


def plan_timed(user, count=10):
    """Timed challenge over concepts the learner has at least practised."""
    lmap = LearnerMap(user)
    eligible = [m.topic_id for m in lmap.states.values() if m.stage >= Stage.PRACTICE and m.topic_id in lmap.by_id]
    if not eligible:
        return []
    return [ItemSpec("question", P.CHALLENGE, eligible[i % len(eligible)]) for i in range(count)]

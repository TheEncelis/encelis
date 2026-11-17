import itertools

from django.contrib.auth import get_user_model

from apps.curriculum.models import Level, Topic, TopicPrerequisite, Unit
from apps.questions.models import Question
from apps.questions.services import save_question

_seq = itertools.count(1)


def level(code="A1", order=None):
    return Level.objects.get_or_create(code=code, defaults={"title": code, "order": order or (["A1", "A2", "B1", "B2", "C1", "C2"].index(code) + 1)})[0]


def topic(slug=None, code="A1", offset=0.5, status=Topic.Status.PUBLISHED, prerequisites=(), soft=()):
    n = next(_seq)
    unit = Unit.objects.get_or_create(level=level(code), title=f"Unit {code}")[0]
    t = Topic.objects.create(unit=unit, slug=slug or f"topic-{n}", title=f"Mavzu {n}", title_en=f"Topic {n}", difficulty_offset=offset, status=status, order=n)
    for p in prerequisites:
        TopicPrerequisite.objects.create(topic=t, prerequisite=p, strength="hard")
    for p in soft:
        TopicPrerequisite.objects.create(topic=t, prerequisite=p, strength="soft")
    return t


def mcq(t, n=None, difficulty=3):
    n = n or next(_seq)
    return save_question(
        t,
        {
            "type": "mcq",
            "difficulty": difficulty,
            "prompt": f"Sentence number {n} ___ here.",
            "payload": {"options": [{"text": f"right{n}"}, {"text": f"wrong{n}a", "error": "tense"}, {"text": f"wrong{n}b"}], "correct": 0},
            "explanation": "Izoh matni.",
            "error_tag": "tense",
        },
        source=Question.Source.ADMIN,
        status=Question.Status.APPROVED,
    )


def gap(t, answer="goes", n=None, difficulty=3):
    n = n or next(_seq)
    return save_question(
        t,
        {"type": "gap", "difficulty": difficulty, "prompt": f"He ___ to school number {n}.", "payload": {"blanks": [[answer]]}, "explanation": "Izoh.", "error_tag": "agreement"},
        source=Question.Source.ADMIN,
        status=Question.Status.APPROVED,
    )


def production(t, qtype="transform", answer="She does not like tea.", n=None):
    n = n or next(_seq)
    fields = {
        "type": qtype,
        "difficulty": 3,
        "prompt": f"She likes tea number {n}." if qtype != "translation" else f"U choy yoqtirmaydi {n}.",
        "instruction": "Inkor qiling.",
        "payload": {"answers": [answer.replace(".", f" {n}.")]},
        "explanation": "Izoh.",
        "error_tag": "auxiliary",
    }
    return save_question(t, fields, source=Question.Source.ADMIN, status=Question.Status.APPROVED)


def learner(email=None, placement_done=True, **profile):
    User = get_user_model()
    n = next(_seq)
    user = User.objects.create_user(username=f"user{n}", email=email or f"user{n}@example.com", password="Strong-pass-123")
    p = user.profile
    p.placement_done = placement_done
    for key, value in profile.items():
        setattr(p, key, value)
    p.save()
    return user


def answer_for(question, correct=True):
    """A raw answer in the shape the checker expects."""
    p = question.payload
    if question.type == "mcq":
        return p["correct"] if correct else (p["correct"] + 1) % len(p["options"])
    if question.type in ("gap", "contextual"):
        return [b[0] if correct else "zzz" for b in p["blanks"]]
    if question.type == "free_production":
        return p["sample"] if correct else "no"
    return p["answers"][0] if correct else question.prompt

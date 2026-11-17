"""Curriculum graph operations, block validation and ordering helpers."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import TheoryBlock, Topic, TopicPrerequisite


def prerequisite_map(strength=None):
    """topic_id -> set(prerequisite_id) for the whole curriculum (one query)."""
    links = TopicPrerequisite.objects.all()
    if strength:
        links = links.filter(strength=strength)
    graph = defaultdict(set)
    for topic_id, prereq_id in links.values_list("topic_id", "prerequisite_id"):
        graph[topic_id].add(prereq_id)
    return graph


def would_create_cycle(topic_id, prerequisite_id, graph=None):
    """True when adding edge topic -> prerequisite closes a loop."""
    if topic_id == prerequisite_id:
        return True
    graph = graph if graph is not None else prerequisite_map()
    stack, seen = [prerequisite_id], set()
    while stack:
        node = stack.pop()
        if node == topic_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False


def ancestors(topic_id, graph=None):
    """All transitive prerequisites of a topic."""
    graph = graph if graph is not None else prerequisite_map()
    stack, seen = list(graph.get(topic_id, ())), set()
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return seen


@transaction.atomic
def set_prerequisites(topic, pairs):
    """Replace a topic's prerequisites with [(prerequisite_id, strength)], rejecting cycles."""
    TopicPrerequisite.objects.filter(topic=topic).delete()
    graph = prerequisite_map()
    for prereq_id, strength in pairs:
        if would_create_cycle(topic.id, prereq_id, graph):
            raise ValidationError("Aylanma bog'liqlik: mavzu o'zining davomchisiga bog'lana olmaydi.")
        TopicPrerequisite.objects.create(topic=topic, prerequisite_id=prereq_id, strength=strength)
        graph[topic.id].add(prereq_id)


def live_topics():
    return (
        Topic.objects.filter(status=Topic.Status.PUBLISHED, unit__is_published=True)
        .select_related("unit__level")
        .order_by("unit__level__order", "unit__order", "order", "id")
    )


@transaction.atomic
def reorder(kind, parent_id, ids):
    """Persist a drag-and-drop order.

    kind: "unit" (parent = level), "topic" (parent = unit; topics may be dropped
    into another unit) or "block" (parent = topic). `ids` is the complete new
    order of the parent's children. Raises ValidationError on foreign ids.
    """
    from .models import Level, Unit

    ids = [int(i) for i in ids]
    if len(ids) != len(set(ids)):
        raise ValidationError("Takroriy element.")
    if kind == "unit":
        parent = Level.objects.get(pk=parent_id)
        items = {u.pk: u for u in Unit.objects.filter(pk__in=ids, level=parent)}
    elif kind == "topic":
        parent = Unit.objects.get(pk=parent_id)
        items = {t.pk: t for t in Topic.objects.filter(pk__in=ids)}
    elif kind == "block":
        parent = Topic.objects.get(pk=parent_id)
        items = {b.pk: b for b in TheoryBlock.objects.filter(pk__in=ids, topic=parent)}
    else:
        raise ValidationError("Noma'lum tur.")
    if len(items) != len(ids):
        raise ValidationError("Ro'yxatda begona element bor.")
    fields = ["order"]
    for position, pk in enumerate(ids):
        item = items[pk]
        item.order = position
        if kind == "topic" and item.unit_id != parent.pk:
            item.unit_id = parent.pk
    if kind == "topic":
        fields.append("unit")
    if items:
        type(next(iter(items.values()))).objects.bulk_update(items.values(), fields)
    return parent




# --- Theory block validation -------------------------------------------------
def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Matn bo'sh bo'lmasligi kerak.")
    return value.strip()[:limit]


def _opt(value, limit=300):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _items(value, minimum=1, maximum=20):
    if not isinstance(value, list) or not (minimum <= len(value) <= maximum):
        raise ValidationError(f"Kamida {minimum} ta element kerak.")
    return value


def clean_block_data(kind, data):
    """Validate and normalise block payloads. Raises ValidationError on malformed input."""
    if not isinstance(data, dict):
        raise ValidationError("Blok ma'lumoti noto'g'ri.")
    K = TheoryBlock.Kind
    if kind == K.EXPLANATION:
        return {"title": _opt(data.get("title")), "text": _text(data.get("text"))}
    if kind == K.NOTE:
        return {"text": _text(data.get("text"), 1500)}
    if kind == K.FORMULA:
        return {"title": _opt(data.get("title")), "pattern": _text(data.get("pattern"), 400), "note": _opt(data.get("note"), 600)}
    if kind == K.EXAMPLE:
        return {
            "items": [
                {"en": _text(i.get("en"), 400), "uz": _opt(i.get("uz"), 400), "note": _opt(i.get("note"), 400)}
                for i in _items(data.get("items"))
                if isinstance(i, dict)
            ]
        }
    if kind == K.MISTAKE:
        return {
            "items": [
                {"wrong": _text(i.get("wrong"), 400), "right": _text(i.get("right"), 400), "why": _opt(i.get("why"), 600)}
                for i in _items(data.get("items"))
                if isinstance(i, dict)
            ]
        }
    if kind == K.TABLE:
        headers = [_opt(h, 120) for h in _items(data.get("headers"), 2, 6)]
        rows = []
        for row in _items(data.get("rows"), 1, 30):
            if not isinstance(row, list) or len(row) != len(headers):
                raise ValidationError("Jadval qatorlari ustunlar soniga mos kelishi kerak.")
            rows.append([_opt(cell, 300) for cell in row])
        return {"title": _opt(data.get("title")), "headers": headers, "rows": rows}
    if kind == K.COMPARISON:
        return {
            "left_title": _text(data.get("left_title"), 120),
            "right_title": _text(data.get("right_title"), 120),
            "pairs": [
                {"left": _text(p.get("left"), 500), "right": _text(p.get("right"), 500)} for p in _items(data.get("pairs")) if isinstance(p, dict)
            ],
            "note": _opt(data.get("note"), 600),
        }
    raise ValidationError("Noma'lum blok turi.")

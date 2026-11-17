from django import template
from django.utils import timezone

from apps.learning.models import Stage

register = template.Library()

STAGE_HINTS = {
    Stage.NEW: "Hali boshlanmagan",
    Stage.LEARN: "Nazariyani o'qing",
    Stage.UNDERSTAND: "Tushunchani tekshirish",
    Stage.PRACTICE: "Turli mashqlar bilan mustahkamlash",
    Stage.APPLY: "Mustaqil qo'llash",
    Stage.MASTER: "Aniqlik, tezlik va barqarorlikni isbotlash",
    Stage.RETAIN: "Vaqti-vaqti bilan takrorlanadi",
}
GAP_LABELS = {
    "score": "umumiy aniqlik hali yetarli emas",
    "types": "kamida 3 xil turdagi mashqda to'g'ri javob kerak",
    "questions": "ko'proq turli savollarda sinab ko'rish kerak",
    "production": "gap tuzish / tarjima kabi mustaqil mashqlarda ko'proq to'g'ri javob kerak",
    "delayed": "kamida bir kun o'tib qayta to'g'ri javob berish kerak",
    "fluency": "javoblar hali sekin — avtomatik qo'llash kerak",
    "repeated_error": "bir xil xato takrorlanmoqda",
}
WEAK_LABELS = {
    "struggling": "Qiyinchilik sezildi",
    "repeated_error": "Takroriy xato",
    "low_accuracy": "Aniqlik past",
    "forgotten": "Unutilmoqda",
    "placement": "Daraja testida",
}


@register.filter
def stage_label(value):
    try:
        return Stage(int(value)).label
    except (TypeError, ValueError):
        return ""


@register.filter
def stage_hint(value):
    try:
        return STAGE_HINTS[Stage(int(value))]
    except (TypeError, ValueError, KeyError):
        return ""


@register.filter
def gap_label(value):
    return GAP_LABELS.get(value, value)


@register.filter
def weak_label(value):
    return WEAK_LABELS.get(value, "Zaif tomon")


@register.inclusion_tag("partials/stage_track.html")
def stage_track(stage):
    stage = int(stage or 0)
    return {"steps": [{"on": stage >= s, "retain": s == Stage.RETAIN} for s in range(1, 7)]}


@register.filter
def minutes(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60} daq" if seconds >= 60 else f"{seconds} son"


@register.filter
def until(value):
    """Uzbek relative time until a datetime."""
    if not value:
        return ""
    delta = value - timezone.now()
    seconds = delta.total_seconds()
    if seconds <= 0:
        return "hozir"
    hours = seconds / 3600
    if hours < 1:
        return f"{max(1, round(seconds / 60))} daqiqadan so'ng"
    if hours < 24:
        return f"{round(hours)} soatdan so'ng"
    days = round(hours / 24)
    if days < 30:
        return f"{days} kundan so'ng"
    return f"{round(days / 30)} oydan so'ng"


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def percent(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def letter(index):
    return "ABCDEFGH"[int(index)] if 0 <= int(index) < 8 else ""


@register.simple_tag(takes_context=True)
def current(context, *prefixes):
    path = context["request"].path
    return 'aria-current="page"' if any(path.startswith(p) for p in prefixes) else ""


@register.filter
def paragraphs(text):
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    import re

    parts = [p.strip() for p in str(text or "").split("\n\n") if p.strip()]
    html = []
    for part in parts:
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(escape(part)))
        html.append(f"<p>{safe.replace(chr(10), '<br>')}</p>")
    return mark_safe("".join(html))

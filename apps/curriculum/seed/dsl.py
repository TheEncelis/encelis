"""Compact authoring helpers for seed content.

Every helper returns plain dicts in the exact shape `questions.services.save_question`
and `curriculum.services.clean_block_data` expect, so seed content goes through the
same validation as admin- and AI-authored content.
"""

# --- theory blocks --------------------------------------------------------------
def E(title, text):
    return {"kind": "explanation", "data": {"title": title, "text": text}}


def F(pattern, title="", note=""):
    return {"kind": "formula", "data": {"title": title, "pattern": pattern, "note": note}}


def X(*items):
    """Examples: (en, uz) or (en, uz, note)."""
    return {"kind": "example", "data": {"items": [dict(zip(("en", "uz", "note"), item)) for item in items]}}


def T(headers, rows, title=""):
    return {"kind": "table", "data": {"title": title, "headers": list(headers), "rows": [list(r) for r in rows]}}


def C(left_title, right_title, pairs, note=""):
    return {"kind": "comparison", "data": {"left_title": left_title, "right_title": right_title, "pairs": [{"left": l, "right": r} for l, r in pairs], "note": note}}


def N(text):
    return {"kind": "note", "data": {"text": text}}


def M(*items):
    """Common mistakes: (wrong, right, why)."""
    return {"kind": "mistake", "data": {"items": [dict(zip(("wrong", "right", "why"), item)) for item in items]}}


# --- questions ----------------------------------------------------------------
def _q(qtype, d, prompt, payload, expl, tag, instruction=""):
    return {"type": qtype, "difficulty": d, "prompt": prompt, "payload": payload, "explanation": expl, "error_tag": tag, "instruction": instruction}


def mcq(d, prompt, options, expl, tag="verb_form", instruction="To'g'ri variantni tanlang."):
    """Options: correct one starts with '*'; a wrong option may end with '#tag'."""
    parsed, correct = [], None
    for index, option in enumerate(options):
        text, _, error = option.partition("#")
        if text.startswith("*"):
            correct, text = index, text[1:]
        item = {"text": text.strip()}
        if error:
            item["error"] = error.strip()
        parsed.append(item)
    return _q("mcq", d, prompt, {"options": parsed, "correct": correct}, expl, tag, instruction)


def _blanks(prompt, blanks):
    """A flat list of strings for a one-blank prompt lists alternatives for that blank."""
    import re

    if len(re.findall(r"_{3,}", prompt)) == 1 and all(isinstance(b, str) for b in blanks):
        return [list(blanks)]
    return [b if isinstance(b, list) else [b] for b in blanks]


def gap(d, prompt, blanks, expl, tag="verb_form", hint="", instruction="Bo'sh joyni to'ldiring."):
    blanks = _blanks(prompt, blanks)
    payload = {"blanks": blanks}
    if hint:
        payload["hint"] = hint
    return _q("gap", d, prompt, payload, expl, tag, instruction)


def ctx(d, context, prompt, blanks, expl, tag="tense", instruction="Matnni o'qing va bo'sh joyni to'ldiring."):
    blanks = _blanks(prompt, blanks)
    return _q("contextual", d, prompt, {"context": context, "blanks": blanks}, expl, tag, instruction)


def tr(d, instruction, prompt, answers, expl, tag="structure"):
    return _q("transform", d, prompt, {"answers": list(answers)}, expl, tag, instruction)


def err(d, prompt, answers, expl, tag="verb_form"):
    return _q("error_correction", d, prompt, {"answers": list(answers)}, expl, tag, "Gapdagi xatoni tuzating va to'liq yozing.")


def build(d, uz, answer, expl, tag="word_order", alternatives=()):
    tokens = list(reversed(answer.rstrip(".?!").split()))  # the UI reshuffles per learner
    return _q("construction", d, uz, {"tokens": tokens, "answers": [answer, *alternatives]}, expl, tag, "So'zlardan gap tuzing.")


def tl(d, uz, answers, expl, tag="structure"):
    return _q("translation", d, uz, {"answers": list(answers)}, expl, tag, "Ingliz tiliga tarjima qiling.")


def free(d, task, sample, criteria, expl, patterns=(), hint="", min_words=5):
    payload = {"sample": sample, "criteria": criteria, "required_patterns": list(patterns), "min_words": min_words}
    if hint:
        payload["pattern_hint"] = hint
    return _q("free_production", d, task, payload, expl, "structure", "Erkin yozing.")


def topic(slug, title, title_en, summary, *, offset, objectives, prerequisites=(), soft=(), related=(), blocks, questions, threshold=0.8):
    return {
        "slug": slug,
        "title": title,
        "title_en": title_en,
        "summary": summary,
        "difficulty_offset": offset,
        "mastery_threshold": threshold,
        "objectives": list(objectives),
        "prerequisites": list(prerequisites),
        "soft": list(soft),
        "related": list(related),
        "blocks": list(blocks),
        "questions": list(questions),
    }

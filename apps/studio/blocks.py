"""Conversion between theory blocks and simple, line-based editor fields."""
from apps.curriculum.models import TheoryBlock

K = TheoryBlock.Kind
SEP = "||"


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _split(line, parts, sep=SEP):
    values = [v.strip() for v in line.split(sep)]
    return (values + [""] * parts)[:parts]


def data_from_post(kind, post):
    g = post.get
    if kind == K.EXPLANATION:
        return {"title": g("title", ""), "text": g("text", "")}
    if kind == K.NOTE:
        return {"text": g("text", "")}
    if kind == K.FORMULA:
        return {"title": g("title", ""), "pattern": g("pattern", ""), "note": g("note", "")}
    if kind == K.EXAMPLE:
        return {"items": [dict(zip(("en", "uz", "note"), _split(line, 3))) for line in _lines(g("items"))]}
    if kind == K.MISTAKE:
        return {"items": [dict(zip(("wrong", "right", "why"), _split(line, 3))) for line in _lines(g("items"))]}
    if kind == K.TABLE:
        headers = [h.strip() for h in g("headers", "").split("|") if h.strip()]
        # Rows are not padded: a row with the wrong number of cells must fail validation.
        return {"title": g("title", ""), "headers": headers, "rows": [[c.strip() for c in line.split("|")] for line in _lines(g("rows"))]}
    if kind == K.COMPARISON:
        return {
            "left_title": g("left_title", ""),
            "right_title": g("right_title", ""),
            "pairs": [dict(zip(("left", "right"), _split(line, 2))) for line in _lines(g("pairs"))],
            "note": g("note", ""),
        }
    return {}


def fields_for(block_kind, data):
    """Initial editor values for a block."""
    d = data or {}
    if block_kind == K.EXAMPLE:
        return {"items": "\n".join(f" {SEP} ".join(filter(None, [i.get("en", ""), i.get("uz", ""), i.get("note", "")])) for i in d.get("items", []))}
    if block_kind == K.MISTAKE:
        return {"items": "\n".join(f"{i.get('wrong', '')} {SEP} {i.get('right', '')} {SEP} {i.get('why', '')}" for i in d.get("items", []))}
    if block_kind == K.TABLE:
        return {"title": d.get("title", ""), "headers": " | ".join(d.get("headers", [])), "rows": "\n".join(" | ".join(r) for r in d.get("rows", []))}
    if block_kind == K.COMPARISON:
        return {
            "left_title": d.get("left_title", ""),
            "right_title": d.get("right_title", ""),
            "pairs": "\n".join(f"{p.get('left', '')} {SEP} {p.get('right', '')}" for p in d.get("pairs", [])),
            "note": d.get("note", ""),
        }
    return dict(d)

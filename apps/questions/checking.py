"""Deterministic answer checking and mistake classification.

`check` never guesses: when a text answer matches no accepted form the verdict
is UNCERTAIN and the learning engine decides whether to consult a stored
verdict or the AI evaluator.
"""
import re
from dataclasses import dataclass, field
from itertools import product

from .models import ErrorTag, QType

CORRECT, INCORRECT, UNCERTAIN = "correct", "incorrect", "uncertain"

_SIMPLE = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "won't": "will not", "can't": "cannot", "can not": "cannot", "shan't": "shall not",
    "let's": "let us", "gonna": "going to", "wanna": "want to",
}  # fmt: skip
_AMBIGUOUS_S = re.compile(r"\b(he|she|it|that|there|what|who|where|here|how)'s\b")
_AMBIGUOUS_D = re.compile(r"\b(i|you|he|she|it|we|they|there|who)'d\b")
ARTICLES = {"a", "an", "the"}
PREPOSITIONS = {
    "in", "on", "at", "to", "for", "from", "with", "by", "of", "about", "into", "over", "under", "since",
    "during", "through", "between", "among", "after", "before", "until", "till", "off", "up", "down", "out",
}  # fmt: skip
AUXILIARIES = {
    "am", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must", "not",
}  # fmt: skip


def normalize(text):
    text = (text or "").lower().strip()
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("ʼ", "'")
    text = re.sub(r"[“”\"]", "", text)
    text = re.sub(r"[.!?;:,]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def variants(text):
    """All normalised readings of a sentence with contractions expanded."""
    base = normalize(text)
    for short, full in _SIMPLE.items():
        base = re.sub(rf"\b{re.escape(short)}\b", full, base)
    base = re.sub(r"\b(\w+)n't\b", r"\1 not", base)
    base = re.sub(r"\b(\w+)'ve\b", r"\1 have", base)
    base = re.sub(r"\b(\w+)'ll\b", r"\1 will", base)
    base = re.sub(r"\b(\w+)'re\b", r"\1 are", base)
    slots = []

    def mark(options):
        def inner(match):
            slots.append([f"{match.group(1)} {o}" for o in options])
            return f"\x00{len(slots) - 1}\x00"

        return inner

    base = _AMBIGUOUS_S.sub(mark(("is", "has")), base)
    base = _AMBIGUOUS_D.sub(mark(("would", "had")), base)
    if not slots:
        return {base}
    results = set()
    for combo in product(*slots[:4]):
        reading = base
        for index, choice in enumerate(combo):
            reading = reading.replace(f"\x00{index}\x00", choice)
        results.add(re.sub(r"\x00\d+\x00", "", reading))
    return results


def same(answer, accepted):
    return bool(variants(answer) & variants(accepted))


def matches_any(answer, accepted_list):
    return any(same(answer, accepted) for accepted in accepted_list)


def _edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _stem(word):
    for suffix in ("ing", "ies", "ed", "es", "s", "d"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def _word_kind(a, b):
    if a in ARTICLES and b in ARTICLES:
        return ErrorTag.ARTICLE
    if a in PREPOSITIONS and b in PREPOSITIONS:
        return ErrorTag.PREPOSITION
    if a in AUXILIARIES and b in AUXILIARIES:
        return ErrorTag.AGREEMENT if {a, b} <= {"is", "are", "am", "was", "were", "do", "does", "have", "has"} else ErrorTag.AUXILIARY
    stem_a, stem_b = _stem(a), _stem(b)
    if stem_a == stem_b or (len(stem_a) >= 3 and (stem_a.startswith(stem_b) or stem_b.startswith(stem_a))):
        return ErrorTag.VERB_FORM
    if _edit_distance(a, b) <= 2 and min(len(a), len(b)) > 3:
        return ErrorTag.SPELLING
    return None


def classify_mistake(answer, accepted_list):
    """Best-effort deterministic error category, or None when unclear."""
    given = next(iter(sorted(variants(answer)))).split()
    best = None
    for accepted in accepted_list:
        expected = next(iter(sorted(variants(accepted)))).split()
        if given == expected:
            return None
        if sorted(given) == sorted(expected):
            return ErrorTag.WORD_ORDER
        if len(given) == len(expected):
            diff = [(g, e) for g, e in zip(given, expected) if g != e]
            if len(diff) == 1:
                best = best or _word_kind(*diff[0])
        elif abs(len(given) - len(expected)) == 1:
            longer, shorter = (given, expected) if len(given) > len(expected) else (expected, given)
            for index in range(len(longer)):
                if longer[:index] + longer[index + 1 :] == shorter:
                    word = longer[index]
                    if word in ARTICLES:
                        return ErrorTag.ARTICLE
                    if word in PREPOSITIONS:
                        return ErrorTag.PREPOSITION
                    if word in AUXILIARIES:
                        return ErrorTag.AUXILIARY
                    best = best or (ErrorTag.EXTRA_WORD if longer is given else ErrorTag.MISSING_WORD)
                    break
    return best


@dataclass
class CheckResult:
    verdict: str
    score: float = 0.0
    expected: str = ""
    error_tag: str = ""
    normalized: str = ""
    problems: list = field(default_factory=list)


def expected_text(question):
    """Canonical correct answer shown to the learner."""
    payload = question.payload
    if question.type == QType.MCQ:
        return payload["options"][payload["correct"]]["text"]
    if question.type in (QType.GAP, QType.CONTEXT):
        parts = iter(blank[0] for blank in payload["blanks"])
        return re.sub(r"_{3,}", lambda m: next(parts, "___"), question.prompt)
    if question.type == QType.FREE:
        return payload.get("sample", "")
    return payload["answers"][0]


def check(question, raw):
    """raw: option index for mcq, list of strings for gap/contextual, text otherwise."""
    payload = question.payload
    expected = expected_text(question)

    if question.type == QType.MCQ:
        try:
            index = int(raw)
            option = payload["options"][index]
        except (TypeError, ValueError, IndexError):
            return CheckResult(INCORRECT, expected=expected, error_tag=question.error_tag, normalized="")
        if index == payload["correct"]:
            return CheckResult(CORRECT, 1.0, expected, normalized=str(index))
        return CheckResult(INCORRECT, 0.0, expected, option.get("error") or question.error_tag, str(index))

    if question.type in (QType.GAP, QType.CONTEXT):
        given = [str(v) for v in raw] if isinstance(raw, (list, tuple)) else [str(raw or "")]
        blanks = payload["blanks"]
        given += [""] * (len(blanks) - len(given))
        hits = [matches_any(value, accepted) for value, accepted in zip(given, blanks)]
        normalized = " | ".join(normalize(v) for v in given[: len(blanks)])
        if all(hits):
            return CheckResult(CORRECT, 1.0, expected, normalized=normalized)
        tag = None
        for value, accepted, hit in zip(given, blanks, hits):
            if not hit and value.strip():
                tag = classify_mistake(value, accepted)
                break
        return CheckResult(INCORRECT, sum(hits) / len(hits) * 0.5, expected, tag or question.error_tag, normalized)

    text = str(raw or "").strip()
    normalized = normalize(text)[:600]
    if not normalized:
        return CheckResult(INCORRECT, 0.0, expected, ErrorTag.OTHER, "")

    if question.type == QType.FREE:
        problems = free_precheck(question, text)
        if problems:
            return CheckResult(INCORRECT, 0.0, expected, ErrorTag.STRUCTURE, normalized, problems)
        return CheckResult(UNCERTAIN, expected=expected, normalized=normalized)

    answers = payload["answers"]
    if matches_any(text, answers):
        return CheckResult(CORRECT, 1.0, expected, normalized=normalized)
    tag = classify_mistake(text, answers)
    if question.type == QType.CONSTRUCT:
        # All tokens are given, so any mismatch is definitively wrong.
        return CheckResult(INCORRECT, 0.0, expected, tag or ErrorTag.WORD_ORDER, normalized)
    if question.type == QType.ERROR and same(text, question.prompt):
        return CheckResult(INCORRECT, 0.0, expected, question.error_tag, normalized)
    # A different wording may still be valid English: let a validated evaluator decide.
    return CheckResult(UNCERTAIN, expected=expected, error_tag=tag or "", normalized=normalized)


def free_precheck(question, text):
    """Deterministic gates a free answer must pass before any AI evaluation."""
    payload, problems = question.payload, []
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < payload.get("min_words", 4):
        problems.append(f"Javob juda qisqa: kamida {payload.get('min_words', 4)} ta so'z yozing.")
    if len(text) > 600:
        problems.append("Javob juda uzun.")
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isascii() for c in letters) / len(letters) < 0.9:
        problems.append("Javobni ingliz tilida yozing.")
    for pattern in payload.get("required_patterns", []):
        if not re.search(pattern, text, flags=re.IGNORECASE):
            problems.append(payload.get("pattern_hint") or "Javobda talab qilingan grammatik qurilma ishlatilmagan.")
            break
    return problems

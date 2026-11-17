"""Mastery assessment — pure functions, no database access.

Mastery of a concept is an evidence-weighted estimate, not a completion flag.
Each answer contributes *evidence* whose weight depends on what kind of
exercise it was, how hard it was, whether the learner had seen that exact
question before, and whether it came after a real delay. Quality of the
answer blends correctness with response fluency.

    score = (sum(weight * quality) / sum(weight)) * confidence
    confidence = min(1, independent_evidence / REQUIRED_EVIDENCE)

`confidence` keeps a handful of lucky answers from producing a high score, and
the novelty discount keeps repeated exposure to identical questions from
inflating it. Stage transitions (see `decide_stage`) additionally require
breadth: several exercise types, productive use, and delayed recall.
"""
from dataclasses import dataclass

from apps.questions.models import TYPE_META, QType

from .models import Stage

WINDOW = 24  # most recent counted attempts considered
REQUIRED_EVIDENCE = 5.0
RECENCY_DECAY = 0.93
FLUENT_RATIO = 1.5  # an answer within 1.5x the expected time counts as fluent
QUESTION_MASS_CAP = 1.5


@dataclass(frozen=True)
class Evidence:
    score: float
    q_type: str
    difficulty: int
    response_ms: int
    expected_ms: int
    question_id: int
    repeat_index: int = 0
    hours_since_seen: float | None = None
    delayed: bool = False
    in_stage: bool = True  # answered since the learner entered the current stage
    error_tag: str = ""


@dataclass(frozen=True)
class Signals:
    score: float = 0.0
    confidence: float = 0.0
    accuracy: float = 0.0
    recent_accuracy: float = 0.0
    fluency: float = 0.0
    attempts: int = 0
    distinct_questions: int = 0
    distinct_types: int = 0
    production_correct: int = 0
    delayed_correct: int = 0
    stage_attempts: int = 0
    stage_recent_correct: int = 0  # correct among the last 4 in-stage answers
    stage_recent_total: int = 0
    repeated_error: str = ""  # error category repeated >= 3 times in the last 8 answers


def novelty(repeat_index, hours_since_seen):
    """Discount for answers to a question the learner has already seen."""
    if repeat_index == 0:
        return 1.0
    if hours_since_seen is not None and hours_since_seen < 24:
        return 0.2
    return 0.5


def evidence_weight(e):
    type_weight = TYPE_META[QType(e.q_type)].weight
    difficulty_weight = 0.7 + 0.15 * (e.difficulty - 1)
    return type_weight * difficulty_weight * novelty(e.repeat_index, e.hours_since_seen) * (1.3 if e.delayed else 1.0)


def fluency_factor(response_ms, expected_ms):
    """1.0 for a fluent answer, falling linearly to 0.8 at 2.5x the expected time."""
    if not expected_ms or not response_ms:
        return 1.0
    ratio = response_ms / expected_ms
    if ratio <= 1:
        return 1.0
    return max(0.8, 1.0 - 0.2 * (ratio - 1) / 1.5)


def compute_signals(evidence):
    """evidence: counted attempts for one learner x topic, oldest first."""
    window = list(evidence)[-WINDOW:]
    if not window:
        return Signals()
    weighted_quality = total_weight = 0.0
    per_question = {}
    for age, e in enumerate(reversed(window)):
        weight = evidence_weight(e)
        decayed = weight * (RECENCY_DECAY**age)
        weighted_quality += decayed * e.score * fluency_factor(e.response_ms, e.expected_ms)
        total_weight += decayed
        type_weight = TYPE_META[QType(e.q_type)].weight
        # A single question can never supply more than ~1.5 answers' worth of
        # confidence, however often it is repeated.
        per_question[e.question_id] = min(type_weight * QUESTION_MASS_CAP, per_question.get(e.question_id, 0.0) + type_weight * novelty(e.repeat_index, e.hours_since_seen))
    accuracy = weighted_quality / total_weight if total_weight else 0.0
    confidence = min(1.0, sum(per_question.values()) / REQUIRED_EVIDENCE)

    correct = [e for e in window if e.score >= 0.8]
    first_exposure_correct = [e for e in correct if e.repeat_index == 0]
    recent = window[-5:]
    in_stage = [e for e in window if e.in_stage]
    stage_recent = in_stage[-4:]
    tags = [e.error_tag for e in window[-8:] if e.score < 0.8 and e.error_tag]
    repeated = next((t for t in dict.fromkeys(tags) if tags.count(t) >= 3), "")
    return Signals(
        score=round(accuracy * confidence, 4),
        confidence=round(confidence, 4),
        accuracy=round(accuracy, 4),
        recent_accuracy=round(sum(e.score for e in recent) / len(recent), 4),
        fluency=round(sum(1 for e in correct if e.response_ms <= e.expected_ms * FLUENT_RATIO) / len(correct), 4) if correct else 0.0,
        attempts=len(window),
        distinct_questions=len({e.question_id for e in window}),
        distinct_types=len({e.q_type for e in first_exposure_correct}),
        production_correct=sum(1 for e in first_exposure_correct if TYPE_META[QType(e.q_type)].production),
        delayed_correct=sum(1 for e in correct if e.delayed),
        stage_attempts=len(in_stage),
        stage_recent_correct=sum(1 for e in stage_recent if e.score >= 0.8),
        stage_recent_total=len(stage_recent),
        repeated_error=repeated,
    )


@dataclass(frozen=True)
class StageDecision:
    stage: int
    reteach: bool = False
    reason: str = ""


def mastery_gaps(s, threshold=0.8):
    """What still separates the learner from demonstrated mastery (empty list = mastered)."""
    gaps = []
    if s.score < threshold:
        gaps.append("score")
    if s.distinct_types < 3:
        gaps.append("types")
    if s.distinct_questions < 6:
        gaps.append("questions")
    if s.production_correct < 3:
        gaps.append("production")
    if s.delayed_correct < 1:
        gaps.append("delayed")
    if s.fluency < 0.6:
        gaps.append("fluency")
    if s.repeated_error:
        gaps.append("repeated_error")
    return gaps


def decide_stage(stage, s, theory_seen, threshold=0.8):
    """One step of the adaptive Learn -> Understand -> Practice -> Apply -> Master -> Retain cycle.

    Moves at most one stage forward per call; falls back when recent evidence
    shows the learner is not coping at the current stage.
    """
    recent_full = s.stage_recent_total >= 4
    # --- fall back -----------------------------------------------------------
    if stage in (Stage.UNDERSTAND, Stage.PRACTICE) and recent_full and s.stage_recent_correct <= 1:
        return StageDecision(Stage.LEARN, reteach=True, reason="struggling")
    if stage in (Stage.APPLY, Stage.MASTER) and recent_full and s.stage_recent_correct <= 1:
        return StageDecision(Stage.PRACTICE, reason="struggling")
    if stage == Stage.RETAIN and s.attempts >= 4 and (s.score < 0.6 or s.recent_accuracy < 0.4):
        return StageDecision(Stage.PRACTICE, reason="decayed")
    # --- move forward --------------------------------------------------------
    if stage == Stage.NEW:
        # Evidence alone (e.g. placement probes) never starts a topic; the learner does.
        return StageDecision(Stage.UNDERSTAND if theory_seen else Stage.NEW)
    if stage == Stage.LEARN:
        return StageDecision(Stage.UNDERSTAND if theory_seen else Stage.LEARN)
    if stage == Stage.UNDERSTAND and s.stage_attempts >= 3 and s.stage_recent_correct >= max(2, s.stage_recent_total - 1):
        return StageDecision(Stage.PRACTICE)
    if stage == Stage.PRACTICE and s.stage_attempts >= 4 and s.score >= 0.5 and s.distinct_types >= 2 and s.recent_accuracy >= 0.6:
        return StageDecision(Stage.APPLY)
    if stage == Stage.APPLY and s.stage_attempts >= 3 and s.score >= 0.68 and s.production_correct >= 2:
        return StageDecision(Stage.MASTER)
    if stage == Stage.MASTER and not mastery_gaps(s, threshold):
        return StageDecision(Stage.RETAIN)
    return StageDecision(stage)


def weakness_reason(stage, s, demoted=False):
    """Why a concept should be surfaced as a weak area ('' when it should not)."""
    if demoted:
        return "struggling"
    if s.repeated_error:
        return "repeated_error"
    if stage >= Stage.UNDERSTAND and s.attempts >= 6 and s.accuracy < 0.5:
        return "low_accuracy"
    return ""


def expected_correct(ability, difficulty, guess=0.0):
    """Probability that a learner of `ability` answers an item of absolute `difficulty` correctly."""
    import math

    return guess + (1 - guess) / (1 + math.exp(-1.4 * (ability - difficulty)))


def updated_ability(ability, difficulty, score, guess=0.0, k=0.05):
    """Small Elo-style step that keeps the ability estimate tracking performance after placement."""
    return max(0.0, min(6.0, ability + k * (score - expected_correct(ability, difficulty, guess))))

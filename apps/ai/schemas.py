"""Explicit schemas for every structured AI output."""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.questions.models import ErrorTag, QType

ErrorCategory = Literal[tuple(ErrorTag.values)]
QuestionType = Literal[tuple(QType.values)]
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _latin_uzbek(text):
    if _CYRILLIC.search(text):
        raise ValueError("Uzbek text must use the Latin alphabet")
    return text.strip()


class AnswerEvaluation(BaseModel):
    verdict: Literal["correct", "minor_errors", "incorrect"]
    error_category: Optional[ErrorCategory] = None
    feedback_uz: str = Field(min_length=8, max_length=700)
    corrected: str = Field(default="", max_length=600)

    _uz = field_validator("feedback_uz")(_latin_uzbek)

    @model_validator(mode="after")
    def consistent(self):
        if self.verdict != "correct" and not self.corrected.strip():
            raise ValueError("corrected sentence is required unless the verdict is 'correct'")
        if self.verdict == "incorrect" and self.error_category is None:
            self.error_category = "structure"
        if _CYRILLIC.search(self.corrected):
            raise ValueError("corrected must be English")
        return self


class MistakeExplanation(BaseModel):
    explanation_uz: str = Field(min_length=20, max_length=900)
    rule_uz: str = Field(min_length=5, max_length=300)
    example_en: str = Field(min_length=3, max_length=200)

    _uz = field_validator("explanation_uz", "rule_uz")(_latin_uzbek)


class GeneratedOption(BaseModel):
    text: str = Field(min_length=1, max_length=160)
    error: Optional[ErrorCategory] = None


class GeneratedQuestion(BaseModel):
    type: QuestionType
    difficulty: int = Field(ge=1, le=5)
    instruction_uz: str = Field(default="", max_length=240)
    prompt: str = Field(min_length=3, max_length=700)
    explanation_uz: str = Field(min_length=15, max_length=700)
    error_tag: ErrorCategory = "structure"
    options: Optional[list[GeneratedOption]] = None
    correct_index: Optional[int] = None
    blanks: Optional[list[list[str]]] = None
    answers: Optional[list[str]] = None
    tokens: Optional[list[str]] = None
    context: Optional[str] = None
    sample: Optional[str] = None
    criteria: Optional[str] = None
    required_patterns: Optional[list[str]] = None

    _uz = field_validator("explanation_uz", "instruction_uz")(_latin_uzbek)

    def to_fields(self):
        """Shape expected by questions.services.save_question (validated again there)."""
        if self.type == QType.MCQ:
            payload = {"options": [o.model_dump(exclude_none=True) for o in self.options or []], "correct": self.correct_index}
        elif self.type == QType.GAP:
            payload = {"blanks": self.blanks or []}
        elif self.type == QType.CONTEXT:
            payload = {"blanks": self.blanks or [], "context": self.context or ""}
        elif self.type == QType.CONSTRUCT:
            payload = {"tokens": self.tokens or [], "answers": self.answers or []}
        elif self.type == QType.FREE:
            payload = {"sample": self.sample or "", "criteria": self.criteria or "", "required_patterns": self.required_patterns or []}
        else:
            payload = {"answers": self.answers or []}
        return {
            "type": self.type,
            "difficulty": self.difficulty,
            "instruction": self.instruction_uz,
            "prompt": self.prompt,
            "explanation": self.explanation_uz,
            "error_tag": self.error_tag,
            "payload": payload,
        }


class GeneratedQuestionSet(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=20)


class SolvedQuestion(BaseModel):
    """A blind solution used to cross-check a generated question's answer key."""

    answer_index: Optional[int] = None
    answers: Optional[list[str]] = None
    ambiguous: bool = False


class TheoryBlockDraft(BaseModel):
    kind: Literal["explanation", "example", "formula", "table", "comparison", "note", "mistake"]
    data: dict


class TheoryDraft(BaseModel):
    summary_uz: str = Field(min_length=20, max_length=400)
    objectives_uz: list[str] = Field(min_length=2, max_length=6)
    blocks: list[TheoryBlockDraft] = Field(min_length=4, max_length=14)

    _uz = field_validator("summary_uz")(_latin_uzbek)


class TheoryIssue(BaseModel):
    severity: Literal["high", "medium", "low"]
    block: Optional[int] = None
    problem_uz: str = Field(min_length=5, max_length=500)
    suggestion_uz: str = Field(min_length=5, max_length=700)


class TheoryReview(BaseModel):
    verdict_uz: str = Field(min_length=10, max_length=600)
    issues: list[TheoryIssue] = Field(max_length=15)

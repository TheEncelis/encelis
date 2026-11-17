"""Answer evaluation pipeline: deterministic check -> stored verdict -> validated AI."""
import logging
from dataclasses import dataclass

from django.conf import settings

from apps.core.models import PlatformSetting
from apps.questions import checking
from apps.questions.models import AnswerVerdict, ErrorTag, QType

log = logging.getLogger("encelis.learning")

ERROR_HINTS = {
    ErrorTag.VERB_FORM: "Fe'l shakliga e'tibor bering.",
    ErrorTag.TENSE: "Bu vaziyatda boshqa zamon ishlatiladi.",
    ErrorTag.AGREEMENT: "Ega va kesim shaxs-sonda moslashishi kerak.",
    ErrorTag.AUXILIARY: "Yordamchi fe'l noto'g'ri tanlangan yoki tushib qolgan.",
    ErrorTag.WORD_ORDER: "So'zlar to'g'ri, lekin tartibi noto'g'ri.",
    ErrorTag.ARTICLE: "Artikl (a / an / the) ishlatilishiga e'tibor bering.",
    ErrorTag.PREPOSITION: "Bu yerda boshqa predlog kerak.",
    ErrorTag.SPELLING: "Imloda xato bor — so'zning yozilishini tekshiring.",
    ErrorTag.VOCABULARY: "So'z tanlashda xato bor.",
    ErrorTag.MISSING_WORD: "Gapda bir so'z tushib qolgan.",
    ErrorTag.EXTRA_WORD: "Gapda ortiqcha so'z bor.",
}


@dataclass
class Evaluation:
    is_correct: bool
    score: float
    counted: bool = True
    evaluated_by: str = "deterministic"
    error_tag: str = ""
    expected: str = ""
    feedback: str = ""
    corrected: str = ""
    problems: tuple = ()

    @property
    def hint(self):
        return ERROR_HINTS.get(self.error_tag, "") if not self.is_correct else ""


def ai_evaluation_available():
    return settings.AI_ENABLED and PlatformSetting.get("ai_answer_evaluation")


def evaluate(question, raw, user=None):
    result = checking.check(question, raw)
    if result.verdict == checking.CORRECT:
        return Evaluation(True, 1.0, expected=result.expected, feedback=question.explanation)
    if result.verdict == checking.INCORRECT:
        feedback = " ".join(result.problems) if result.problems else question.explanation
        return Evaluation(
            False, result.score, expected=result.expected, error_tag=result.error_tag, feedback=feedback, problems=tuple(result.problems)
        )

    # Uncertain: a text answer that matches no listed form.
    stored = AnswerVerdict.objects.filter(question=question, normalized=result.normalized).first()
    if stored:
        return Evaluation(
            stored.is_correct,
            stored.score,
            evaluated_by="stored",
            error_tag=stored.error_tag,
            expected=result.expected,
            feedback=stored.feedback or question.explanation,
            corrected=stored.corrected,
        )
    if ai_evaluation_available():
        from apps.ai import services as ai

        verdict = ai.evaluate_answer(question, str(raw), user=user)
        if verdict is not None:
            is_correct = verdict.verdict in ("correct", "minor_errors")
            score = {"correct": 1.0, "minor_errors": 0.7}.get(verdict.verdict, 0.0)
            tag = "" if verdict.verdict == "correct" else (verdict.error_category or result.error_tag or question.error_tag)
            AnswerVerdict.objects.update_or_create(
                question=question,
                normalized=result.normalized,
                defaults={"is_correct": is_correct, "score": score, "error_tag": tag, "feedback": verdict.feedback_uz, "corrected": verdict.corrected},
            )
            return Evaluation(
                is_correct, score, evaluated_by="ai", error_tag=tag, expected=result.expected, feedback=verdict.feedback_uz, corrected=verdict.corrected
            )

    # No validated judgement is possible: keep the answer for history, but it must
    # not move mastery in either direction.
    if question.type == QType.FREE:
        message = "Javobingiz avtomatik baholanmadi. Uni namunaviy javob bilan solishtirib ko'ring."
    else:
        message = "Javobingiz kutilgan javoblarga mos kelmadi, ammo uni aniq baholab bo'lmadi. Namunaviy javob bilan solishtiring."
    return Evaluation(False, 0.0, counted=False, evaluated_by="none", error_tag=result.error_tag, expected=result.expected, feedback=message)

"""Single gateway to the DeepSeek API.

Owns model selection, transport, retries, structured-output validation and
usage tracking. Nothing else in the codebase talks to the API directly, and
callers only ever receive objects that passed their pydantic schema.
"""
import json
import logging
import time
from dataclasses import dataclass

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

log = logging.getLogger("encelis.ai")


class AIError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    model: str
    thinking: bool
    timeout: float
    max_tokens: int


FAST, DEEP = settings.AI_MODEL_FAST, settings.AI_MODEL_DEEP

# Flash handles routine work; V4-Pro with thinking handles hard grammatical reasoning.
PROFILES = {
    "evaluate_answer": Profile(FAST, False, 25, 700),
    "evaluate_answer_deep": Profile(DEEP, True, 60, 1500),
    "explain_mistake": Profile(FAST, False, 25, 700),
    "generate_questions": Profile(FAST, False, 90, 6000),
    "generate_targeted": Profile(FAST, False, 90, 4000),
    "verify_question": Profile(FAST, False, 25, 300),
    "draft_theory": Profile(DEEP, True, 240, 8000),
    "review_theory": Profile(DEEP, True, 240, 5000),
}
TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_TRANSPORT_ATTEMPTS = 3


def is_configured():
    return bool(settings.DEEPSEEK_API_KEY)


def _post(profile, messages):
    body = {
        "model": profile.model,
        "messages": messages,
        "thinking": {"type": "enabled" if profile.thinking else "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": profile.max_tokens,
        "stream": False,
    }
    response = httpx.post(
        f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
        timeout=profile.timeout,
    )
    if response.status_code in TRANSIENT:
        raise httpx.TransportError(f"transient status {response.status_code}")
    if response.status_code >= 400:
        raise AIError(f"API error {response.status_code}: {response.text[:200]}")
    return response.json()


def _extract_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[4:] if content.lower().startswith("json") else content
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(content[start : end + 1])


def complete(task, system, prompt, schema: type[BaseModel], *, user=None, context=None):
    """Run one structured completion. Returns a validated `schema` instance or raises AIError."""
    from .models import AIUsageLog

    if not is_configured():
        raise AIError("DeepSeek API key is not configured.")
    profile = PROFILES[task]
    messages = [{"role": "system", "content": system + "\nRespond with a single valid JSON object and nothing else."}, {"role": "user", "content": prompt}]
    started = time.monotonic()
    attempts = tokens_in = tokens_out = 0
    error = ""
    result = None
    validation_retries = 1
    try:
        while True:
            attempts += 1
            try:
                data = _post(profile, messages)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                error = f"{exc.__class__.__name__}: {exc}"[:300]
                if attempts >= MAX_TRANSPORT_ATTEMPTS:
                    raise AIError(error) from exc
                time.sleep(0.6 * attempts)
                continue
            usage = data.get("usage") or {}
            tokens_in += usage.get("prompt_tokens", 0)
            tokens_out += usage.get("completion_tokens", 0)
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            try:
                result = schema.model_validate(_extract_json(content), context=context)
                error = ""
                return result
            except (ValueError, ValidationError) as exc:
                error = f"invalid output: {str(exc)[:240]}"
                if validation_retries <= 0:
                    raise AIError(error) from exc
                validation_retries -= 1
                messages = messages + [
                    {"role": "assistant", "content": content[:4000]},
                    {"role": "user", "content": f"Your output failed validation: {str(exc)[:600]}\nReturn a corrected JSON object only."},
                ]
    finally:
        try:
            AIUsageLog.objects.create(
                task=task,
                model=profile.model,
                thinking=profile.thinking,
                success=result is not None,
                attempts=attempts,
                prompt_tokens=tokens_in,
                completion_tokens=tokens_out,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=error[:300],
                user=user,
            )
        except Exception:  # usage logging must never mask the real outcome
            log.exception("Could not write AI usage log")
        if result is None:
            log.warning("AI task %s failed after %s attempt(s): %s", task, attempts, error)

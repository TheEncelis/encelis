"""Resend email backend. The API key never leaves the server."""
import logging

import httpx
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger("encelis.email")


class ResendEmailBackend(BaseEmailBackend):
    endpoint = "https://api.resend.com/emails"

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            payload = {
                "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
                "to": list(message.to),
                "subject": message.subject,
                "text": message.body,
            }
            for content, mimetype in getattr(message, "alternatives", []):
                if mimetype == "text/html":
                    payload["html"] = content
            try:
                response = httpx.post(
                    self.endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                    timeout=15,
                )
                response.raise_for_status()
                sent += 1
            except httpx.HTTPError as exc:
                detail = getattr(getattr(exc, "response", None), "text", "")[:300]
                log.error("Resend delivery failed: %s %s", exc.__class__.__name__, detail)
                if not self.fail_silently:
                    raise
        return sent

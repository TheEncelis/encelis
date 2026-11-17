import logging

from celery import shared_task

log = logging.getLogger("encelis.accounts")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email(self, to, subject, template, context):
    from .services import build_message

    try:
        build_message(to, subject, template, context).send()
    except Exception as exc:  # delivery problems must never break a user-facing request
        log.error("Email '%s' to %s failed: %s", template, to, exc)
        if not self.request.is_eager:
            raise self.retry(exc=exc)

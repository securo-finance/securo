"""Outbound email over SMTP, for password recovery (ADR 0041).

Nothing leaves the instance unless EMAIL_ENABLED is on; the same switch makes
the forgot-password route answer 503 instead of pretending a message went
out. The message is built and handed to the relay in a worker thread, so a
slow server never holds the event loop.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

import anyio

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 30


class EmailDisabled(RuntimeError):
    """EMAIL_ENABLED is off, so no message can be sent."""


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


def password_reset_link(token: str, settings: Settings | None = None) -> str:
    """The page the recipient opens to choose a new password."""
    settings = settings or get_settings()
    return (
        f"{settings.frontend_url.rstrip('/')}/reset-password"
        f"?token={quote(token, safe='')}"
    )


def password_reset_email(
    to: str,
    link: str,
    language: str,
    token_minutes: int,
) -> Email:
    """The recovery message in the account's language.

    Only the languages with a full translation are written here; anything
    else gets English, which every instance can be read in.
    """
    minutes = token_minutes
    if (language or "en").lower().startswith("pt"):
        subject = "Redefinir sua senha do Securo"
        text = (
            "Olá,\n\n"
            "Recebemos um pedido para redefinir a senha da sua conta no Securo.\n"
            f"Abra o link abaixo para escolher uma nova senha. Ele vale por {minutes} minutos:\n\n"
            f"{link}\n\n"
            "Se você não pediu isso, ignore esta mensagem: sua senha continua a mesma.\n"
        )
    else:
        subject = "Reset your Securo password"
        text = (
            "Hi,\n\n"
            "We received a request to reset the password for your Securo account.\n"
            f"Open the link below to choose a new password. It is valid for {minutes} minutes:\n\n"
            f"{link}\n\n"
            "If you did not ask for this, ignore this message: your password stays the same.\n"
        )
    return Email(to=to, subject=subject, text=text)


async def send_email(message: Email, settings: Settings | None = None) -> None:
    """Hand the message to the SMTP relay.

    Raises EmailDisabled when the switch is off. A relay that refuses the
    message raises too; the caller decides what the person sees.
    """
    settings = settings or get_settings()
    if not settings.email_enabled:
        raise EmailDisabled("Email is not enabled on this instance")
    await anyio.to_thread.run_sync(_send_now, settings, message)


async def deliver(message: Email, settings: Settings | None = None) -> None:
    """Send after the response is on its way, logging a refusal instead of raising.

    The forgot-password answer must not depend on the relay: a slow or refusing
    server would delay (or fail) only the known addresses, and the difference
    alone tells anyone which accounts exist.
    """
    try:
        await send_email(message, settings)
    except Exception:
        # Operator-only, and without the token or the link.
        logger.warning(
            "Could not send the password recovery email to %s", message.to, exc_info=True
        )


def _send_now(settings: Settings, message: Email) -> None:
    email = EmailMessage()
    email["From"] = settings.email_from or settings.smtp_username
    email["To"] = message.to
    email["Subject"] = message.subject
    email.set_content(message.text)

    # An explicit context: smtplib's own default skips certificate checks for
    # STARTTLS, which would let anything on the path take the relay's place.
    tls = ssl.create_default_context()

    if settings.smtp_security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=SMTP_TIMEOUT_SECONDS,
            context=tls,
        )
    else:
        client = smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        )
    try:
        if settings.smtp_security == "starttls":
            client.starttls(context=tls)
        if settings.smtp_username:
            client.login(
                settings.smtp_username, settings.smtp_password.get_secret_value()
            )
        client.send_message(email)
    finally:
        # A relay that already dropped the connection must not turn the
        # send's own error (or its success) into a different one.
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            client.close()

"""Outbound email for password recovery, over SMTP or a hosted API (ADR 0044).

Nothing leaves the instance unless EMAIL_ENABLED is on; the same switch makes
the forgot-password route answer 503 instead of pretending a message went
out. EMAIL_PROVIDER picks the path behind that switch: an SMTP relay, handed
the message in a worker thread, or the Resend API, called with httpx. The
default message is written into the code for English and Portuguese; an admin
may replace it from the admin screen, and the placeholders are checked before
anything is saved.
"""

import json
import logging
import re
import smtplib
import ssl
import string
import unicodedata
from collections import Counter
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

import anyio
import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 30
API_TIMEOUT_SECONDS = 15

# The languages the recovery message is written in. Anything else gets
# English, which every instance can be read in.
TEMPLATE_LANGUAGES = ("en", "pt")

# What an admin may put in a template. Anything else is refused: a placeholder
# that reaches `.format` unguarded would raise while composing the message,
# and the person who asked to recover their account would never learn why.
TEMPLATE_FIELDS = frozenset({"app_name", "link", "minutes"})

SUBJECT_MAX_LENGTH = 200
BODY_MAX_LENGTH = 4000

# The Unicode categories that carry a line break, whatever the language: a
# control character (Cc: \n, \r, \v, \f, U+001C..U+001E, U+0085) or a
# separator (Zl: U+2028, Zp: U+2029). Only \n and \r are obvious enough to
# check by hand; a subject holding any of the others is accepted by the admin
# and then refused by the SMTP library while composing the message, which the
# reader sees as a recovery email that never arrives.
LINE_BREAK_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})

# An address the recovery message may not carry. The message leads back into
# the account through `{link}` and nothing else, so a second address is either
# a mistake or a way to take the reader somewhere else. Three shapes count: a
# scheme (`https://`, `ftp://`, and the like), a `www.` host, and a bare host
# name (`evil.example`): a mail client turns the last one into a link too, and
# a host with no scheme but a path (`evil.example/{link}`) is the same attack
# with less ceremony.
URL_PATTERN = re.compile(
    r"[a-z][a-z0-9+.-]*://"
    r"|\b(?:https?|ftps?|file|javascript|vbscript):"
    r"|www\."
    r"|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b",
    re.IGNORECASE,
)

# What the placeholders stand for while a template is checked. The check runs
# over the text as the reader gets it, not only as the admin wrote it: a host
# name can be assembled from the values, and `reset{minutes}.com` is no
# address in the template while `reset60.com` is one in the message.
CHECK_LINK = "https://securo.example/reset-password?token=check"
CHECK_VALUES = {"app_name": "Securo", "link": CHECK_LINK, "minutes": 60}


class EmailDisabled(RuntimeError):
    """EMAIL_ENABLED is off, so no message can be sent."""


class TemplateError(ValueError):
    """A template the admin sent cannot be used."""


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "subject": "Reset your {app_name} password",
        "body": (
            "Hi,\n\n"
            "We received a request to reset the password for your {app_name} account.\n"
            "Open the link below to choose a new password. It is valid for {minutes} minutes:\n\n"
            "{link}\n\n"
            "If you did not ask for this, ignore this message: your password stays the same.\n"
        ),
    },
    "pt": {
        "subject": "Redefinir sua senha do {app_name}",
        "body": (
            "Olá,\n\n"
            "Recebemos um pedido para redefinir a senha da sua conta no {app_name}.\n"
            "Abra o link abaixo para escolher uma nova senha. Ele vale por {minutes} minutos:\n\n"
            "{link}\n\n"
            "Se você não pediu isso, ignore esta mensagem: sua senha continua a mesma.\n"
        ),
    },
}


def template_language(language: object) -> str:
    """The template a reader's language maps to.

    The stored preference is free-form JSON, so a number or a list is not a
    language and must not raise while composing: only existing accounts would
    see that error (ADR 0044).
    """
    return (
        "pt" if isinstance(language, str) and language.lower().startswith("pt") else "en"
    )


def _fields_in(text: str) -> Counter[str]:
    """The placeholders a template uses, and how often, refusing anything
    `.format` would surprise."""
    fields: Counter[str] = Counter()
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise TemplateError(f"Unbalanced braces: {exc}") from exc
    for _literal, field, format_spec, conversion in parsed:
        if field is None:
            continue
        if not field:
            raise TemplateError("Empty placeholder: name the value, as in {link}")
        if conversion or format_spec:
            raise TemplateError(f"Placeholder {{{field}}} takes no conversion or format")
        if field not in TEMPLATE_FIELDS:
            raise TemplateError(
                f"Unknown placeholder {{{field}}}; use only "
                + ", ".join(f"{{{name}}}" for name in sorted(TEMPLATE_FIELDS))
            )
        fields[field] += 1
    return fields


def has_url(text: str) -> bool:
    """Whether the text holds an address of any shape (see URL_PATTERN)."""
    return URL_PATTERN.search(text) is not None


def _link_is_alone_on_its_line(body: str) -> bool:
    """The one `{link}` the body may carry sits on a line of its own.

    Anywhere else — inside a sentence, at the end of one, or wrapped in a URL
    written around it — the text decides where the reader's click goes, and
    the token inside it follows.
    """
    return sum(1 for line in body.splitlines() if line.strip() == "{link}") == 1


def _as_sent(text: str, link: str = CHECK_LINK) -> str:
    """The text with the placeholders filled in, as the reader would get it.

    `link` is what `{link}` stands for while the text is read, so a check that
    must not see the recovery address itself passes an empty one: only then is
    an address written into the text by hand found rather than taken for the
    placeholder.
    """
    return text.format(**{**CHECK_VALUES, "link": link})


def has_line_break(text: str) -> bool:
    """Whether the text holds any character that ends or separates a line."""
    return any(unicodedata.category(character) in LINE_BREAK_CATEGORIES for character in text)


def _reject_unstorable_text(data: object) -> None:
    """Refuse a lone surrogate before it is saved or put in an error message.

    JSON may hold an unpaired surrogate (``"\\ud800"``), and Python keeps it in
    the string, but nothing downstream does: the JSON response cannot encode
    it and neither can the database, so the request would end as a 500. It is
    checked here, over every string in the payload, so the language, the field
    names and the placeholders are covered as well as the text.

    The walk keeps its own stack: a payload nested deeper than the interpreter
    allows must be answered as a bad template, not crash the request.
    """
    pending = [data]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise TemplateError("Email templates must be valid UTF-8 text") from exc
        elif isinstance(item, dict):
            for key, value in item.items():
                pending.append(key)
                pending.append(value)
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def validate_templates(data: object) -> dict[str, dict[str, str]]:
    """Check a set of templates and return it, or raise TemplateError.

    A language may be left out (it keeps the built-in text), but what is sent
    for a language must be complete and safe to format.
    """
    _reject_unstorable_text(data)
    if not isinstance(data, dict):
        raise TemplateError("Email templates must be an object keyed by language")
    for language in data:
        if language not in TEMPLATE_LANGUAGES:
            raise TemplateError(
                f"Unknown language in email templates: {language}; use "
                + ", ".join(TEMPLATE_LANGUAGES)
            )
    checked: dict[str, dict[str, str]] = {}
    for language, template in data.items():
        if not isinstance(template, dict):
            raise TemplateError(f"The {language} template must be an object")
        unknown = set(template) - {"subject", "body"}
        if unknown:
            raise TemplateError(
                f"The {language} template has unknown fields: "
                + ", ".join(sorted(unknown))
            )
        subject = template.get("subject")
        body = template.get("body")
        if not isinstance(subject, str) or not subject.strip():
            raise TemplateError(f"The {language} template needs a subject")
        # The subject becomes a header: a line break there makes every send fail.
        if has_line_break(subject):
            raise TemplateError(f"The {language} subject must be a single line")
        if not isinstance(body, str) or not body.strip():
            raise TemplateError(f"The {language} template needs a body")
        if len(subject) > SUBJECT_MAX_LENGTH:
            raise TemplateError(
                f"The {language} subject is longer than {SUBJECT_MAX_LENGTH} characters"
            )
        if len(body) > BODY_MAX_LENGTH:
            raise TemplateError(
                f"The {language} body is longer than {BODY_MAX_LENGTH} characters"
            )
        subject_fields = _fields_in(subject)
        # The subject is a header, not a body: a link there is not the one line
        # the reader clicks, and it is not checked the way the body is.
        if subject_fields["link"]:
            raise TemplateError(f"The {language} subject must not contain {{link}}")
        if has_url(_as_sent(subject)):
            raise TemplateError(f"The {language} subject must not contain a link")
        body_fields = _fields_in(body)
        if body_fields["link"] == 0:
            raise TemplateError(f"The {language} body must contain {{link}}")
        if body_fields["link"] > 1:
            raise TemplateError(f"The {language} body must contain {{link}} only once")
        if not _link_is_alone_on_its_line(body):
            raise TemplateError(
                f"In the {language} body, {{link}} must be alone on its own line"
            )
        # The recovery link itself is the only address the message may carry,
        # so the placeholder stands for nothing while the rest is read: an
        # address typed into the text by hand is still there to be found.
        if has_url(_as_sent(body, link="")):
            raise TemplateError(
                f"The {language} body must not contain a link other than {{link}}"
            )
        checked[language] = {"subject": subject, "body": body}
    return checked


def parse_templates(raw: str) -> dict[str, dict[str, str]]:
    """Read the stored JSON, or raise TemplateError.

    A value edited by hand may nest deeper than the parser's stack; that is a
    bad template like any other, not an error that reaches the reader, which
    would make a known address answer differently from an unknown one.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, RecursionError) as exc:
        raise TemplateError("Email templates are not valid JSON") from exc
    return validate_templates(data)


def effective_templates(
    saved: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """What would be sent: the saved template per language, else the built-in one."""
    saved = saved or {}
    return {
        language: dict(saved.get(language) or DEFAULT_TEMPLATES[language])
        for language in TEMPLATE_LANGUAGES
    }


def render_template(
    template: dict[str, str],
    *,
    app_name: str,
    link: str,
    minutes: int,
) -> tuple[str, str]:
    """Fill the placeholders, returning the subject and the body."""
    values = {"app_name": app_name, "link": link, "minutes": minutes}
    return template["subject"].format(**values), template["body"].format(**values)


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
    language: object,
    token_minutes: int,
    *,
    app_name: str = "Securo",
    templates: dict[str, dict[str, str]] | None = None,
) -> Email:
    """The recovery message in the account's language."""
    template = effective_templates(templates)[template_language(language)]
    subject, text = render_template(
        template, app_name=app_name, link=link, minutes=token_minutes
    )
    return Email(to=to, subject=subject, text=text)


def _httpx_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


async def send_email(message: Email, settings: Settings | None = None) -> None:
    """Hand the message to the provider behind the switch.

    Raises EmailDisabled when the switch is off. A provider that refuses the
    message raises too; the caller decides what the person sees.
    """
    settings = settings or get_settings()
    if not settings.email_enabled:
        raise EmailDisabled("Email is not enabled on this instance")
    if settings.email_provider == "resend":
        await _send_via_api(settings, message)
    else:
        await anyio.to_thread.run_sync(_send_now, settings, message)


async def deliver(message: Email, settings: Settings | None = None) -> None:
    """Send after the response is on its way, logging a refusal instead of raising.

    The forgot-password answer must not depend on the provider: a slow or
    refusing server would delay (or fail) only the known addresses, and the
    difference alone tells anyone which accounts exist.
    """
    try:
        await send_email(message, settings)
    except Exception as exc:
        # Operator-only, and without the token, the link or the server's reply.
        # An SMTPDataError keeps the relay's response text, which some relays
        # quote from the message, and an API error body can echo the message
        # too, so neither the exception text nor a traceback is written: just
        # the class and the numeric status.
        status = getattr(exc, "smtp_code", None)
        if status is not None:
            detail = f", SMTP {status}"
        elif isinstance(exc, httpx.HTTPStatusError):
            detail = f", HTTP {exc.response.status_code}"
        else:
            detail = ""
        logger.warning(
            "Could not send the password recovery email to %s (%s%s)",
            message.to,
            type(exc).__name__,
            detail,
        )


async def _send_via_api(settings: Settings, message: Email) -> None:
    """POST the message to the provider's send endpoint."""
    url = f"{settings.resend_api_url.rstrip('/')}/emails"
    payload = {
        "from": settings.email_from,
        "to": [message.to],
        "subject": message.subject,
        "text": message.text,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    async with _httpx_client(API_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()


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

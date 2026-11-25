"""Password recovery by email (#540, ADR 0044).

The relay is mocked: nothing leaves the test process. What is checked is what
the person sees (503 with the switch off, the same answer for a known and an
unknown address) and that the link in the sent message really changes the
password.
"""

import re

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.models.user import User
from app.services import email_service

TOKEN_IN_LINK = re.compile(r"reset-password\?token=([A-Za-z0-9._~-]+)")


@pytest.fixture
def email_off():
    """The shipped default: no mail, and the route says so."""
    settings = get_settings()
    previous = settings.email_enabled
    settings.email_enabled = False
    yield settings
    settings.email_enabled = previous


@pytest.fixture
def sent_emails(monkeypatch):
    """Email on, with the SMTP hand-off replaced by a list."""
    settings = get_settings()
    previous = (
        settings.email_enabled,
        settings.frontend_url,
        settings.smtp_host,
        settings.email_from,
        settings.password_reset_token_minutes,
    )
    settings.email_enabled = True
    settings.frontend_url = "https://app.example.com"
    settings.smtp_host = "smtp.example.com"
    settings.email_from = "Securo <no-reply@example.com>"
    settings.password_reset_token_minutes = 60

    sent: list[email_service.Email] = []

    async def _fake_send(message, _settings=None):
        sent.append(message)

    monkeypatch.setattr(email_service, "send_email", _fake_send)
    yield sent, settings

    (
        settings.email_enabled,
        settings.frontend_url,
        settings.smtp_host,
        settings.email_from,
        settings.password_reset_token_minutes,
    ) = previous


async def _ask_for_link(client: AsyncClient, email: str) -> None:
    response = await client.post("/api/auth/forgot-password", json={"email": email})
    assert response.status_code == 202
    assert response.json() is None


def _token_from(message: email_service.Email) -> str:
    match = TOKEN_IN_LINK.search(message.text)
    assert match, "the sent message has no reset-password link"
    return match.group(1)


@pytest.mark.asyncio
async def test_forgot_password_answers_503_with_the_switch_off(
    client: AsyncClient, test_user: User, email_off
):
    response = await client.post("/api/auth/forgot-password", json={"email": "test@example.com"})
    assert response.status_code == 503
    assert "not enabled" in response.json()["detail"]


@pytest.mark.asyncio
async def test_forgot_password_does_not_reveal_whether_an_account_exists(
    client: AsyncClient, test_user: User, sent_emails
):
    sent, _settings = sent_emails

    await _ask_for_link(client, "test@example.com")
    assert len(sent) == 1

    await _ask_for_link(client, "nobody@example.com")
    # No second message, and the caller cannot tell the two apart.
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_recovery_link_sets_a_new_password(client: AsyncClient, test_user: User, sent_emails):
    sent, _settings = sent_emails
    await _ask_for_link(client, "test@example.com")

    message = sent[0]
    assert message.to == "test@example.com"
    # The account speaks Portuguese, so the message does too.
    assert "Redefinir sua senha" in message.subject
    assert message.text.startswith("Olá")
    token = _token_from(message)

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "brandnewpass1"},
    )
    assert response.status_code == 200, response.text

    new_login = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "brandnewpass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert new_login.status_code == 200

    old_login = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert old_login.status_code == 400


@pytest.mark.asyncio
async def test_account_without_a_stored_language_gets_the_english_message(
    client: AsyncClient, test_user: User, sent_emails, session
):
    """A null preference is not a language: fall back instead of failing.

    Only known addresses reach this path, so an exception here would answer
    500 for them and 202 for everyone else, which is the enumeration oracle
    the identical answer exists to avoid.
    """
    sent, _settings = sent_emails
    test_user.preferences = {**(test_user.preferences or {}), "language": None}
    session.add(test_user)
    await session.commit()

    await _ask_for_link(client, "test@example.com")

    assert len(sent) == 1
    assert sent[0].subject == "Reset your Securo password"
    assert sent[0].text.startswith("Hi")


@pytest.mark.asyncio
async def test_relay_connection_verifies_the_certificate(monkeypatch):
    """smtplib's own STARTTLS default skips the certificate check."""
    import smtplib
    import ssl

    settings = get_settings()
    monkeypatch.setattr(settings, "email_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_username", "")
    contexts: list[ssl.SSLContext | None] = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.context = context
            if context is not None:
                contexts.append(context)

        def starttls(self, context=None):
            self.starttls_context = context
            if context is not None:
                contexts.append(context)

        def login(self, username, password):
            pass

        def send_message(self, message):
            pass

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)

    message = email_service.Email(to="someone@example.com", subject="s", text="t")

    settings.smtp_security = "ssl"
    await email_service.send_email(message, settings)
    settings.smtp_security = "starttls"
    await email_service.send_email(message, settings)

    # One for SMTP_SSL, one handed to starttls — neither may be omitted.
    assert len(contexts) == 2
    for context in contexts:
        assert isinstance(context, ssl.SSLContext)
        # Without these, anything on the path can stand in for the relay.
        assert context.check_hostname is True
        assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.asyncio
async def test_expired_recovery_token_is_refused(client: AsyncClient, test_user: User, sent_emails):
    sent, settings = sent_emails
    # A link that was already past its lifetime when it went out.
    settings.password_reset_token_minutes = -1
    await _ask_for_link(client, "test@example.com")
    token = _token_from(sent[0])

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "brandnewpass1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"


@pytest.mark.asyncio
async def test_reset_password_checks_the_new_password(
    client: AsyncClient, test_user: User, sent_emails
):
    sent, _settings = sent_emails
    await _ask_for_link(client, "test@example.com")
    token = _token_from(sent[0])

    response = await client.post(
        "/api/auth/reset-password", json={"token": token, "password": "short"}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "RESET_PASSWORD_INVALID_PASSWORD"
    assert "between 8 and 72" in detail["reason"]


@pytest.mark.asyncio
async def test_relay_failure_keeps_the_answer_unchanged(
    client: AsyncClient, test_user: User, sent_emails, monkeypatch, caplog
):
    """A refused message must not turn into a 500 for known addresses only."""

    async def _refuse(message, _settings=None):
        raise OSError("relay refused the message")

    monkeypatch.setattr(email_service, "send_email", _refuse)
    response = await client.post("/api/auth/forgot-password", json={"email": "test@example.com"})
    assert response.status_code == 202
    # Logged for the operator, without the token or the link.
    assert "Could not send the password recovery email" in caplog.text
    assert "reset-password?token=" not in caplog.text


@pytest.mark.asyncio
async def test_relay_refusal_logs_the_status_and_never_the_reply(
    client: AsyncClient, test_user: User, sent_emails, monkeypatch, caplog
):
    """An SMTP refusal keeps the server's reply, which can quote the message.

    Some relays answer DATA with the body they rejected, so writing the
    exception or its traceback would put a live token in the log.
    """
    import smtplib

    async def _refuse(message, _settings=None):
        raise smtplib.SMTPDataError(
            550, b"rejected <https://app.example.com/reset-password?token=leaked-token>"
        )

    monkeypatch.setattr(email_service, "send_email", _refuse)
    response = await client.post("/api/auth/forgot-password", json={"email": "test@example.com"})

    assert response.status_code == 202
    assert "SMTPDataError" in caplog.text
    assert "SMTP 550" in caplog.text
    assert "leaked-token" not in caplog.text


def test_email_settings_refuse_half_a_configuration():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(email_enabled=True, smtp_host="smtp.example.com", email_from="")

    configured = Settings(
        email_enabled=True,
        smtp_host="smtp.example.com",
        smtp_username="relay-user",
    )
    # The sender falls back to the SMTP username.
    assert configured.email_from == ""
    assert configured.password_reset_token_minutes == 60

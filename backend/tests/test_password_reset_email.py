"""Password recovery by email (#540, ADR 0041).

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
    response = await client.post(
        "/api/auth/forgot-password", json={"email": "test@example.com"}
    )
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
async def test_recovery_link_sets_a_new_password(
    client: AsyncClient, test_user: User, sent_emails
):
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
async def test_expired_recovery_token_is_refused(
    client: AsyncClient, test_user: User, sent_emails
):
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
    response = await client.post(
        "/api/auth/forgot-password", json={"email": "test@example.com"}
    )
    assert response.status_code == 202
    # Logged for the operator, without the token or the link.
    assert "Could not send the password recovery email" in caplog.text
    assert "reset-password?token=" not in caplog.text


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

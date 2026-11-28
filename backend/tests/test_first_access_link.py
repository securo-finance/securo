"""An account the operator opens gets a link to set its own password (#1967).

The relay is mocked: nothing leaves the test process. The link lands on the
recovery screen, works once, expires, and never reaches a log.
"""
from __future__ import annotations

import logging
import re

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import User
from app.services import email_service

TOKEN = "operator-secret-token"
URL = "/api/operator/accounts"
TOKEN_IN_LINK = re.compile(r"reset-password\?token=([A-Za-z0-9._~-]+)")


@pytest.fixture(autouse=True)
def _clean(clean_db):
    """Each test starts from an empty database."""


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    monkeypatch.setattr(get_settings(), "frontend_url", "https://app.example.com")
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def sent(monkeypatch):
    """Email on, with the hand-off to the relay replaced by a list."""
    settings = get_settings()
    monkeypatch.setattr(settings, "email_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_from", "Securo <no-reply@example.com>")
    messages: list[email_service.Email] = []

    async def _fake_send(message, _settings=None):
        messages.append(message)

    monkeypatch.setattr(email_service, "send_email", _fake_send)
    return messages


def _body(**overrides) -> dict:
    body = {
        "email": "ana@example.com",
        "external_reference": "crm-0042",
        "language": "pt-BR",
        "workspaces": [{"name": "Casa", "kind": "personal", "default_currency": "BRL"}],
    }
    return body | overrides


def _token(link_or_text: str) -> str:
    match = TOKEN_IN_LINK.search(link_or_text)
    assert match, "no reset-password link"
    return match.group(1)


async def _set_password(client: AsyncClient, token: str, password: str = "brandnewpass1"):
    return await client.post("/api/auth/reset-password", json={"token": token, "password": password})


async def _login(client: AsyncClient, password: str = "brandnewpass1"):
    return await client.post(
        "/api/auth/login",
        data={"username": "ana@example.com", "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@pytest.mark.asyncio
async def test_the_message_carries_a_link_that_sets_the_first_password(
    operator, sent, client: AsyncClient, session: AsyncSession
):
    resp = await client.post(URL, json=_body(), headers=operator)
    assert resp.status_code == 201, resp.text
    assert resp.json()["first_access_link"] is None

    [message] = sent
    assert message.to == "ana@example.com"
    # The account speaks Portuguese, so the message does too.
    assert "está pronta" in message.subject
    assert "72 horas" in message.text
    assert "https://app.example.com/reset-password?token=" in message.text

    assert (await _login(client)).status_code == 400
    assert (await _set_password(client, _token(message.text))).status_code == 200
    assert (await _login(client)).status_code == 200

    user = await session.scalar(select(User).where(User.email == "ana@example.com"))
    await session.refresh(user)
    assert user.password_set_at is not None
    # The link reached the mailbox.
    assert user.is_verified is True


@pytest.mark.parametrize(
    ("minutes", "language", "says"),
    [
        (4320, "en", "valid for 72 hours:"),
        (60, "en", "valid for 1 hour:"),
        (30, "en", "valid for 30 minutes:"),
        (90, "en", "valid for 90 minutes:"),
        (1, "en", "valid for 1 minute:"),
        (120, "pt-BR", "vale por 2 horas:"),
        (45, "pt-BR", "vale por 45 minutos:"),
    ],
)
def test_the_message_states_the_lifetime_the_link_has(minutes, language, says):
    # A lifetime that is not whole hours is said in minutes, never rounded.
    message = email_service.first_access_email("ana@example.com", "https://x", language, minutes)
    assert says in message.text


@pytest.mark.asyncio
async def test_the_link_works_once(operator, sent, client: AsyncClient):
    await client.post(URL, json=_body(), headers=operator)
    token = _token(sent[0].text)
    assert (await _set_password(client, token)).status_code == 200

    again = await _set_password(client, token, "someoneelse99")
    assert again.status_code == 400
    assert again.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_an_expired_link_is_refused(monkeypatch, operator, sent, client: AsyncClient):
    # Already past its lifetime when it went out.
    monkeypatch.setattr(get_settings(), "first_access_token_minutes", -1)
    await client.post(URL, json=_body(), headers=operator)

    resp = await _set_password(client, _token(sent[0].text))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"


@pytest.mark.asyncio
async def test_with_email_off_the_call_is_refused_before_anything_is_written(
    operator, client: AsyncClient, session: AsyncSession
):
    resp = await client.post(URL, json=_body(), headers=operator)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "email_disabled"
    assert await session.scalar(select(User).where(User.email == "ana@example.com")) is None


@pytest.mark.asyncio
async def test_the_link_in_the_response_works_and_stays_out_of_the_logs(
    operator, client: AsyncClient, caplog
):
    caplog.set_level(logging.DEBUG)
    resp = await client.post(URL, json=_body(first_access="response"), headers=operator)
    assert resp.status_code == 201, resp.text
    link = resp.json()["first_access_link"]
    token = _token(link)

    assert token not in caplog.text
    assert (await _set_password(client, token)).status_code == 200
    assert token not in caplog.text
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_recovery_still_works_and_spends_the_first_access_link(
    operator, sent, client: AsyncClient
):
    await client.post(URL, json=_body(), headers=operator)
    first_access = _token(sent[0].text)
    # A person who lost the first message can still recover the account...
    await client.post("/api/auth/forgot-password", json={"email": "ana@example.com"})
    recovery = _token(sent[1].text)
    assert recovery != first_access
    assert (await _set_password(client, recovery)).status_code == 200
    # ...and the first-access link stops working once a password is set.
    assert (await _set_password(client, first_access, "otherpass123")).status_code == 400
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_resend_sends_again_until_a_password_is_set(operator, sent, client: AsyncClient):
    user_id = (await client.post(URL, json=_body(), headers=operator)).json()["user_id"]
    resend = f"{URL}/{user_id}/first-access-link"

    first = await client.post(resend, headers=operator)
    assert first.status_code == 202
    assert len(sent) == 2
    assert (await _set_password(client, _token(sent[1].text))).status_code == 200

    # Same answer, nothing sent, once the account has its password.
    after = await client.post(resend, headers=operator)
    assert after.status_code == 202
    assert after.json() == first.json()
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_resend_in_the_response_refuses_an_account_with_a_password(operator, client: AsyncClient):
    opened = (await client.post(URL, json=_body(first_access="response"), headers=operator)).json()
    resend = f"{URL}/{opened['user_id']}/first-access-link"

    resp = await client.post(resend, json={"delivery": "response"}, headers=operator)
    assert resp.status_code == 202
    assert (await _set_password(client, _token(resp.json()["link"]))).status_code == 200

    spent = await client.post(resend, json={"delivery": "response"}, headers=operator)
    assert spent.status_code == 409
    assert spent.json()["detail"]["code"] == "password_already_set"


@pytest.mark.asyncio
async def test_a_retry_with_the_same_reference_sends_nothing_again(operator, sent, client: AsyncClient):
    assert (await client.post(URL, json=_body(), headers=operator)).status_code == 201
    retry = await client.post(URL, json=_body(), headers=operator)
    assert retry.status_code == 200
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_resend_is_operator_only(operator, client: AsyncClient, test_user, admin_auth_headers):
    resend = f"{URL}/{test_user.id}/first-access-link"
    assert (await client.post(resend, headers=admin_auth_headers)).status_code == 403
    wrong = {"Authorization": "Bearer wrong"}
    assert (await client.post(resend, json={"delivery": "response"}, headers=wrong)).status_code == 403

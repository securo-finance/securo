"""Outbound email: the provider behind the switch, and the admin's template (#1885).

The relay and the API are mocked, so nothing leaves the test process. What is
checked is the request each provider builds, that the one switch keeps both
quiet, and that a saved template is what the recipient reads.
"""

import json
import re

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.admin_access_log import AdminAccessLog
from app.models.user import User
from app.services import admin_service, email_service

TOKEN_IN_LINK = re.compile(r"reset-password\?token=([A-Za-z0-9._~-]+)")

# The body every test may save: `{link}` alone on a line of its own, with no
# other address in the text.
VALID_BODY = "Open the app:\n\n{link}\n\nIt lasts {minutes} minutes."


@pytest.fixture
def email_on():
    """The switch on, with an SMTP relay and an API key configured."""
    settings = get_settings()
    previous = (
        settings.email_enabled,
        settings.email_provider,
        settings.frontend_url,
        settings.smtp_host,
        settings.email_from,
        settings.password_reset_token_minutes,
        settings.resend_api_key,
        settings.resend_api_url,
    )
    settings.email_enabled = True
    settings.email_provider = "smtp"
    settings.frontend_url = "https://app.example.com"
    settings.smtp_host = "smtp.example.com"
    settings.email_from = "Securo <no-reply@example.com>"
    settings.password_reset_token_minutes = 60
    settings.resend_api_key = SecretStr("test-key")
    settings.resend_api_url = "https://api.resend.example"
    yield settings
    (
        settings.email_enabled,
        settings.email_provider,
        settings.frontend_url,
        settings.smtp_host,
        settings.email_from,
        settings.password_reset_token_minutes,
        settings.resend_api_key,
        settings.resend_api_url,
    ) = previous


@pytest.fixture
def sent(monkeypatch, email_on):
    """The composed messages instead of the network."""
    messages: list[email_service.Email] = []

    async def _keep(message, _settings=None):
        messages.append(message)

    monkeypatch.setattr(email_service, "send_email", _keep)
    return messages


def _api_client(monkeypatch, handler):
    """Point the provider at a fake API instead of the network."""
    calls: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        email_service,
        "_httpx_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(_handle), timeout=timeout
        ),
    )
    return calls


def _message() -> email_service.Email:
    return email_service.Email(
        to="ana@example.com", subject="Reset your password", text="Open the link."
    )


# The provider called behind the switch


@pytest.mark.asyncio
async def test_the_relay_is_the_default_provider(monkeypatch, email_on):
    handed: list[email_service.Email] = []
    monkeypatch.setattr(
        email_service, "_send_now", lambda _settings, message: handed.append(message)
    )

    await email_service.send_email(_message())

    assert handed == [_message()]


@pytest.mark.asyncio
async def test_the_api_provider_posts_the_message_with_the_key(monkeypatch, email_on):
    email_on.email_provider = "resend"
    calls = _api_client(monkeypatch, lambda _request: httpx.Response(200, json={"id": "abc"}))

    await email_service.send_email(_message())

    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "https://api.resend.example/emails"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "from": "Securo <no-reply@example.com>",
        "to": ["ana@example.com"],
        "subject": "Reset your password",
        "text": "Open the link.",
    }


@pytest.mark.asyncio
async def test_the_api_provider_raises_when_it_refuses(monkeypatch, email_on):
    email_on.email_provider = "resend"
    _api_client(monkeypatch, lambda _request: httpx.Response(422, json={"message": "no"}))

    with pytest.raises(httpx.HTTPStatusError):
        await email_service.send_email(_message())


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["smtp", "resend"])
async def test_nothing_leaves_while_the_switch_is_off(monkeypatch, provider):
    settings = get_settings()
    monkeypatch.setattr(settings, "email_enabled", False)
    monkeypatch.setattr(settings, "email_provider", provider)

    def explode(*_args, **_kwargs):
        raise AssertionError("no provider may be reached while the switch is off")

    monkeypatch.setattr(email_service, "_send_now", explode)
    monkeypatch.setattr(email_service, "_httpx_client", explode)

    with pytest.raises(email_service.EmailDisabled):
        await email_service.send_email(_message())


@pytest.mark.asyncio
async def test_a_refused_message_is_logged_without_its_text(monkeypatch, email_on, caplog):
    email_on.email_provider = "resend"
    _api_client(monkeypatch, lambda _request: httpx.Response(422, json={"message": "no"}))

    with caplog.at_level("WARNING"):
        await email_service.deliver(_message())

    assert "Could not send the password recovery email" in caplog.text
    assert "HTTP 422" in caplog.text
    assert "Open the link." not in caplog.text


# Configuration refuses a half-configured provider at boot


def _settings(**values) -> Settings:
    return Settings(_env_file=None, _secrets_dir=None, **values)


def test_the_api_provider_needs_a_key_at_boot():
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        _settings(
            email_enabled=True,
            email_provider="resend",
            email_from="Securo <no-reply@example.com>",
        )


def test_the_api_provider_needs_a_sender_at_boot():
    with pytest.raises(ValidationError, match="EMAIL_FROM"):
        _settings(
            email_enabled=True,
            email_provider="resend",
            resend_api_key=SecretStr("key"),
        )


def test_the_switch_off_needs_nothing():
    settings = _settings(email_enabled=False, email_provider="resend")
    assert settings.email_enabled is False


def test_the_api_address_must_be_https():
    with pytest.raises(ValidationError, match="RESEND_API_URL"):
        _settings(
            email_enabled=True,
            email_provider="resend",
            resend_api_key=SecretStr("key"),
            email_from="Securo <no-reply@example.com>",
            resend_api_url="http://api.example.com",
        )


def test_the_api_address_cannot_be_empty_for_the_hosted_provider():
    with pytest.raises(ValidationError, match="RESEND_API_URL"):
        _settings(
            email_enabled=True,
            email_provider="resend",
            resend_api_key=SecretStr("key"),
            email_from="Securo <no-reply@example.com>",
            resend_api_url="",
        )


def test_the_api_address_is_ignored_by_the_relay():
    settings = _settings(
        email_enabled=True,
        email_provider="smtp",
        smtp_host="smtp.example.com",
        email_from="Securo <no-reply@example.com>",
        resend_api_url="http://api.example.com",
    )

    assert settings.email_provider == "smtp"


def test_the_relay_still_needs_a_host_at_boot():
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        _settings(email_enabled=True, email_provider="smtp")


def test_the_provider_is_one_of_the_two():
    with pytest.raises(ValidationError):
        _settings(email_provider="carrier-pigeon")


def test_a_refused_setup_never_prints_the_secrets():
    """The error is what a container that failed to boot logs.

    A model-level validator reports the input it refused, and that input is
    every field the instance was built from: the email key and SECRET_KEY were
    printed there, truncated but legible.
    """
    key = "re_live_0123456789ABCDEFGHIJKLMNOP"
    secret = "0123456789abcdef0123456789abcdef"
    cases = {
        "no sender": {"email_from": ""},
        "no address": {"email_from": "no-reply@example.com", "resend_api_url": ""},
        "address in the clear": {
            "email_from": "no-reply@example.com",
            "resend_api_url": "http://api.example.com",
        },
    }
    for name, extra in cases.items():
        with pytest.raises(ValidationError) as caught:
            _settings(
                email_enabled=True,
                email_provider="resend",
                resend_api_key=SecretStr(key),
                secret_key=secret,
                **extra,
            )
        message = str(caught.value)
        assert key not in message, name
        assert key[-8:] not in message, name
        assert secret[:8] not in message, name
        assert secret[-8:] not in message, name


# The templates: what is allowed, what falls back


def test_a_valid_template_is_accepted():
    templates = email_service.validate_templates(
        {
            "en": {
                "subject": "{app_name} password",
                "body": "Open the app:\n\n{link}\n\nIt lasts {minutes} minutes.",
            }
        }
    )
    assert templates["en"]["subject"] == "{app_name} password"


def test_an_unknown_placeholder_is_refused():
    with pytest.raises(email_service.TemplateError, match="secret"):
        email_service.validate_templates(
            {"en": {"subject": "Reset", "body": "Open {link}, code {secret}."}}
        )


def test_a_body_without_the_link_is_refused():
    with pytest.raises(email_service.TemplateError, match="link"):
        email_service.validate_templates(
            {"en": {"subject": "Reset", "body": "Open the app within {minutes} minutes."}}
        )


# The recovery link is the only thing the message may lead to, and it goes
# alone on its own line: anywhere else, the text around it decides where the
# reader's click goes, and the token inside it follows.
FOREIGN_LINK_TEMPLATES = [
    pytest.param(
        {"subject": "Reset", "body": "https://evil.example/c?u={link}"},
        "alone",
        id="wrapped-in-a-foreign-url",
    ),
    pytest.param(
        {"subject": "Reset", "body": "evil.example/{link}"},
        "alone",
        id="foreign-host-without-a-scheme",
    ),
    pytest.param(
        {"subject": "Reset", "body": "Open:\n\n{link}\n\nAgain: {link}"},
        "only once",
        id="the-link-twice",
    ),
    pytest.param(
        {"subject": "Reset", "body": "Open {link} now."},
        "alone",
        id="inside-a-sentence",
    ),
    pytest.param(
        {"subject": "Reset {link}", "body": VALID_BODY},
        "subject",
        id="in-the-subject",
    ),
    pytest.param(
        {
            "subject": "Reset",
            "body": "Open the app:\n\n{link}\n\nOr write to https://evil.example/.",
        },
        "other than",
        id="a-second-address-beside-the-link",
    ),
    pytest.param(
        {"subject": "Reset", "body": "Open:\n\n{link}\n\nHelp: reset{minutes}.com"},
        "other than",
        id="an-address-assembled-from-the-values",
    ),
    pytest.param(
        {
            "subject": "Reset",
            "body": "{link}\n\nhttps://securo.example/reset-password?token=check",
        },
        "other than",
        id="a-sample-address-typed-into-the-body",
    ),
]


@pytest.mark.parametrize("template, reason", FOREIGN_LINK_TEMPLATES)
def test_a_message_that_leads_away_from_the_link_is_refused(template, reason):
    with pytest.raises(email_service.TemplateError, match=reason):
        email_service.validate_templates({"en": template})


@pytest.mark.asyncio
@pytest.mark.parametrize("template, reason", FOREIGN_LINK_TEMPLATES)
async def test_the_same_templates_answer_400_and_save_nothing(
    template, reason, client: AsyncClient, admin_auth_headers, email_on
):
    response = await client.put(
        "/api/admin/email",
        json={"templates": {"en": template}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400, response.text
    assert reason in response.json()["detail"]
    state = await client.get("/api/admin/email", headers=admin_auth_headers)
    assert state.json()["templates"] == email_service.DEFAULT_TEMPLATES


@pytest.mark.asyncio
async def test_a_stored_template_that_leads_away_falls_back_to_the_built_in_text(
    client: AsyncClient, test_user: User, session, sent
):
    """A row written straight into the database is read, not trusted: the
    reader gets the built-in text instead of an error or a foreign link."""
    # Stored in the reader's own language (pt): a template saved under another
    # language would be ignored anyway, and the test would pass with the bug.
    await admin_service.set_app_setting(
        session,
        admin_service.EMAIL_TEMPLATES_KEY,
        json.dumps(
            {
                "pt": {
                    "subject": "Reset",
                    "body": "https://evil.example/c?u={link}",
                }
            }
        ),
    )
    try:
        response = await client.post(
            "/api/auth/forgot-password", json={"email": "test@example.com"}
        )

        assert response.status_code == 202
        assert len(sent) == 1
        assert "evil.example" not in sent[0].text
        assert sent[0].subject == email_service.DEFAULT_TEMPLATES["pt"]["subject"].format(
            app_name="Securo"
        )
    finally:
        await admin_service.delete_app_setting(session, admin_service.EMAIL_TEMPLATES_KEY)


def test_a_stray_brace_is_refused():
    with pytest.raises(email_service.TemplateError):
        email_service.validate_templates(
            {"en": {"subject": "Reset", "body": "Open {link} and this }."}}
        )


def test_a_language_the_email_is_not_written_in_is_refused():
    with pytest.raises(email_service.TemplateError, match="es"):
        email_service.validate_templates(
            {"es": {"subject": "Restablecer", "body": "Abre {link} en {minutes} minutos."}}
        )


def test_templates_that_are_not_json_are_refused():
    with pytest.raises(email_service.TemplateError):
        email_service.parse_templates("{not json")


@pytest.mark.parametrize(
    "line_break",
    ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
    ids=["VT", "FF", "FS", "GS", "RS", "NEL", "LS", "PS"],
)
def test_every_line_break_python_knows_is_refused_in_a_subject(line_break):
    """\\n and \\r are not the only ones: the SMTP library refuses the rest too,
    after the admin has already saved them."""
    with pytest.raises(email_service.TemplateError, match="single line"):
        email_service.validate_templates(
            {"en": {"subject": f"Reset{line_break}password", "body": "Open {link}."}}
        )


def test_an_unpaired_surrogate_is_refused():
    """JSON may hold one, but neither the response nor the database can encode
    it; accepted here, it answers 500 after the switch already said 202."""
    template = json.loads(
        '{"en": {"subject": "Reset", "body": "Open \\ud800 {link}."}}'
    )

    with pytest.raises(email_service.TemplateError, match="UTF-8"):
        email_service.validate_templates(template)


def test_an_unpaired_surrogate_in_a_language_is_refused_before_it_is_named():
    """The message naming an unknown language cannot encode the language."""
    template = json.loads('{"\\ud800": {"subject": "Reset", "body": "Open {link}."}}')

    with pytest.raises(email_service.TemplateError, match="UTF-8"):
        email_service.validate_templates(template)


def test_templates_nested_past_the_parser_stack_are_a_bad_template():
    """A row edited by hand must not turn a known address's answer into a 500."""
    nested = "[" * 200_000 + "]" * 200_000

    with pytest.raises(email_service.TemplateError):
        email_service.parse_templates(nested)


def test_a_language_without_a_template_keeps_the_built_in_text():
    effective = email_service.effective_templates(
        {"en": {"subject": "Mine", "body": "Open {link}."}}
    )
    assert effective["en"]["subject"] == "Mine"
    assert effective["pt"] == email_service.DEFAULT_TEMPLATES["pt"]


def test_the_link_arrives_in_the_message_the_person_would_read():
    subject, body = email_service.render_template(
        {"subject": "{app_name} reset", "body": "Open {link} within {minutes} minutes."},
        app_name="Securo",
        link="https://app.example.com/reset-password?token=abc",
        minutes=60,
    )
    assert subject == "Securo reset"
    assert "https://app.example.com/reset-password?token=abc" in body
    assert "within 60 minutes" in body


# The admin endpoint and the message it changes


@pytest.mark.asyncio
async def test_only_an_admin_reads_or_writes_it(client: AsyncClient, viewer_auth_headers):
    assert (
        await client.get("/api/admin/email", headers=viewer_auth_headers)
    ).status_code == 403
    assert (
        await client.put(
            "/api/admin/email", json={"templates": {}}, headers=viewer_auth_headers
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_state_names_the_provider_in_use(client: AsyncClient, admin_auth_headers, email_on):
    email_on.email_provider = "resend"

    response = await client.get("/api/admin/email", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["provider"] == "resend"
    assert body["sender"] == "Securo <no-reply@example.com>"
    assert set(body["templates"]) == {"en", "pt"}
    assert body["templates"]["en"] == email_service.DEFAULT_TEMPLATES["en"]


@pytest.mark.asyncio
async def test_an_invalid_template_answers_400(client: AsyncClient, admin_auth_headers, email_on):
    response = await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Reset", "body": "Open {link} with {secret}."}}},
        headers=admin_auth_headers,
    )
    assert response.status_code == 400
    assert "secret" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_answers_400(client: AsyncClient, admin_auth_headers, email_on):
    response = await client.put(
        "/api/admin/email",
        content="{not json",
        headers={**admin_auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_empty_body_answers_400_instead_of_clearing(
    client: AsyncClient, admin_auth_headers, email_on
):
    """Clearing is explicit ({"templates": {}}); a client that sends nothing
    must not wipe the templates that are in use."""
    await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Mine", "body": VALID_BODY}}},
        headers=admin_auth_headers,
    )
    empty = await client.put(
        "/api/admin/email",
        content=b"",
        headers={**admin_auth_headers, "Content-Type": "application/json"},
    )
    assert empty.status_code == 400
    state = await client.get("/api/admin/email", headers=admin_auth_headers)
    assert state.json()["templates"]["en"]["subject"] == "Mine"


@pytest.mark.asyncio
async def test_a_subject_with_a_line_break_answers_400(
    client: AsyncClient, admin_auth_headers, email_on
):
    response = await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Reset\npassword", "body": "Open {link}."}}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    assert "single line" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line_break",
    ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
    ids=["VT", "FF", "FS", "GS", "RS", "NEL", "LS", "PS"],
)
async def test_a_subject_with_any_line_break_answers_400(
    client: AsyncClient, admin_auth_headers, email_on, line_break
):
    response = await client.put(
        "/api/admin/email",
        json={
            "templates": {
                "en": {"subject": f"Reset{line_break}password", "body": "Open {link}."}
            }
        },
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    assert "single line" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_subject_with_an_unpaired_surrogate_answers_400(
    client: AsyncClient, admin_auth_headers, email_on
):
    """It cannot be stored or echoed back; accepted, it answered 500."""
    response = await client.put(
        "/api/admin/email",
        content='{"templates": {"en": {"subject": "Reset", "body": "Open \\ud800 {link}."}}}',
        headers={**admin_auth_headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_body_nested_past_the_parser_stack_answers_400(
    client: AsyncClient, admin_auth_headers, email_on
):
    response = await client.put(
        "/api/admin/email",
        content=b"[" * 200_000 + b"]" * 200_000,
        headers={**admin_auth_headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_empty_object_answers_400_instead_of_clearing(
    client: AsyncClient, admin_auth_headers, email_on
):
    """Clearing is {"templates": {}}; {} forgot the field and must not wipe
    the templates that are in use."""
    await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Mine", "body": VALID_BODY}}},
        headers=admin_auth_headers,
    )

    empty = await client.put("/api/admin/email", json={}, headers=admin_auth_headers)

    assert empty.status_code == 400
    state = await client.get("/api/admin/email", headers=admin_auth_headers)
    assert state.json()["templates"]["en"]["subject"] == "Mine"


@pytest.mark.asyncio
async def test_clearing_the_templates_returns_to_the_built_in_text(
    client: AsyncClient, admin_auth_headers, email_on
):
    await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Mine", "body": VALID_BODY}}},
        headers=admin_auth_headers,
    )
    cleared = await client.put(
        "/api/admin/email", json={"templates": {}}, headers=admin_auth_headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["templates"] == email_service.DEFAULT_TEMPLATES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        '{"en": {"subject": "Reset\\u2028password", "body": "Open {link}."}}',
        '{"en": {"subject": "Reset", "body": "Open \\ud800 {link}."}}',
        "[" * 200_000 + "]" * 200_000,
    ],
    ids=["hidden-line-break", "unpaired-surrogate", "nested-too-deep"],
)
async def test_a_template_edited_by_hand_falls_back_instead_of_breaking_the_send(
    stored, client: AsyncClient, test_user: User, session, sent
):
    """A row edited straight in the database must not make a known address
    answer 202 and receive nothing."""
    await admin_service.set_app_setting(
        session, admin_service.EMAIL_TEMPLATES_KEY, stored
    )
    try:
        response = await client.post(
            "/api/auth/forgot-password", json={"email": "test@example.com"}
        )

        assert response.status_code == 202
        assert len(sent) == 1
        assert sent[0].subject == email_service.DEFAULT_TEMPLATES["pt"]["subject"].format(
            app_name="Securo"
        )
    finally:
        await admin_service.delete_app_setting(session, admin_service.EMAIL_TEMPLATES_KEY)


@pytest.mark.asyncio
async def test_the_saved_template_is_what_the_recipient_reads(
    client: AsyncClient, admin_auth_headers, test_user: User, sent
):
    saved = await client.put(
        "/api/admin/email",
        json={
            "templates": {
                "pt": {
                    "subject": "{app_name}: nova senha",
                    "body": "Abra o link abaixo:\n\n{link}\n\nO link vale {minutes} minutos.",
                }
            }
        },
        headers=admin_auth_headers,
    )
    assert saved.status_code == 200, saved.text

    response = await client.post(
        "/api/auth/forgot-password", json={"email": "test@example.com"}
    )
    assert response.status_code == 202

    assert len(sent) == 1
    message = sent[0]
    assert message.subject == "Securo: nova senha"
    assert "O link vale 60 minutos." in message.text
    link = re.search(r"https://app\.example\.com/reset-password\?token=\S+", message.text)
    assert link, "the rendered body lost the link"
    assert TOKEN_IN_LINK.search(message.text)


@pytest.mark.asyncio
async def test_a_language_without_a_saved_template_keeps_the_built_in_text(
    client: AsyncClient, admin_auth_headers, test_user: User, sent
):
    await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Mine", "body": VALID_BODY}}},
        headers=admin_auth_headers,
    )

    await client.post("/api/auth/forgot-password", json={"email": "test@example.com"})

    assert sent[0].subject == "Redefinir sua senha do Securo"
    assert sent[0].text.startswith("Olá")


# What the save leaves behind (#605)


async def _log_entries(session) -> list[AdminAccessLog]:
    result = await session.execute(
        select(AdminAccessLog).order_by(AdminAccessLog.created_at)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_saving_the_templates_leaves_a_record_of_who_and_what(
    client: AsyncClient, admin_auth_headers, session, email_on
):
    """The route that decides where the recovery link goes now leaves a trail.

    Before #605 this was the one administrative mutation with no record: a
    superuser could rewrite the message, and with it the only way back into an
    account, and nothing said who did it or what the text had been.
    """
    saved = await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "New password", "body": VALID_BODY}}},
        headers=admin_auth_headers,
    )
    assert saved.status_code == 200, saved.text

    entries = await _log_entries(session)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "settings.email_templates"
    assert entry.actor_email == "admin@example.com"
    assert entry.target_user_id is None
    assert entry.target_email == ""
    assert entry.ip
    assert entry.created_at is not None
    assert entry.changes == {
        "templates": {
            "en": {
                "from": None,  # nothing saved before: the built-in text
                "to": {"subject": "New password", "body": VALID_BODY},
            }
        }
    }

    # The provider's key is configuration, never part of what is recorded.
    assert email_on.resend_api_key.get_secret_value() not in json.dumps(entry.changes)
    assert email_on.resend_api_key.get_secret_value() not in entry.reason


@pytest.mark.asyncio
async def test_clearing_the_templates_is_recorded_with_what_it_replaced(
    client: AsyncClient, admin_auth_headers, session, email_on
):
    saved = {"subject": "New password", "body": VALID_BODY}
    await client.put(
        "/api/admin/email", json={"templates": {"en": saved}}, headers=admin_auth_headers
    )
    cleared = await client.put(
        "/api/admin/email", json={"templates": {}}, headers=admin_auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["templates"] == email_service.DEFAULT_TEMPLATES

    entries = await _log_entries(session)
    assert len(entries) == 2
    assert entries[1].changes == {"templates": {"en": {"from": saved, "to": None}}}


@pytest.mark.asyncio
async def test_an_unchanged_save_says_so_instead_of_inventing_a_change(
    client: AsyncClient, admin_auth_headers, session, email_on
):
    body = {"templates": {"en": {"subject": "New password", "body": VALID_BODY}}}
    await client.put("/api/admin/email", json=body, headers=admin_auth_headers)
    again = await client.put("/api/admin/email", json=body, headers=admin_auth_headers)
    assert again.status_code == 200, again.text

    entries = await _log_entries(session)
    assert len(entries) == 2
    assert entries[1].changes is None


@pytest.mark.asyncio
async def test_a_refused_template_saves_nothing_and_leaves_no_record(
    client: AsyncClient, admin_auth_headers, session, email_on
):
    response = await client.put(
        "/api/admin/email",
        json={"templates": {"en": {"subject": "Reset", "body": "Open {link} with {secret}."}}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    assert await _log_entries(session) == []


@pytest.mark.asyncio
async def test_a_field_the_templates_do_not_take_never_reaches_the_record(
    client: AsyncClient, admin_auth_headers, session, email_on
):
    """Only the subject and the body are stored, so nothing else a caller sends
    can end up in a record an administrator later reads."""
    response = await client.put(
        "/api/admin/email",
        json={
            "templates": {
                "en": {"subject": "Reset", "body": VALID_BODY, "api_key": "re_live_secret"}
            }
        },
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    assert await _log_entries(session) == []
    state = await client.get("/api/admin/email", headers=admin_auth_headers)
    assert state.json()["templates"] == email_service.DEFAULT_TEMPLATES


@pytest.mark.asyncio
async def test_the_save_and_its_record_commit_together(
    client: AsyncClient, admin_auth_headers, session, email_on, monkeypatch
):
    """A record that cannot be written takes the save with it: one commit."""

    async def _fail(*args, **kwargs):
        raise RuntimeError("the access log is unavailable")

    monkeypatch.setattr(admin_service, "_record_instance_access", _fail)

    with pytest.raises(RuntimeError):
        await client.put(
            "/api/admin/email",
            json={"templates": {"en": {"subject": "New password", "body": VALID_BODY}}},
            headers=admin_auth_headers,
        )

    assert await admin_service.get_email_templates(session) == {}

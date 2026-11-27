"""The operator opens an account under the restricted role (#1965, ADR 0043).

Several workspaces in one transaction: each one's rows have to land while the
session is bound to it, and a refusal has to take all of them back.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.category import Category
from app.models.user import User
from app.models.workspace import Workspace
from tests.test_row_security_postgres import stack  # noqa: F401  (fixture)

TOKEN = "operator-secret-token"


def _body(email: str, reference: str, workspaces: list[dict]) -> dict:
    return {"email": email, "external_reference": reference, "language": "pt-BR", "workspaces": workspaces}


PERSONAL = {"name": "Casa", "kind": "personal", "default_currency": "BRL"}
BUSINESS = {
    "name": "Ana Design", "kind": "business", "default_currency": "BRL", "legal_name": "Ana Design Ltda",
    "tax_jurisdiction": "BR", "tax_ids": [{"kind": "cnpj", "value": "11.222.333/0001-81"}],
}


async def test_the_operator_opens_an_account_under_row_security(stack, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "managed_instance", True)
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))

    async def sessions():
        async with stack.app() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, sessions)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        made = await client.post(
            "/api/operator/accounts", headers=headers,
            json=_body("ana@example.com", "crm-1", [PERSONAL, BUSINESS]) | {"quotas": {"max_business_workspaces": 1}},
        )
        assert made.status_code == 201, made.text
        again = await client.post(
            "/api/operator/accounts", headers=headers, json=_body("ana@example.com", "crm-1", [PERSONAL]),
        )
        assert again.status_code == 200 and again.json()["workspaces"] == made.json()["workspaces"]
        over = await client.post(
            "/api/operator/accounts", headers=headers,
            json=_body("bia@example.com", "crm-2", [PERSONAL, BUSINESS, BUSINESS])
            | {"quotas": {"max_business_workspaces": 1}},
        )
        assert over.status_code == 403 and over.json()["detail"]["code"] == "quota_exceeded"

    async with stack.owner() as session:
        for workspace in made.json()["workspaces"]:
            wallets = await session.scalar(
                select(func.count()).select_from(Account).where(Account.workspace_id == workspace["id"])
            )
            categories = await session.scalar(
                select(func.count()).select_from(Category).where(Category.workspace_id == workspace["id"])
            )
            assert (wallets, categories > 0) == (1, True), workspace
        assert await session.scalar(select(User).where(User.email == "bia@example.com")) is None
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 2

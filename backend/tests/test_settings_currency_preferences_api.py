from datetime import date
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.fx_rate import FxRate
from app.models.user import User

BASE = "/api/settings/currency-preferences"


async def _seed_rate(session: AsyncSession, code: str, rate: str, on: date | None = None):
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency=code,
            date=on or app_today(),
            rate=Decimal(rate),
            source="test",
        )
    )
    await session.commit()


async def test_reads_the_stored_display_currency(client: AsyncClient, auth_headers: dict):
    # `test_user` ships with currency_display=BRL and no shortlist.
    r = await client.get(BASE, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"currency_display": "BRL", "quick_currencies": ["BRL"]}


async def test_falls_back_to_the_instance_default(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    test_user.preferences = None
    session.add(test_user)
    await session.commit()

    r = await client.get(BASE, headers=auth_headers)
    assert r.json() == {"currency_display": "USD", "quick_currencies": ["USD"]}


async def test_requires_authentication(client: AsyncClient):
    assert (await client.get(BASE)).status_code == 401
    assert (await client.put(f"{BASE}/display", json={"currency": "EUR"})).status_code == 401
    assert (await client.put(f"{BASE}/quick-list", json={"currencies": ["EUR"]})).status_code == 401


async def test_switch_display_currency(client: AsyncClient, auth_headers: dict, session: AsyncSession):
    await _seed_rate(session, "EUR", "0.9")

    r = await client.put(f"{BASE}/display", json={"currency": "eur"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["currency_display"] == "EUR"


async def test_switch_to_usd_needs_no_stored_rate(client: AsyncClient, auth_headers: dict):
    # USD is the cross-rate base, so it resolves without an fx_rates row.
    r = await client.put(f"{BASE}/display", json={"currency": "USD"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["currency_display"] == "USD"


async def test_switch_accepts_a_rate_from_another_date(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    # A stale rate still converts honestly, so the switch is allowed.
    await _seed_rate(session, "EUR", "0.9", on=date(2020, 1, 1))

    r = await client.put(f"{BASE}/display", json={"currency": "EUR"}, headers=auth_headers)
    assert r.status_code == 200


async def test_switch_rejected_without_an_fx_rate(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/display", json={"currency": "GBP"}, headers=auth_headers)
    assert r.status_code == 409
    assert "GBP" in r.json()["detail"]

    after = await client.get(BASE, headers=auth_headers)
    assert after.json()["currency_display"] == "BRL"


async def test_unsupported_display_currency_rejected(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/display", json={"currency": "ZZZ"}, headers=auth_headers)
    assert r.status_code == 400
    assert "ZZZ" in r.json()["detail"]


async def test_blank_display_currency_rejected(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/display", json={"currency": "  "}, headers=auth_headers)
    assert r.status_code == 400


async def test_quick_list_normalizes_and_dedupes(client: AsyncClient, auth_headers: dict):
    r = await client.put(
        f"{BASE}/quick-list", json={"currencies": ["eur", "EUR", " brl "]}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["quick_currencies"] == ["EUR", "BRL"]


async def test_quick_list_gains_the_display_currency(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/quick-list", json={"currencies": ["EUR"]}, headers=auth_headers)
    assert r.status_code == 200
    # BRL is appended so the active currency keeps a chip.
    assert r.json()["quick_currencies"] == ["EUR", "BRL"]


async def test_quick_list_capped_at_six(client: AsyncClient, auth_headers: dict):
    seven = ["USD", "EUR", "GBP", "BRL", "CAD", "AUD", "CHF"]
    r = await client.put(f"{BASE}/quick-list", json={"currencies": seven}, headers=auth_headers)
    assert r.status_code == 400
    assert "6" in r.json()["detail"]


async def test_quick_list_rejects_unsupported(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/quick-list", json={"currencies": ["EUR", "ZZZ"]}, headers=auth_headers)
    assert r.status_code == 400


async def test_empty_quick_list_reads_back_as_the_display_currency(client: AsyncClient, auth_headers: dict):
    r = await client.put(f"{BASE}/quick-list", json={"currencies": []}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["quick_currencies"] == ["BRL"]


async def test_display_stays_reachable_after_switching_outside_the_list(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    await _seed_rate(session, "EUR", "0.9")
    await client.put(f"{BASE}/quick-list", json={"currencies": ["GBP", "BRL"]}, headers=auth_headers)

    r = await client.put(f"{BASE}/display", json={"currency": "EUR"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["quick_currencies"] == ["GBP", "BRL", "EUR"]


async def test_full_quick_list_does_not_grow_for_the_display_currency(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    # At the cap the shortlist wins over reachability, leaving the active
    # currency without a chip. Asserted so the trade-off stays deliberate.
    await _seed_rate(session, "EUR", "0.9")
    six = ["GBP", "CAD", "AUD", "CHF", "JPY", "USD"]
    await client.put(f"{BASE}/quick-list", json={"currencies": six}, headers=auth_headers)

    r = await client.put(f"{BASE}/display", json={"currency": "EUR"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["quick_currencies"] == six

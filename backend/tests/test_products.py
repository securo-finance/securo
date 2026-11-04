"""The catalog and the one rule it lives by: a line owns its values.

Products fill lines; they never own them. So the tests below care most
about what happens at the edges: a product renamed, archived or deleted
after an invoice named it, a term written before a product went away,
and an id that belongs to somebody else's workspace.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.product import Product
from app.models.workspace import WorkspaceMember
from app.services import invoice_schedule_service as schedules
from app.services import invoice_service
from app.services import product_service as svc
from app.services.invoice_service import InvoiceError

TODAY = date(2026, 9, 23)


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Estudio", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def ws_id(business_ws) -> uuid.UUID:
    return uuid.UUID(business_ws["id"])


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


async def make_product(session, ws_id, user_id, **overrides) -> Product:
    data = {
        "name": "Consulting hour",
        "unit": "h",
        "prices": [{"currency": "USD", "unit_price": "200.00"}],
    }
    data.update(overrides)
    product = await svc.create_product(session, ws_id, user_id, data)
    await session.commit()
    return product


# ---------------------------------------------------------------------------
# The catalog itself
# ---------------------------------------------------------------------------
class TestCatalog:
    @pytest.mark.asyncio
    async def test_a_product_is_created_with_its_prices(self, session, ws_id, test_user):
        p = await make_product(
            session, ws_id, test_user.id,
            prices=[
                {"currency": "USD", "unit_price": "200.00", "tax_rate": "10"},
                {"currency": "eur", "unit_price": "180.00", "billing": "recurring", "interval": "monthly", "nickname": "Monthly"},
            ],
        )
        assert p.kind == "service" and p.active
        assert [(x.currency, x.unit_price, x.billing, x.interval) for x in p.prices] == [
            ("USD", Decimal("200.00"), "one_time", None),
            ("EUR", Decimal("180.00"), "recurring", "monthly"),
        ]

    @pytest.mark.asyncio
    async def test_a_recurring_price_needs_a_cadence_and_a_one_time_price_drops_it(self, session, ws_id, test_user):
        with pytest.raises(InvoiceError) as exc:
            await make_product(session, ws_id, test_user.id, prices=[{"currency": "USD", "unit_price": "1", "billing": "recurring"}])
        assert exc.value.code == "interval_required"
        p = await make_product(session, ws_id, test_user.id, prices=[{"currency": "USD", "unit_price": "1", "billing": "one_time", "interval": "monthly"}])
        assert p.prices[0].interval is None

    @pytest.mark.asyncio
    async def test_prices_are_validated(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        for bad, code in (
            ({"unit_price": "1"}, "currency_required"),
            ({"currency": "USD"}, "unit_price_required"),
            ({"currency": "USD", "unit_price": "-1"}, "negative_price"),
            ({"currency": "USD", "unit_price": "1", "billing": "weird"}, "invalid_billing"),
        ):
            with pytest.raises(InvoiceError) as exc:
                await svc.add_price(session, p, bad)
            assert exc.value.code == code

    @pytest.mark.asyncio
    async def test_imported_rows_converge_on_their_external_id(self, session, ws_id, test_user):
        with pytest.raises(InvoiceError) as exc:
            await make_product(session, ws_id, test_user.id, origin="imported")
        assert exc.value.code == "external_source_required"
        p = await make_product(
            session, ws_id, test_user.id, origin="imported", external_source="gateway", external_id="prod_1",
            prices=[{"currency": "USD", "unit_price": "10", "external_source": "gateway", "external_id": "price_1"}],
        )
        found = await svc.find_by_external_id(session, ws_id, "gateway", "prod_1")
        assert found is not None and found.id == p.id
        with pytest.raises(InvoiceError) as exc:
            await make_product(session, ws_id, test_user.id, origin="imported", external_source="gateway", external_id="prod_1", prices=[])
        assert exc.value.code == "already_imported" and exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_list_defaults_to_live_products_and_searches_by_name(self, session, ws_id, test_user):
        a = await make_product(session, ws_id, test_user.id, name="Zeta hosting", description="servers")
        b = await make_product(session, ws_id, test_user.id, name="Alpha design")
        await svc.update_product(session, b, {"active": False})
        await session.commit()
        assert [p.id for p in await svc.list_products(session, ws_id)] == [a.id]
        assert {p.id for p in await svc.list_products(session, ws_id, active=None)} == {a.id, b.id}
        assert [p.id for p in await svc.list_products(session, ws_id, active=None, q="alpha")] == [b.id]
        assert [p.id for p in await svc.list_products(session, ws_id, q="SERVERS")] == [a.id]

    @pytest.mark.asyncio
    async def test_price_for_prefers_a_live_one_time_price_in_the_currency(self, session, ws_id, test_user):
        p = await make_product(
            session, ws_id, test_user.id,
            prices=[
                {"currency": "USD", "unit_price": "1", "billing": "recurring", "interval": "monthly"},
                {"currency": "USD", "unit_price": "2"},
                {"currency": "EUR", "unit_price": "3"},
            ],
        )
        def amount(currency: str) -> Decimal | None:
            price = svc.price_for(p, currency)
            return price.unit_price if price else None

        assert amount("usd") == Decimal("2.00")
        assert amount("EUR") == Decimal("3.00")
        assert amount("BRL") is None
        await svc.update_price(session, p, p.prices[1], {"active": False})
        assert amount("USD") == Decimal("1.00")


# ---------------------------------------------------------------------------
# The bridge to invoice lines
# ---------------------------------------------------------------------------
async def an_invoice(session, ws_id, user_id, lines) -> Invoice:
    invoice = await invoice_service.create_invoice(
        session, ws_id, user_id,
        {"issue_date": TODAY, "due_date": date(2026, 10, 8), "currency": "USD", "lines": lines},
    )
    await session.commit()
    return invoice


class TestLines:
    @pytest.mark.asyncio
    async def test_a_line_remembers_its_product_and_keeps_its_own_values(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        price = p.prices[0]
        inv = await an_invoice(session, ws_id, test_user.id, [
            {"description": "Consulting hour", "quantity": 3, "unit": "h", "unit_price": "180.00",
             "product_id": p.id, "price_id": price.id},
            {"description": "Travel", "unit_price": "50.00"},
        ])
        catalog, free = inv.lines
        assert catalog.product_id == p.id and catalog.price_id == price.id
        assert catalog.unit_price == Decimal("180.00")  # the typed value, not the catalog's 200
        assert free.product_id is None and free.price_id is None
        assert inv.total == Decimal("590.00")

        # Renaming and archiving the product changes nothing on the line.
        await svc.update_product(session, p, {"name": "Senior hour", "active": False})
        await session.commit()
        await session.refresh(catalog)
        assert catalog.description == "Consulting hour" and catalog.product_id == p.id

    @pytest.mark.asyncio
    async def test_a_price_alone_names_its_product(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        inv = await an_invoice(session, ws_id, test_user.id, [
            {"description": "x", "unit_price": "1", "price_id": p.prices[0].id},
        ])
        assert inv.lines[0].product_id == p.id

    @pytest.mark.asyncio
    async def test_ids_from_another_workspace_or_another_product_are_refused(self, session, ws_id, test_user, client, auth_headers):
        resp = await client.post(
            "/api/workspaces", headers=auth_headers,
            json={"name": "Outra", "kind": "business", "self_membership": True},
        )
        other_ws = uuid.UUID(resp.json()["id"])
        foreign = await make_product(session, other_ws, test_user.id)
        mine = await make_product(session, ws_id, test_user.id)

        with pytest.raises(InvoiceError) as exc:
            await an_invoice(session, ws_id, test_user.id, [{"description": "x", "unit_price": "1", "product_id": foreign.id}])
        assert exc.value.code == "product_not_found"
        with pytest.raises(InvoiceError) as exc:
            await an_invoice(session, ws_id, test_user.id, [{"description": "x", "unit_price": "1", "product_id": mine.id, "price_id": foreign.prices[0].id}])
        assert exc.value.code == "price_not_found"
        with pytest.raises(InvoiceError) as exc:
            await an_invoice(session, ws_id, test_user.id, [{"description": "x", "unit_price": "1", "product_id": uuid.uuid4()}])
        assert exc.value.code == "product_not_found"

    @pytest.mark.asyncio
    async def test_a_named_product_is_archived_not_deleted(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        price = p.prices[0]
        await an_invoice(session, ws_id, test_user.id, [{"description": "x", "unit_price": "1", "product_id": p.id, "price_id": price.id}])
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_product(session, p)
        assert exc.value.code == "product_in_use"
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_price(session, p, price)
        assert exc.value.code == "price_in_use"
        assert (await svc.usage_counts(session, ws_id, [p.id]))[p.id] == 1

        unused = await make_product(session, ws_id, test_user.id, name="Unused")
        await svc.delete_price(session, unused, unused.prices[0])
        await svc.delete_product(session, unused)
        assert await svc.get_product(session, unused.id, ws_id) is None


class TestFiscalRefs:
    @pytest.mark.asyncio
    async def test_refs_are_cleaned_and_any_key_is_accepted(self, session, ws_id, test_user):
        p = await make_product(
            session, ws_id, test_user.id,
            fiscal_refs={"NCM": " 8471.30.12 ", "service_code": "", "my_own_key": "x", "gtin": None},
        )
        assert p.fiscal_refs == {"ncm": "8471.30.12", "my_own_key": "x"}
        await svc.update_product(session, p, {"fiscal_refs": {}})
        assert p.fiscal_refs is None

    @pytest.mark.asyncio
    async def test_a_key_that_is_not_a_key_is_refused(self, session, ws_id, test_user):
        for bad in ({"has space": "1"}, {"": "1"}, {"a" * 41: "1"}, ["ncm"], {"ncm": "x" * 101}):
            with pytest.raises(InvoiceError) as exc:
                await make_product(session, ws_id, test_user.id, fiscal_refs=bad)
            assert exc.value.code == "invalid_fiscal_refs"

    @pytest.mark.asyncio
    async def test_the_line_takes_a_copy_and_keeps_its_own(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id, fiscal_refs={"service_code": "1.05", "nbs": "1.1401"})
        inv = await an_invoice(session, ws_id, test_user.id, [
            {"description": "Hour", "unit_price": "200", "product_id": p.id},
            {"description": "Hour, own refs", "unit_price": "200", "price_id": p.prices[0].id, "fiscal_refs": {"service_code": "1.07"}},
            {"description": "Typed", "unit_price": "1"},
        ])
        copied, own, typed = inv.lines
        assert copied.fiscal_refs == {"service_code": "1.05", "nbs": "1.1401"}
        assert own.fiscal_refs == {"service_code": "1.07"} and own.product_id == p.id
        assert typed.fiscal_refs is None
        # Changing the product later changes nothing on the line.
        await svc.update_product(session, p, {"fiscal_refs": {"service_code": "9.99"}})
        await session.commit()
        await session.refresh(copied)
        assert copied.fiscal_refs == {"service_code": "1.05", "nbs": "1.1401"}

    @pytest.mark.asyncio
    async def test_the_pack_suggests_by_kind_and_never_restricts(self, client, biz_headers, auth_headers, business_ws):
        resp = await client.patch(f"/api/workspaces/{business_ws['id']}", headers=biz_headers, json={"tax_jurisdiction": "BR"})
        assert resp.status_code == 200, resp.text
        fields = (await client.get("/api/fiscal/product-fields", headers=biz_headers)).json()
        assert fields["jurisdiction"] == "BR"
        by_key = {f["key"]: f for f in fields["fields"]}
        assert by_key["ncm"]["kinds"] == ["product"] and by_key["service_code"]["kinds"] == ["service"]
        assert by_key["ncm"]["label_key"] == "fiscal.productField.ncm"
        # A key the pack never mentions is stored all the same.
        resp = await client.post("/api/products", headers=biz_headers, json={"name": "x", "fiscal_refs": {"hs_code": "8471"}})
        assert resp.status_code == 201 and resp.json()["fiscal_refs"] == {"hs_code": "8471"}
        # No jurisdiction: no opinion, not an error.
        await client.patch(f"/api/workspaces/{business_ws['id']}", headers=biz_headers, json={"tax_jurisdiction": None})
        fields = (await client.get("/api/fiscal/product-fields", headers=biz_headers)).json()
        assert fields == {"jurisdiction": None, "fields": []}


class TestLookupKeys:
    @pytest.mark.asyncio
    async def test_a_lookup_key_finds_a_price_and_is_unique_per_workspace(self, session, ws_id, test_user, client, auth_headers):
        # Read before the refused add below rolls the session back and
        # expires every loaded row.
        uid = test_user.id
        p = await make_product(session, ws_id, uid, prices=[{"currency": "USD", "unit_price": "10", "lookup_key": "pro_monthly"}])
        found = await svc.find_price_by_lookup_key(session, ws_id, "pro_monthly")
        assert found is not None and found.id == p.prices[0].id
        with pytest.raises(InvoiceError) as exc:
            await svc.add_price(session, p, {"currency": "EUR", "unit_price": "9", "lookup_key": "pro_monthly"})
        assert exc.value.code == "price_key_taken" and exc.value.status_code == 409
        # Another workspace may use the same key.
        resp = await client.post("/api/workspaces", headers=auth_headers, json={"name": "Outra", "kind": "business", "self_membership": True})
        other = await make_product(session, uuid.UUID(resp.json()["id"]), uid, prices=[{"currency": "USD", "unit_price": "1", "lookup_key": "pro_monthly"}])
        assert other.prices[0].lookup_key == "pro_monthly"

    @pytest.mark.asyncio
    async def test_a_cleared_lookup_key_is_null_not_empty(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id, prices=[{"currency": "USD", "unit_price": "10", "lookup_key": "k"}])
        await svc.update_price(session, p, p.prices[0], {"lookup_key": "  "})
        assert p.prices[0].lookup_key is None
        q = await make_product(session, ws_id, test_user.id, prices=[{"currency": "USD", "unit_price": "10", "lookup_key": ""}])
        assert q.prices[0].lookup_key is None


# ---------------------------------------------------------------------------
# Recurring agreements
# ---------------------------------------------------------------------------
class TestSchedules:
    @pytest.mark.asyncio
    async def test_a_term_carries_the_product_into_every_period(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        s = await schedules.create_schedule(
            session, ws_id, test_user.id,
            {"name": "Retainer", "frequency": "monthly", "start_date": date(2026, 10, 5), "currency": "USD",
             "lines": [{"description": "Consulting hour", "quantity": 10, "unit_price": "200", "product_id": p.id, "price_id": p.prices[0].id}]},
            today=TODAY,
        )
        await session.commit()
        assert s.terms[0].lines[0]["product_id"] == str(p.id)
        [inv] = await schedules.generate_due(session, s, today=date(2026, 10, 5))
        assert inv.lines[0].product_id == p.id and inv.lines[0].price_id == p.prices[0].id

    @pytest.mark.asyncio
    async def test_a_product_deleted_after_the_term_does_not_stop_the_agreement(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        s = await schedules.create_schedule(
            session, ws_id, test_user.id,
            {"name": "Retainer", "frequency": "monthly", "start_date": date(2026, 10, 5), "currency": "USD",
             "lines": [{"description": "Hour", "unit_price": "200", "product_id": p.id}]},
            today=TODAY,
        )
        await session.commit()
        await session.delete(p)
        await session.commit()
        [inv] = await schedules.generate_due(session, s, today=date(2026, 10, 5))
        assert inv.lines[0].product_id is None and inv.total == Decimal("200.00")

    @pytest.mark.asyncio
    async def test_a_term_refuses_bad_fiscal_refs_when_written_not_when_emitted(self, session, ws_id, test_user):
        """A bad key on a term would fail every period until the job paused
        the agreement. It is refused at the door, and a good one is stored
        clean."""
        with pytest.raises(InvoiceError) as exc:
            await schedules.create_schedule(
                session, ws_id, test_user.id,
                {"name": "R", "frequency": "monthly", "start_date": date(2026, 10, 5), "currency": "USD",
                 "lines": [{"description": "Hour", "unit_price": "200", "fiscal_refs": {"bad key": "1"}}]},
                today=TODAY,
            )
        assert exc.value.code == "invalid_fiscal_refs"
        s = await schedules.create_schedule(
            session, ws_id, test_user.id,
            {"name": "R", "frequency": "monthly", "start_date": date(2026, 10, 5), "currency": "USD",
             "lines": [{"description": "Hour", "unit_price": "200", "fiscal_refs": {"Service_Code": " 1.05 ", "empty": ""}}]},
            today=TODAY,
        )
        await session.commit()
        assert s.terms[0].lines[0]["fiscal_refs"] == {"service_code": "1.05"}
        [inv] = await schedules.generate_due(session, s, today=date(2026, 10, 5))
        assert inv.lines[0].fiscal_refs == {"service_code": "1.05"}

    @pytest.mark.asyncio
    async def test_make_recurring_keeps_the_provenance(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        inv = await an_invoice(session, ws_id, test_user.id, [{"description": "Hour", "unit_price": "200", "product_id": p.id}])
        s = await schedules.make_recurring(session, inv, test_user.id, {"frequency": "monthly", "name": "R"}, today=TODAY)
        assert s.terms[0].lines[0]["product_id"] == str(p.id)


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def personal_headers(session: AsyncSession, auth_headers, test_user) -> dict:
    from app.models.workspace import Workspace

    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id, Workspace.kind == "personal")
        .limit(1)
    )
    return {**auth_headers, "X-Workspace-Id": str(result.scalar_one().id)}


@pytest_asyncio.fixture
async def viewer_headers(session: AsyncSession, client: AsyncClient, business_ws) -> dict:
    import bcrypt

    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="viewer-products@example.com",
        hashed_password=bcrypt.hashpw(b"viewerpass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=uuid.UUID(business_ws["id"]), user_id=user.id, role="viewer"))
    await session.commit()
    resp = await client.post(
        "/api/auth/login",
        data={"username": "viewer-products@example.com", "password": "viewerpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}", "X-Workspace-Id": business_ws["id"]}


PAYLOAD = {"name": "Consulting hour", "unit": "h", "prices": [{"currency": "USD", "unit_price": "200.00"}]}


@pytest.mark.asyncio
async def test_personal_workspace_cannot_reach_the_catalog(client: AsyncClient, personal_headers):
    fake = str(uuid.uuid4())
    routes = [
        ("get", "/api/products", None),
        ("post", "/api/products", PAYLOAD),
        ("get", f"/api/products/{fake}", None),
        ("patch", f"/api/products/{fake}", {"name": "x"}),
        ("delete", f"/api/products/{fake}", None),
        ("post", f"/api/products/{fake}/prices", {"currency": "USD", "unit_price": "1"}),
        ("patch", f"/api/products/{fake}/prices/{fake}", {"active": False}),
        ("delete", f"/api/products/{fake}/prices/{fake}", None),
    ]
    for method, url, body in routes:
        resp = await getattr(client, method)(url, headers=personal_headers, **({"json": body} if body else {}))
        assert resp.status_code == 404, f"{method.upper()} {url} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_viewer_reads_and_never_writes(client: AsyncClient, biz_headers, viewer_headers):
    created = (await client.post("/api/products", headers=biz_headers, json=PAYLOAD)).json()
    pid, price_id = created["id"], created["prices"][0]["id"]
    assert (await client.get("/api/products", headers=viewer_headers)).status_code == 200
    assert (await client.get(f"/api/products/{pid}", headers=viewer_headers)).status_code == 200
    writes = [
        ("post", "/api/products", PAYLOAD),
        ("patch", f"/api/products/{pid}", {"name": "x"}),
        ("delete", f"/api/products/{pid}", None),
        ("post", f"/api/products/{pid}/prices", {"currency": "EUR", "unit_price": "1"}),
        ("patch", f"/api/products/{pid}/prices/{price_id}", {"active": False}),
        ("delete", f"/api/products/{pid}/prices/{price_id}", None),
    ]
    for method, url, body in writes:
        resp = await getattr(client, method)(url, headers=viewer_headers, **({"json": body} if body else {}))
        assert resp.status_code == 403, f"{method.upper()} {url} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_catalog_journey_over_http(client: AsyncClient, biz_headers, auth_headers):
    resp = await client.post("/api/products", headers=biz_headers, json=PAYLOAD)
    assert resp.status_code == 201, resp.text
    product = resp.json()
    assert product["kind"] == "service" and product["active"] and product["invoice_count"] == 0
    assert product["prices"][0]["unit_price"] == "200.00"
    pid = product["id"]

    # A second price, then a bad one.
    resp = await client.post(f"/api/products/{pid}/prices", headers=biz_headers, json={"currency": "EUR", "unit_price": "180", "billing": "recurring", "interval": "monthly"})
    assert resp.status_code == 201 and len(resp.json()["prices"]) == 2
    resp = await client.post(f"/api/products/{pid}/prices", headers=biz_headers, json={"currency": "EUR", "unit_price": "1", "billing": "recurring"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "interval_required"

    # An invoice from the catalog counts on the product and refuses deletion.
    resp = await client.post(
        "/api/invoices", headers=biz_headers,
        json={"currency": "USD", "lines": [{"description": "Consulting hour", "quantity": 2, "unit_price": "200.00", "product_id": pid, "price_id": product["prices"][0]["id"]}]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["lines"][0]["product_id"] == pid
    assert (await client.get(f"/api/products/{pid}", headers=biz_headers)).json()["invoice_count"] == 1
    resp = await client.delete(f"/api/products/{pid}", headers=biz_headers)
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "product_in_use"

    # Archived: gone from the default list, back with active=false.
    resp = await client.patch(f"/api/products/{pid}", headers=biz_headers, json={"active": False})
    assert resp.status_code == 200 and resp.json()["active"] is False
    assert (await client.get("/api/products", headers=biz_headers)).json() == []
    assert [p["id"] for p in (await client.get("/api/products", headers=biz_headers, params={"active": "false"})).json()] == [pid]

    # Another workspace never sees it.
    other = await client.post("/api/workspaces", headers=auth_headers, json={"name": "Outra", "kind": "business", "self_membership": True})
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    assert (await client.get(f"/api/products/{pid}", headers=other_headers)).status_code == 404
    resp = await client.post("/api/invoices", headers=other_headers, json={"currency": "USD", "lines": [{"description": "x", "unit_price": "1", "product_id": pid}]})
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "product_not_found"

    # Unused products simply go.
    resp = await client.post("/api/products", headers=biz_headers, json={"name": "Nothing yet"})
    assert (await client.delete(f"/api/products/{resp.json()['id']}", headers=biz_headers)).status_code == 204


# ---------------------------------------------------------------------------
# More edges: the paths a real catalog walks every week
# ---------------------------------------------------------------------------
class TestMoreEdges:
    @pytest.mark.asyncio
    async def test_editing_a_draft_re_resolves_its_lines(self, session, ws_id, test_user):
        """A draft's lines are replaced on edit, so the catalog check and
        the fiscal copy run again, against the workspace, every time."""
        p = await make_product(session, ws_id, test_user.id, fiscal_refs={"ncm": "1"})
        inv = await invoice_service.create_invoice(
            session, ws_id, test_user.id,
            {"issue_date": TODAY, "due_date": date(2026, 10, 8), "currency": "USD", "as_draft": True,
             "lines": [{"description": "Typed", "unit_price": "1"}]},
        )
        await session.commit()
        assert inv.status == "draft" and inv.lines[0].product_id is None
        await invoice_service.update_invoice(session, inv, {"lines": [{"description": "Hour", "unit_price": "200", "product_id": p.id}]})
        await session.commit()
        assert inv.lines[0].product_id == p.id and inv.lines[0].fiscal_refs == {"ncm": "1"}
        with pytest.raises(InvoiceError) as exc:
            await invoice_service.update_invoice(session, inv, {"lines": [{"description": "x", "unit_price": "1", "product_id": uuid.uuid4()}]})
        assert exc.value.code == "product_not_found"

    @pytest.mark.asyncio
    async def test_a_price_from_another_workspace_is_dropped_when_not_strict(self, session, ws_id, test_user, client, auth_headers):
        uid = test_user.id
        resp = await client.post("/api/workspaces", headers=auth_headers, json={"name": "Outra", "kind": "business", "self_membership": True})
        foreign = await make_product(session, uuid.UUID(resp.json()["id"]), uid)
        mine = await make_product(session, ws_id, uid)
        lines = await svc.resolve_lines(
            session, ws_id,
            [{"description": "a", "product_id": foreign.id, "price_id": foreign.prices[0].id},
             {"description": "b", "product_id": mine.id, "price_id": foreign.prices[0].id},
             {"description": "c", "price_id": mine.prices[0].id}],
            strict=False,
        )
        assert (lines[0]["product_id"], lines[0]["price_id"]) == (None, None)
        assert (lines[1]["product_id"], lines[1]["price_id"]) == (mine.id, None)
        assert (lines[2]["product_id"], lines[2]["price_id"]) == (mine.id, mine.prices[0].id)

    @pytest.mark.asyncio
    async def test_usage_counts_one_invoice_once_and_include_voided_ones(self, session, ws_id, test_user):
        """Two lines of one invoice are one invoice; a voided invoice still
        named the product, so the product still cannot be deleted."""
        p = await make_product(session, ws_id, test_user.id)
        inv = await an_invoice(session, ws_id, test_user.id, [
            {"description": "a", "unit_price": "1", "product_id": p.id},
            {"description": "b", "unit_price": "1", "product_id": p.id},
        ])
        assert (await svc.usage_counts(session, ws_id, [p.id]))[p.id] == 1
        await invoice_service.void_invoice(session, inv)
        await session.commit()
        assert (await svc.usage_counts(session, ws_id, [p.id]))[p.id] == 1
        with pytest.raises(InvoiceError):
            await svc.delete_product(session, p)

    @pytest.mark.asyncio
    async def test_kind_filter_and_archived_prices_stay_out_of_the_picker(self, session, ws_id, test_user):
        goods = await make_product(session, ws_id, test_user.id, name="Cable", kind="product")
        await make_product(session, ws_id, test_user.id, name="Support")
        assert [p.id for p in await svc.list_products(session, ws_id, kind="product")] == [goods.id]
        with pytest.raises(InvoiceError) as exc:
            await make_product(session, ws_id, test_user.id, kind="subscription")
        assert exc.value.code == "invalid_kind"
        await svc.update_price(session, goods, goods.prices[0], {"active": False})
        assert svc.price_for(goods, "USD") is None
        # An archived price can still be named by a line, on purpose: the
        # invoice was billed at it, and that is what the line records.
        inv = await an_invoice(session, ws_id, test_user.id, [{"description": "c", "unit_price": "1", "price_id": goods.prices[0].id}])
        assert inv.lines[0].price_id == goods.prices[0].id

    @pytest.mark.asyncio
    async def test_a_recurring_price_seeds_nothing_by_itself(self, session, ws_id, test_user):
        """A cadence on a price is a hint. No agreement, no invoice and no
        job appears because a price says monthly."""
        p = await make_product(session, ws_id, test_user.id, prices=[{"currency": "USD", "unit_price": "99", "billing": "recurring", "interval": "monthly"}])
        assert await schedules.list_schedules(session, ws_id) == []
        assert svc.price_for(p, "USD") is not None

    @pytest.mark.asyncio
    async def test_update_product_validates_and_trims(self, session, ws_id, test_user):
        p = await make_product(session, ws_id, test_user.id)
        with pytest.raises(InvoiceError) as exc:
            await svc.update_product(session, p, {"name": "   "})
        assert exc.value.code == "name_required"
        with pytest.raises(InvoiceError) as exc:
            await svc.update_product(session, p, {"kind": "thing"})
        assert exc.value.code == "invalid_kind"
        await svc.update_product(session, p, {"name": "  Senior hour ", "unit": "", "description": ""})
        assert (p.name, p.unit, p.description) == ("Senior hour", None, None)


@pytest.mark.asyncio
async def test_prices_over_http(client: AsyncClient, biz_headers):
    created = (await client.post("/api/products", headers=biz_headers, json={**PAYLOAD, "prices": [{"currency": "USD", "unit_price": "200.00", "lookup_key": "hour_usd"}]})).json()
    pid, price_id = created["id"], created["prices"][0]["id"]
    assert created["prices"][0]["lookup_key"] == "hour_usd"

    # The same lookup key twice is a 409 the client can branch on.
    resp = await client.post(f"/api/products/{pid}/prices", headers=biz_headers, json={"currency": "EUR", "unit_price": "180", "lookup_key": "hour_usd"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "price_key_taken"

    # Update, archive, and a bad update.
    resp = await client.patch(f"/api/products/{pid}/prices/{price_id}", headers=biz_headers, json={"unit_price": "210", "nickname": "  Standard "})
    assert resp.status_code == 200
    assert resp.json()["prices"][0]["unit_price"] == "210.00" and resp.json()["prices"][0]["nickname"] == "Standard"
    resp = await client.patch(f"/api/products/{pid}/prices/{price_id}", headers=biz_headers, json={"billing": "recurring"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "interval_required"
    resp = await client.patch(f"/api/products/{pid}/prices/{price_id}", headers=biz_headers, json={"active": False})
    assert resp.json()["prices"][0]["active"] is False

    # An unknown price on a known product is a 404, not a 500.
    assert (await client.patch(f"/api/products/{pid}/prices/{uuid.uuid4()}", headers=biz_headers, json={"active": True})).status_code == 404
    assert (await client.delete(f"/api/products/{pid}/prices/{uuid.uuid4()}", headers=biz_headers)).status_code == 404

    # Deleting an unused price works; a used one is refused.
    resp = await client.delete(f"/api/products/{pid}/prices/{price_id}", headers=biz_headers)
    assert resp.status_code == 200 and resp.json()["prices"] == []


@pytest.mark.asyncio
async def test_fiscal_refs_over_http(client: AsyncClient, biz_headers):
    resp = await client.post("/api/products", headers=biz_headers, json={"name": "Design", "fiscal_refs": {"Service_Code": " 1.05 ", "empty": ""}})
    assert resp.status_code == 201 and resp.json()["fiscal_refs"] == {"service_code": "1.05"}
    pid = resp.json()["id"]
    resp = await client.post("/api/products", headers=biz_headers, json={"name": "Bad", "fiscal_refs": {"bad key": "1"}})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "invalid_fiscal_refs"
    resp = await client.post("/api/invoices", headers=biz_headers, json={"currency": "USD", "lines": [{"description": "Design", "unit_price": "10", "product_id": pid}]})
    assert resp.status_code == 201 and resp.json()["lines"][0]["fiscal_refs"] == {"service_code": "1.05"}
    resp = await client.patch(f"/api/products/{pid}", headers=biz_headers, json={"fiscal_refs": None})
    assert resp.status_code == 200 and resp.json()["fiscal_refs"] is None

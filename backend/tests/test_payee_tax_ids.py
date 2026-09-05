"""Fiscal documents on a payee: what is stored, and what is refused.

The `other` kind gets its own attention here because it is the escape hatch:
it has to accept a document no pack describes, which means it also has to be
honest about what that costs.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_other_stores_free_text_untouched(client: AsyncClient, auth_headers, test_workspace):
    """No mask, no validation, only surrounding whitespace removed. This is
    what makes a country nobody has contributed a pack for usable today."""
    resp = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={
            "name": "Tokyo KK",
            "type": "company",
            "tax_ids": [{"kind": "other", "value": "  T1234567890123 (houjin bangou)  "}],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tax_ids"] == [
        {"kind": "other", "value": "T1234567890123 (houjin bangou)"}
    ]


@pytest.mark.asyncio
async def test_other_accepts_what_no_validator_understands(
    client: AsyncClient, auth_headers, test_workspace
):
    resp = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={
            "name": "Unknown Co",
            "type": "company",
            "tax_ids": [{"kind": "other", "value": "???-not-a-document-###"}],
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_duplicate_kind_is_refused_rather_than_silently_collapsed(
    client: AsyncClient, auth_headers, test_workspace
):
    """One document per kind. An earlier version kept the last value and
    dropped the first without a word, which loses a caller's data."""
    resp = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={
            "name": "Two Others",
            "type": "company",
            "tax_ids": [
                {"kind": "other", "value": "AAA-111"},
                {"kind": "other", "value": "BBB-222"},
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "duplicate_tax_id:other" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_documents_of_different_kinds_coexist(
    client: AsyncClient, auth_headers, test_workspace
):
    resp = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={
            "name": "Acme BR",
            "type": "company",
            "tax_ids": [
                {"kind": "cnpj", "value": "11.222.333/0001-81"},
                {"kind": "ie", "value": "ISENTO"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    stored = {t["kind"]: t["value"] for t in resp.json()["tax_ids"]}
    assert stored == {"cnpj": "11222333000181", "ie": "ISENTO"}


@pytest.mark.asyncio
async def test_a_foreign_document_is_stored_and_still_validated(
    client: AsyncClient, auth_headers, test_workspace
):
    """The workspace's jurisdiction does not gate the counterparty's country,
    but the document is still checked as what it is."""
    ok = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={"name": "Berlin GmbH", "type": "company",
              "tax_ids": [{"kind": "vat", "value": "de 123 456 789"}]},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["tax_ids"] == [{"kind": "vat", "value": "DE123456789"}]

    bad = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={"name": "Broken GmbH", "type": "company",
              "tax_ids": [{"kind": "vat", "value": "1"}]},
    )
    assert bad.status_code == 400, bad.text
    assert "invalid_tax_id:vat" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_emptying_a_document_removes_it(client: AsyncClient, auth_headers, test_workspace):
    created = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={"name": "Drop Doc", "type": "company",
              "tax_ids": [{"kind": "cnpj", "value": "11222333000181"},
                          {"kind": "ie", "value": "110042490114"}]},
    )
    payee_id = created.json()["id"]
    patched = await client.patch(
        f"/api/payees/{payee_id}",
        headers=auth_headers,
        json={"tax_ids": [{"kind": "cnpj", "value": "11222333000181"},
                          {"kind": "ie", "value": ""}]},
    )
    assert patched.status_code == 200, patched.text
    assert [t["kind"] for t in patched.json()["tax_ids"]] == ["cnpj"]


@pytest.mark.asyncio
async def test_omitting_tax_ids_leaves_them_alone(client: AsyncClient, auth_headers, test_workspace):
    """`tax_ids` absent means "don't touch"; present means "this is the set"."""
    created = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={"name": "Keep Doc", "type": "company",
              "tax_ids": [{"kind": "cnpj", "value": "11222333000181"}]},
    )
    payee_id = created.json()["id"]
    patched = await client.patch(
        f"/api/payees/{payee_id}", headers=auth_headers, json={"name": "Keep Doc Renamed"}
    )
    assert patched.status_code == 200, patched.text
    assert [t["kind"] for t in patched.json()["tax_ids"]] == ["cnpj"]

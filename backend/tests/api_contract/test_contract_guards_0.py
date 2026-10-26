"""Contract cases without seeded data (anon, empty body, unknown id), shard 0 of 2.

These fail before any row is read, so they skip the world fixture. See harness.py.
"""
import pytest
from httpx import AsyncClient

from tests.api_contract.cases import VARIANT_ANON, VARIANT_EMPTY, VARIANT_UNKNOWN_ID, Operation
from tests.api_contract.harness import check_case, headers_for, params_for

GUARD_VARIANTS = (VARIANT_ANON, VARIANT_EMPTY, VARIANT_UNKNOWN_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(("variant", "op"), params_for(GUARD_VARIANTS, shard=0, shards=2))
async def test_contract(
    client: AsyncClient,
    auth_headers,
    admin_auth_headers,
    variant: str,
    op: Operation,
):
    headers = headers_for(variant, op, auth_headers, admin_auth_headers)
    await check_case(client, variant, op, {}, headers)

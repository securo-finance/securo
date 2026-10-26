"""Contract cases: owner happy path, shard 1 of 4. See harness.py."""
import pytest
from httpx import AsyncClient

from tests.api_contract.cases import VARIANT_OK, Operation
from tests.api_contract.harness import check_case, headers_for, params_for


@pytest.mark.asyncio
@pytest.mark.parametrize(("variant", "op"), params_for((VARIANT_OK,), shard=1, shards=4))
async def test_contract(
    client: AsyncClient,
    world,
    auth_headers,
    admin_auth_headers,
    variant: str,
    op: Operation,
):
    headers = headers_for(variant, op, auth_headers, admin_auth_headers)
    await check_case(client, variant, op, world, headers)

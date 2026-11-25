"""A busy instance says so: running out of connections is a 503, not a 500.

The hosted instance ran out of database connections with 40 people at once and
every request over the limit failed with a 500 carrying SQLAlchemy's
"QueuePool limit of size 5 overflow 10 reached" (issue #1901).
"""
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.database import get_async_session
from app.main import app


async def test_pool_exhaustion_answers_503(client, auth_headers):
    """Waiting for a connection to no avail is overload, not a server error."""

    async def exhausted_session():
        raise PoolTimeoutError(
            "QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00"
        )
        yield  # pragma: no cover - the dependency never yields

    original = app.dependency_overrides[get_async_session]
    app.dependency_overrides[get_async_session] = exhausted_session
    try:
        response = await client.get("/api/accounts", headers=auth_headers)
    finally:
        app.dependency_overrides[get_async_session] = original

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["detail"] == (
        "The instance is at its database connection limit. Try again in a moment."
    )

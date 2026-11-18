"""Move a database stamped with the fork's old numbered migrations onto the fork branch.

Before `alembic/fork_versions/`, the fork numbered its migrations 119-121 after
upstream's 118. Those ids now belong to upstream's line. A database stamped
with one of them has upstream 118 and the matching fork migration applied, so
it is restamped to that pair of heads. Any other state is left alone, which
makes this safe to run before every `alembic upgrade heads`. From `backend/`:

    python -m scripts.fork_restamp_legacy

Delete this script once every database has been deployed with the fork branch.
"""

from __future__ import annotations

import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

LEGACY = {"119": "fork_001", "120": "fork_002", "121": "fork_003"}
UPSTREAM_BEFORE_LEGACY = "118"


async def restamp(database_url: str, known: set[str]) -> list[str] | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            rows = [row[0] for row in await conn.execute(text("SELECT version_num FROM alembic_version"))]
            if len(rows) != 1 or rows[0] not in LEGACY or rows[0] in known:
                return None
            heads = [UPSTREAM_BEFORE_LEGACY, LEGACY[rows[0]]]
            await conn.execute(text("DELETE FROM alembic_version"))
            for head in heads:
                await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})
            return heads
    finally:
        await engine.dispose()


def main() -> None:
    known = {script.revision for script in ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions()}
    heads = asyncio.run(restamp(get_settings().database_url, known))
    if heads:
        print(f"Restamped the legacy fork revision to {', '.join(heads)}.")


if __name__ == "__main__":
    main()

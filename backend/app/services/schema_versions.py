"""Which schema revision the running code is at, read from the migrations.

A backup is only meaningful against a schema that fits it. The archive records
the revision the exporting instance was running; a restore refuses a file from
a newer one instead of writing rows a newer model no longer matches.

The revision is read from `alembic/versions/` rather than from the
`alembic_version` table: the test suite builds its schema from the models and
never runs migrations, so the table is not there to ask.
"""
import re
from functools import lru_cache
from pathlib import Path

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
#: Files declare these both bare and annotated (`revision: str = "001"`).
_REVISION_RE = re.compile(r'^revision(?:\s*:\s*[^=\n]+)?\s*=\s*"([^"]+)"', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r"^down_revision(?:\s*:\s*[^=\n]+)?\s*=\s*(.+)$", re.MULTILINE)
_QUOTED_RE = re.compile(r'"([^"]+)"')


@lru_cache(maxsize=1)
def _revisions() -> dict[str, str | None]:
    """Map each revision id to its parent, as the migration files declare it."""
    graph: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*_*.py")):
        source = path.read_text(encoding="utf-8")
        revision = _REVISION_RE.search(source)
        if revision is None:
            continue
        down = _DOWN_REVISION_RE.search(source)
        parents = _QUOTED_RE.findall(down.group(1)) if down else []
        graph[revision.group(1)] = parents[0] if parents else None
    return graph


def head_revision() -> str:
    """The one revision nothing else points back to."""
    revisions = _revisions()
    referenced = {parent for parent in revisions.values() if parent}
    heads = [revision for revision in revisions if revision not in referenced]
    if len(heads) != 1:
        raise RuntimeError(f"Expected exactly one migration head, found: {', '.join(sorted(heads))}")
    return heads[0]


def is_known(revision: str) -> bool:
    return revision in _revisions()


def is_at_or_older(revision: str) -> bool:
    """True when `revision` is the head or one of its ancestors.

    Migrations carry no ordering of their own, only links, so "older" is asked
    as ancestry: a revision the head does not descend from is either newer or
    from a branch this instance does not have, and both are refused.
    """
    revisions = _revisions()
    current: str | None = head_revision()
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current == revision:
            return True
        seen.add(current)
        current = revisions.get(current)
    return False

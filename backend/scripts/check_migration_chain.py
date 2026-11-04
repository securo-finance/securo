#!/usr/bin/env python3
"""Fail when the Alembic revision chain is not a single straight line.

Two long-lived branches that each add a migration pick the same next number
without noticing: both say `revision = "074"`, both chain off `"073"`. Each
branch is fine on its own, and each one's CI is green, so the collision only
shows up once they are both on main — where `alembic upgrade head` refuses to
run at all:

    UserWarning: Revision 074 is present more than once
    FAILED: Multiple head revisions are present for given argument 'head'

That is a broken deploy for everyone, found after the fact. On a pull request
CI checks out the merge with the base branch, so running this there sees both
sides and catches the clash while it is still one rename to fix.

Parses the revision files directly rather than importing Alembic: this runs
before any dependency is installed, and it never touches a database.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# `revision: str = "074"` and `down_revision: Union[str, None] = "073"`, plus
# the plain unannotated forms, single or double quoted.
_REVISION_RE = re.compile(r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN_RE = re.compile(r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", re.M)


class Revision:
    def __init__(self, path: Path, revision: str, down_revision: str | None):
        self.path = path
        self.revision = revision
        self.down_revision = down_revision

    @property
    def name(self) -> str:
        return self.path.name


def load_revisions(versions_dir: Path) -> tuple[list[Revision], list[str]]:
    """Return every parsed revision, plus errors for the files that resisted."""
    revisions: list[Revision] = []
    errors: list[str] = []

    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")

        revision_match = _REVISION_RE.search(source)
        if revision_match is None:
            errors.append(f"{path.name}: no `revision = \"...\"` assignment found")
            continue

        down_match = _DOWN_RE.search(source)
        if down_match is None:
            errors.append(f"{path.name}: no `down_revision = ...` assignment found")
            continue

        revisions.append(Revision(path, revision_match.group(1), down_match.group(1)))

    return revisions, errors


def check(versions_dir: Path) -> list[str]:
    """Return one message per problem; an empty list means the chain is sound."""
    revisions, errors = load_revisions(versions_dir)
    if not revisions:
        return errors + [f"no migration files found in {versions_dir}"]

    by_revision: dict[str, list[Revision]] = {}
    for rev in revisions:
        by_revision.setdefault(rev.revision, []).append(rev)

    # The collision this script exists for: two files claiming one id.
    for revision_id, group in sorted(by_revision.items()):
        if len(group) > 1:
            files = ", ".join(sorted(rev.name for rev in group))
            errors.append(
                f"revision id {revision_id!r} is claimed by {len(group)} files: {files}. "
                f"Renumber the newer one and point its down_revision at the current head."
            )

    # A typo in down_revision, or a migration deleted out from under its child.
    for rev in revisions:
        if rev.down_revision is not None and rev.down_revision not in by_revision:
            errors.append(
                f"{rev.name}: down_revision {rev.down_revision!r} does not exist"
            )

    bases = [rev for rev in revisions if rev.down_revision is None]
    if len(bases) != 1:
        listed = ", ".join(sorted(rev.name for rev in bases)) or "none"
        errors.append(
            f"expected exactly one base migration (down_revision = None), found "
            f"{len(bases)}: {listed}"
        )

    # A head is a revision nothing chains off. More than one means the history
    # forked, which is what `alembic upgrade head` refuses to resolve.
    parents = {rev.down_revision for rev in revisions if rev.down_revision}
    heads = [rev for rev in revisions if rev.revision not in parents]
    if len(heads) != 1:
        listed = ", ".join(f"{rev.revision} ({rev.name})" for rev in sorted(heads, key=lambda r: r.name))
        errors.append(
            f"expected exactly one head, found {len(heads)}: {listed}. "
            f"`alembic upgrade head` cannot pick between them."
        )

    # Repo convention: the filename is the revision id plus a description, so
    # `ls versions/` reads in apply order.
    for rev in revisions:
        prefix = rev.name.split("_", 1)[0]
        if prefix != rev.revision:
            errors.append(
                f"{rev.name}: filename starts with {prefix!r} but the revision id "
                f"is {rev.revision!r}; keep them the same so the directory sorts "
                f"in apply order"
            )

    return errors


def main() -> int:
    versions_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else VERSIONS_DIR
    if not versions_dir.is_dir():
        print(f"migration chain: {versions_dir} is not a directory", file=sys.stderr)
        return 1

    problems = check(versions_dir)
    if problems:
        print("Alembic revision chain is broken:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nOn a pull request this compares your branch merged with the base "
            "branch, so a clash here means another migration landed on main "
            "while yours was open.",
            file=sys.stderr,
        )
        return 1

    revisions, _ = load_revisions(versions_dir)
    parents = {rev.down_revision for rev in revisions if rev.down_revision}
    head = next(rev for rev in revisions if rev.revision not in parents)
    print(
        f"Alembic revision chain is a single line: {len(revisions)} migrations, "
        f"head {head.revision} ({head.name})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

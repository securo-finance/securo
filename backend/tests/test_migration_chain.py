"""Tests for the Alembic revision chain guard.

The guard exists because two branches can each pick the same next revision
number and stay green until they are both on main, where `alembic upgrade
head` then refuses to run. These build that situation on disk and assert the
guard reports it.

Every case is synthetic, under tmp_path. The repository's own chain is checked
by the Migration Chain CI job, not from here: a real clash belongs to that job
alone, so it is not also reported as a backend test failure in a suite that has
nothing to do with it.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_migration_chain.py"
_spec = importlib.util.spec_from_file_location("check_migration_chain", _SCRIPT)
assert _spec and _spec.loader
check_migration_chain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_migration_chain)

check = check_migration_chain.check

_TEMPLATE = '''"""{description}"""
from typing import Sequence, Union

revision: str = "{revision}"
down_revision: Union[str, None] = {down}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _write(directory: Path, filename: str, revision: str, down: str | None) -> None:
    down_literal = "None" if down is None else f'"{down}"'
    directory.joinpath(filename).write_text(
        _TEMPLATE.format(description=filename, revision=revision, down=down_literal),
        encoding="utf-8",
    )


@pytest.fixture
def versions(tmp_path: Path) -> Path:
    """A sound three-migration chain: 001 -> 002 -> 003."""
    directory = tmp_path / "versions"
    directory.mkdir()
    _write(directory, "001_initial.py", "001", None)
    _write(directory, "002_second.py", "002", "001")
    _write(directory, "003_third.py", "003", "002")
    return directory


def test_straight_chain_passes(versions: Path):
    assert check(versions) == []


def test_duplicate_revision_id_is_reported(versions: Path):
    # The real case: two branches both numbered their migration 004.
    _write(versions, "004_from_branch_a.py", "004", "003")
    _write(versions, "004_from_branch_b.py", "004", "003")

    problems = check(versions)

    assert any("'004' is claimed by 2 files" in p for p in problems)
    assert any("004_from_branch_a.py" in p and "004_from_branch_b.py" in p for p in problems)


def test_two_heads_without_duplicate_ids_are_reported(versions: Path):
    # Distinct ids, but both chain off 003, so the history forks.
    _write(versions, "004_from_branch_a.py", "004", "003")
    _write(versions, "005_from_branch_b.py", "005", "003")

    problems = check(versions)

    assert any("expected exactly one head, found 2" in p for p in problems)


def test_missing_down_revision_target_is_reported(versions: Path):
    _write(versions, "004_orphan.py", "004", "999")

    problems = check(versions)

    assert any("down_revision '999' does not exist" in p for p in problems)


def test_second_base_is_reported(versions: Path):
    _write(versions, "004_second_base.py", "004", None)

    problems = check(versions)

    assert any("expected exactly one base migration" in p for p in problems)


def test_filename_prefix_must_match_revision_id(versions: Path):
    _write(versions, "999_mislabelled.py", "004", "003")

    problems = check(versions)

    assert any("filename starts with '999'" in p for p in problems)


def test_unparseable_file_is_reported(versions: Path):
    versions.joinpath("004_broken.py").write_text("# no revision here\n", encoding="utf-8")

    problems = check(versions)

    assert any("no `revision" in p for p in problems)


def test_empty_directory_is_reported(tmp_path: Path):
    empty = tmp_path / "versions"
    empty.mkdir()

    assert any("no migration files found" in p for p in check(empty))

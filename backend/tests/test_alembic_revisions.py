from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
UPSTREAM = BACKEND / "alembic" / "versions"
FORK = BACKEND / "alembic" / "fork_versions"


def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def _directory(revision: str) -> Path:
    path = _scripts().get_revision(revision).path
    assert path is not None
    return Path(path).parent


def test_upstream_and_fork_each_end_in_one_head():
    # Fork migrations sit on their own branch so upstream can keep numbering
    # its line without ever sharing an id with the fork.
    heads = _scripts().get_heads()

    assert sorted(_directory(head).name for head in heads) == ["fork_versions", "versions"]


def test_fork_migrations_form_one_line_off_an_upstream_revision():
    scripts = _scripts()
    fork = [script for script in scripts.walk_revisions() if Path(script.path).parent == FORK]

    assert {script.revision for script in fork} == {path.stem[:8] for path in FORK.glob("fork_*.py")}
    assert all(script.revision.startswith("fork_") for script in fork)
    roots = [script for script in fork if not str(script.down_revision).startswith("fork_")]
    assert len(roots) == 1
    assert roots[0].branch_labels == {"fork"}
    assert _directory(str(roots[0].down_revision)) == UPSTREAM
    assert len({script.down_revision for script in fork}) == len(fork)


def test_upstream_migrations_never_depend_on_the_fork():
    upstream = [script for script in _scripts().walk_revisions() if Path(script.path).parent == UPSTREAM]

    assert [script.revision for script in upstream if str(script.down_revision).startswith("fork_")] == []

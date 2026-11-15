import re
from collections import Counter
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _revision(path: Path) -> str:
    match = re.search(r'^revision(?::\s*str)? = "([^"]+)"', path.read_text(), re.M)
    assert match, f"{path.name} declares no revision"
    return match.group(1)


def test_every_migration_has_its_own_revision_id():
    # A fork and upstream both numbering their next migration alike leaves two
    # files with one id, and alembic silently runs only one of them.
    revisions = Counter(_revision(path) for path in VERSIONS.glob("*.py"))

    assert [rev for rev, count in revisions.items() if count > 1] == []


def test_migrations_form_a_single_chain():
    config = Config()
    config.set_main_option("script_location", str(VERSIONS.parent))

    assert len(ScriptDirectory.from_config(config).get_heads()) == 1

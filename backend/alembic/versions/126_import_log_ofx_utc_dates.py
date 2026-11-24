"""mark the OFX imports whose rows were dated on the UTC day

Revision ID: 126
Revises: 125

Before #1671, ofxparse converted a DTPOSTED with an offset ("[+1:CET]") to
UTC, so those rows could sit one day off the day the bank printed. The FITID
dedup accepts a one-day difference to recognise them on re-import, but the
window must not reach rows imported since: a bank that repeats one FITID for a
daily fee would lose the next day's charge when statements overlap (#388).

Every OFX import that exists when this runs was parsed the old way, so it is
marked; new imports are not.

This migration first shipped on the #1778 branch as revision 125, the number
the budget currency backfill took on main. A database that ran that branch
already has the column and is stamped 125, so alembic never ran the budget
backfill there and comes here next. In that case the column and its marks are
kept as they are (imports made since are not legacy) and the budget backfill
runs now. On every other database the column cannot exist yet.
"""

import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "126"
down_revision: Union[str, None] = "125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _run_budget_currency_backfill() -> None:
    path = Path(__file__).with_name("125_budget_currency_from_owner.py")
    spec = importlib.util.spec_from_file_location("budget_currency_from_owner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("import_logs")}
    if "ofx_utc_dates" in columns:
        _run_budget_currency_backfill()
        return

    op.add_column(
        "import_logs",
        sa.Column(
            "ofx_utc_dates",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("UPDATE import_logs SET ofx_utc_dates = true WHERE lower(format) = 'ofx'")


def downgrade() -> None:
    op.drop_column("import_logs", "ofx_utc_dates")

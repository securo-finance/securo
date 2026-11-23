"""mark the OFX imports whose rows were dated on the UTC day

Revision ID: 125
Revises: 124

Before #1671, ofxparse converted a DTPOSTED with an offset ("[+1:CET]") to
UTC, so those rows could sit one day off the day the bank printed. The FITID
dedup accepts a one-day difference to recognise them on re-import, but the
window must not reach rows imported since: a bank that repeats one FITID for a
daily fee would lose the next day's charge when statements overlap (#388).

Every OFX import that exists when this runs was parsed the old way, so it is
marked; new imports are not.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "125"
down_revision: Union[str, None] = "124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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

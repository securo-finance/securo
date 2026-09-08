"""what a line's quantity counts

"12 hours x 180.00" is a different claim from "12 x 180.00", and only the
first one can be checked by the person paying it.

Free text rather than a list: a translator bills by word, a photographer
by image, a freight company by tonne-kilometre. An enum here would be a
guess about somebody else's trade, and a migration every time the guess
turned out to be short.

Revision ID: 083
Revises: 082
"""
from alembic import op
import sqlalchemy as sa

revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_lines", sa.Column("unit", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_lines", "unit")

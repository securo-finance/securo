"""normalize workspaces.kind to the supported set

Revision ID: 068
Revises: 067
Create Date: 2026-08-12

`kind` is now two values, personal and business. The three retired ones
all described work rather than a household, so they fold into
`business`:

  - `freelancer` and `small_business` split work by size, which the data
    model never acted on.
  - `accountant_firm` described an edge between two workspaces, not an
    attribute of one, and that relationship is already carried by
    `workspaces.managed_by_user_id`.

The column has no DB-level constraint, so without this a row would keep
reading back a kind the app no longer offers. Data-only, and a no-op on
most installs: the retired values were never selectable in the UI.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE workspaces
           SET kind = 'business'
         WHERE kind IN ('freelancer', 'small_business', 'accountant_firm')
        """
    )


def downgrade() -> None:
    # Not reversible: the rows are indistinguishable from workspaces that
    # were always business.
    pass

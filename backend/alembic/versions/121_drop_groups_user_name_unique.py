"""groups: drop the user-wide unique name the workspace model replaced

`uq_groups_user_id_name` was created in 044, back when a group belonged to a
user. Groups have belonged to a workspace since multi-workspace landed, and
`group_service.create_group` enforces one name per *workspace*; the model never
declared the old constraint either, so it survives only in the database — it is
listed as drift in `tests/schema_drift_baseline.txt`.

The database therefore enforces a rule the code does not believe in, and it
blocks a restore. A restore writes every row for the person running it, so a
group it brings back ("Casa") collides with the "Casa" that person still has in
the workspace the backup came from — the same person, two workspaces, one
forbidden name. Nothing is lost (the restore rolls back), but the archive
cannot be restored at all.

This is the groups counterpart of 072, which did the same for payees. The
downgrade only recreates the constraint when the data still satisfies it: the
rows this migration made legal are exactly the ones it forbade.

Revision ID: 121
Revises: 120
"""

import sqlalchemy as sa
from alembic import op

revision = "121"
down_revision = "120"
branch_labels = None
depends_on = None


def _constraint_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'groups' AND constraint_name = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    if _constraint_exists("uq_groups_user_id_name"):
        op.drop_constraint("uq_groups_user_id_name", "groups", type_="unique")


def downgrade() -> None:
    duplicated_per_user = op.get_bind().execute(
        sa.text("SELECT 1 FROM groups GROUP BY user_id, name HAVING count(*) > 1 LIMIT 1")
    ).scalar()
    if not duplicated_per_user:
        op.create_unique_constraint("uq_groups_user_id_name", "groups", ["user_id", "name"])

"""remember which pack a rule came from

Adds rules.source, source_key and source_hash, then marks the rules that
were installed from a built-in pack before this existed. Only exact,
untouched copies are marked: same name, conditions, priority and actions,
with the category being the workspace's copy of the pack's default one.
Anything the user edited stays unmarked and is treated as their own, so a
later "remove pack" never takes it. Marking only adds these three values;
no rule's content changes.

Idempotent: rules that already have a source are skipped. It reads the
packs as they ship today; a later change to a pack only makes fewer old
rules match, never a wrong one.

Revision ID: 107
Revises: 106
"""

import json
import uuid

import sqlalchemy as sa
from sqlalchemy import Connection, text

from alembic import op
from app.plugins.provenance import rule_content_hash
from app.services.rule_packs import find_pack_rule

revision = "107"
down_revision = "106"
branch_labels = None
depends_on = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("rules")}
    if "source" not in columns:
        op.add_column("rules", sa.Column("source", sa.String(120), nullable=True))
        op.add_column("rules", sa.Column("source_key", sa.String(64), nullable=True))
        op.add_column("rules", sa.Column("source_hash", sa.String(64), nullable=True))
        op.create_index("ix_rules_workspace_source", "rules", ["workspace_id", "source"])

    mark_rules_from_packs(bind)


def mark_rules_from_packs(bind: Connection) -> int:
    """Record the origin of rules that are untouched copies of a pack rule.

    Returns how many rules were marked.
    """
    # Keyed the way rule actions store a category id, whatever the driver returns.
    category_names = {
        str(uuid.UUID(str(row.id))): row.name
        for row in bind.execute(text("SELECT id, name FROM categories"))
    }
    rows = bind.execute(
        text(
            "SELECT id, name, conditions_op, conditions, actions, priority "
            "FROM rules WHERE source IS NULL"
        )
    ).fetchall()
    marked = 0
    for row in rows:
        content = {
            "name": row.name,
            "conditions_op": row.conditions_op,
            "conditions": _json(row.conditions),
            "actions": _json(row.actions),
            "priority": row.priority or 0,
        }
        match = find_pack_rule(**content, category_names=category_names)
        if match is None:
            continue
        pack, rule = match
        bind.execute(
            text(
                "UPDATE rules SET source = :source, source_key = :key, source_hash = :hash "
                "WHERE id = :id AND source IS NULL"
            ),
            {"source": pack.source, "key": rule.key, "hash": rule_content_hash(**content), "id": row.id},
        )
        marked += 1
    return marked


def downgrade() -> None:
    op.drop_index("ix_rules_workspace_source", table_name="rules")
    op.drop_column("rules", "source_hash")
    op.drop_column("rules", "source_key")
    op.drop_column("rules", "source")

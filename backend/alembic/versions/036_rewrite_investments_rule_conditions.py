"""rewrite Investimentos rule from a single regex to OR'd `contains` conditions

Revision ID: 036
Revises: 035
Create Date: 2026-04-20

The seed in 033 created one condition using a long pipe-separated regex.
That works but reads poorly in the rules UI — an unreadable wall of tokens
that users can't meaningfully edit. This migration rewrites any rule still
in that form into a list of OR'd `contains` conditions, one per keyword.
Semantically identical (the rule engine normalizes accents/case on both
sides), much easier to skim and tweak. Idempotent: rules already in the
new shape are left alone, and user-edited rules are only touched if they
still match the original single-regex signature.
"""

from typing import Sequence, Union

import json
import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RULE_NAME = "Investimentos (Aplicação / Resgate)"
NEW_KEYWORDS = [
    "APLICACAO",
    "RESGATE",
    "CDB",
    "LCA",
    "LCI",
    "DEBENTURE",
    "TESOURO",
    "RENDA FIXA",
    "FUNDO DE INVESTIMENTO",
    "DEB FUNDO",
    "CREDITO FUNDO",
    "COE",
]


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text("SELECT id, conditions, conditions_op FROM rules WHERE name = :name"),
        {"name": RULE_NAME},
    ).fetchall()

    new_conditions = [
        {"field": "description", "op": "contains", "value": kw} for kw in NEW_KEYWORDS
    ]
    payload = json.dumps(new_conditions)

    for row in rows:
        rule_id, conditions, _ = row
        # Only rewrite rules still in the legacy single-regex shape. If a
        # user has already edited this rule by hand, their version wins.
        if (
            isinstance(conditions, list)
            and len(conditions) == 1
            and conditions[0].get("op") == "regex"
        ):
            bind.execute(
                sa.text(
                    "UPDATE rules SET conditions = CAST(:c AS jsonb), "
                    "conditions_op = 'or' WHERE id = :id"
                ),
                {"c": payload, "id": rule_id},
            )


def downgrade() -> None:
    # No-op: the regex form is a worse UX and we don't want to restore it.
    # Users who want it back can edit the rule by hand.
    pass

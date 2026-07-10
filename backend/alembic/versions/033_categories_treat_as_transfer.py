"""add treat_as_transfer flag and seed Investimentos category + default rule

Revision ID: 033
Revises: 032
Create Date: 2026-04-20

Adds a boolean `treat_as_transfer` on categories. Categories with this
flag are treated like paired transfers in report aggregations — their
transactions are excluded from income/expense sums. This makes one-sided
movements (investment applications/redemptions, loan repayments) stop
polluting P&L figures without needing a real counterpart transaction.

For every existing user we:
  1. Create an "Investments" / "Investimentos" category if missing, with
     the flag set and is_system=true.
  2. Create a default rule matching PT investment keywords
     (APLICACAO, RESGATE, CDB, TESOURO, ...) → that category.
  3. Backfill: apply the rule to existing uncategorized transactions.

User intent is respected — already-categorized transactions are never
touched. The PT rule is a harmless no-op for users whose bank data
doesn't contain those patterns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INVESTMENTS_CATEGORY_PT = "Investimentos"
INVESTMENTS_CATEGORY_EN = "Investments"
INVESTMENTS_ICON = "trending-up"
INVESTMENTS_COLOR = "#0EA5E9"

RULE_NAME = "Investimentos (Aplicação / Resgate)"
# Keywords checked one-by-one as OR'd `contains` conditions. The rule engine
# normalizes both sides (uppercase + strip accents) so "APLICACAO" matches
# "Aplicação", "aplicacao", etc. — no need for per-accent variants.
RULE_KEYWORDS = [
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
# Regex used only for the one-shot backfill SQL below. Postgres ~* is
# case-insensitive but accent-sensitive; the app-side engine strips accents,
# so we include both variants here to match the full historical set.
BACKFILL_REGEX = (
    r"APLICACAO|APLICAÇÃO|RESGATE|CDB|LCA|LCI|DEBENTURE|TESOURO|"
    r"RENDA FIXA|FUNDO DE INVESTIMENTO|DEB FUNDO|"
    r"CREDITO FUNDO|CRÉDITO FUNDO|COE"
)


def upgrade() -> None:
    # 1. Column on categories.
    op.add_column(
        "categories",
        sa.Column(
            "treat_as_transfer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    bind = op.get_bind()

    # 2. Resolve each user's language from preferences JSON. Default to
    #    pt-BR if unreadable — the app is Brazilian-first and the rule
    #    patterns won't match EN descriptions anyway.
    users = bind.execute(sa.text("SELECT id, preferences FROM users")).fetchall()

    for row in users:
        user_id = row[0]
        prefs = row[1] or {}
        lang = (prefs.get("language") or "pt-BR") if isinstance(prefs, dict) else "pt-BR"
        cat_name = INVESTMENTS_CATEGORY_EN if lang == "en" else INVESTMENTS_CATEGORY_PT

        # Find "Other" group so the new category has a home; fall back to NULL.
        group_id = bind.execute(
            sa.text(
                "SELECT id FROM category_groups WHERE user_id = :uid "
                "AND name IN ('Outros', 'Other') LIMIT 1"
            ),
            {"uid": user_id},
        ).scalar()

        # 3. Upsert the category — idempotent on re-run; don't clobber
        #    an existing one the user may have renamed or customized.
        category_id = bind.execute(
            sa.text("SELECT id FROM categories WHERE user_id = :uid AND name = :name"),
            {"uid": user_id, "name": cat_name},
        ).scalar()

        if category_id is None:
            category_id = bind.execute(
                sa.text(
                    "INSERT INTO categories "
                    "(id, user_id, group_id, name, icon, color, is_system, treat_as_transfer) "
                    "VALUES (gen_random_uuid(), :uid, :gid, :name, :icon, :color, true, true) "
                    "RETURNING id"
                ),
                {
                    "uid": user_id,
                    "gid": group_id,
                    "name": cat_name,
                    "icon": INVESTMENTS_ICON,
                    "color": INVESTMENTS_COLOR,
                },
            ).scalar()
        else:
            # Ensure the flag is set even if the category already existed.
            bind.execute(
                sa.text("UPDATE categories SET treat_as_transfer = true WHERE id = :cid"),
                {"cid": category_id},
            )

        # 4. Seed the default rule if missing.
        existing_rule = bind.execute(
            sa.text("SELECT id FROM rules WHERE user_id = :uid AND name = :name"),
            {"uid": user_id, "name": RULE_NAME},
        ).scalar()

        if existing_rule is None:
            import json

            conditions = json.dumps(
                [{"field": "description", "op": "contains", "value": kw} for kw in RULE_KEYWORDS]
            )
            actions = json.dumps(
                [
                    {"op": "set_category", "value": str(category_id)},
                ]
            )
            bind.execute(
                sa.text(
                    "INSERT INTO rules "
                    "(id, user_id, name, conditions_op, conditions, actions, priority, is_active) "
                    "VALUES (gen_random_uuid(), :uid, :name, 'or', "
                    "CAST(:cond AS jsonb), CAST(:act AS jsonb), 20, true)"
                ),
                {
                    "uid": user_id,
                    "name": RULE_NAME,
                    "cond": conditions,
                    "act": actions,
                },
            )

        # 5. Backfill — assign the new category to uncategorized transactions
        #    whose description matches the regex. Only touches uncategorized
        #    rows so a user who manually categorized an APLICACAO into
        #    "Poupança" or similar keeps their choice. Postgres regex
        #    matches are case-sensitive by default; use ~* for case-insensitive
        #    so lowercase variants ("aplicação") also match.
        bind.execute(
            sa.text(
                "UPDATE transactions "
                "SET category_id = :cid "
                "WHERE user_id = :uid "
                "AND category_id IS NULL "
                "AND description ~* :pattern "
                "AND source != 'opening_balance'"
            ),
            {
                "cid": category_id,
                "uid": user_id,
                "pattern": BACKFILL_REGEX,
            },
        )


def downgrade() -> None:
    # Keep seeded categories and rules — users may have re-categorized
    # transactions against them. Only drop the column.
    op.drop_column("categories", "treat_as_transfer")

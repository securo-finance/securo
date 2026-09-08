"""enforce normalized payee names within each workspace

Revision ID: 076
Revises: 075
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Revision 072 made exact names workspace-unique. Collapse case/whitespace
    # variants before adding the normalized invariant used by the service.
    op.execute(
        """
        CREATE TEMP TABLE payee_dedupe_076 ON COMMIT DROP AS
        SELECT id AS source_id, target_id
        FROM (
            SELECT
                id,
                first_value(id) OVER (
                    PARTITION BY workspace_id, lower(btrim(name))
                    ORDER BY created_at, id
                ) AS target_id,
                row_number() OVER (
                    PARTITION BY workspace_id, lower(btrim(name))
                    ORDER BY created_at, id
                ) AS duplicate_rank
            FROM payees
        ) ranked
        WHERE duplicate_rank > 1
        """
    )
    op.execute(
        """
        UPDATE payees target
        SET
            is_favorite = merged.is_favorite,
            notes = left(merged.notes, 1000),
            type = coalesce(target.type, merged.payee_type),
            email = coalesce(target.email, merged.email),
            phone = coalesce(target.phone, merged.phone),
            address = coalesce(target.address, merged.address),
            website = coalesce(target.website, merged.website)
        FROM (
            SELECT
                duplicates.target_id,
                bool_or(payees.is_favorite) AS is_favorite,
                (array_agg(payees.type ORDER BY payees.created_at, payees.id)
                    FILTER (WHERE payees.type IS NOT NULL))[1] AS payee_type,
                (array_agg(payees.email ORDER BY payees.created_at, payees.id)
                    FILTER (WHERE payees.email IS NOT NULL))[1] AS email,
                (array_agg(payees.phone ORDER BY payees.created_at, payees.id)
                    FILTER (WHERE payees.phone IS NOT NULL))[1] AS phone,
                (array_agg(payees.address ORDER BY payees.created_at, payees.id)
                    FILTER (WHERE payees.address IS NOT NULL))[1] AS address,
                (array_agg(payees.website ORDER BY payees.created_at, payees.id)
                    FILTER (WHERE payees.website IS NOT NULL))[1] AS website,
                string_agg(
                    DISTINCT NULLIF(btrim(payees.notes), ''),
                    E'\n' ORDER BY NULLIF(btrim(payees.notes), '')
                ) AS notes
            FROM payee_dedupe_076 duplicates
            JOIN payees
              ON payees.id = duplicates.source_id
              OR payees.id = duplicates.target_id
            GROUP BY duplicates.target_id
        ) merged
        WHERE target.id = merged.target_id
        """
    )
    op.execute(
        """
        WITH candidates AS (
            SELECT
                tax_id.id,
                duplicates.target_id,
                row_number() OVER (
                    PARTITION BY duplicates.target_id, tax_id.kind
                    ORDER BY tax_id.created_at, tax_id.id
                ) AS keep_rank
            FROM payee_tax_ids tax_id
            JOIN payee_dedupe_076 duplicates
              ON duplicates.source_id = tax_id.payee_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM payee_tax_ids existing
                WHERE existing.payee_id = duplicates.target_id
                  AND existing.kind = tax_id.kind
            )
        )
        UPDATE payee_tax_ids tax_id
        SET payee_id = candidates.target_id
        FROM candidates
        WHERE tax_id.id = candidates.id
          AND candidates.keep_rank = 1
        """
    )
    op.execute(
        """
        UPDATE transactions
        SET payee_id = duplicates.target_id
        FROM payee_dedupe_076 duplicates
        WHERE transactions.payee_id = duplicates.source_id
        """
    )
    op.execute(
        """
        UPDATE payee_mapping
        SET
            target_id = duplicates.target_id,
            workspace_id = target.workspace_id
        FROM payee_dedupe_076 duplicates
        JOIN payees target ON target.id = duplicates.target_id
        WHERE payee_mapping.target_id = duplicates.source_id
        """
    )
    op.execute(
        """
        INSERT INTO payee_mapping (id, user_id, workspace_id, target_id)
        SELECT source.id, source.user_id, source.workspace_id, duplicates.target_id
        FROM payee_dedupe_076 duplicates
        JOIN payees source ON source.id = duplicates.source_id
        ON CONFLICT (id) DO UPDATE
        SET
            target_id = EXCLUDED.target_id,
            workspace_id = EXCLUDED.workspace_id
        """
    )
    op.execute(
        """
        UPDATE rules
        SET actions = rewritten.actions
        FROM (
            SELECT
                rules_to_update.id,
                jsonb_agg(
                    CASE
                        WHEN duplicates.target_id IS NOT NULL THEN jsonb_set(
                            action.item::jsonb,
                            '{value}',
                            to_jsonb(duplicates.target_id::text)
                        )
                        ELSE action.item::jsonb
                    END
                    ORDER BY action.ordinality
                )::json AS actions
            FROM rules rules_to_update
            CROSS JOIN LATERAL json_array_elements(rules_to_update.actions)
                WITH ORDINALITY AS action(item, ordinality)
            LEFT JOIN payee_dedupe_076 duplicates
              ON action.item->>'op' = 'set_payee'
             AND action.item->>'value' = duplicates.source_id::text
            GROUP BY rules_to_update.id
            HAVING bool_or(duplicates.target_id IS NOT NULL)
        ) rewritten
        WHERE rules.id = rewritten.id
        """
    )
    op.execute(
        """
        DO $$
        DECLARE duplicate record;
        BEGIN
            FOR duplicate IN SELECT * FROM payee_dedupe_076 LOOP
                UPDATE rules
                SET conditions = (
                    SELECT jsonb_agg(
                        CASE
                            WHEN node.item ? 'conditions' THEN jsonb_set(
                                node.item,
                                '{conditions}',
                                (
                                    SELECT jsonb_agg(
                                        CASE
                                            WHEN leaf.item->>'field' = 'payee_id'
                                             AND leaf.item->>'value' = duplicate.source_id::text
                                            THEN jsonb_set(
                                                leaf.item,
                                                '{value}',
                                                to_jsonb(duplicate.target_id::text)
                                            )
                                            ELSE leaf.item
                                        END
                                        ORDER BY leaf.ordinality
                                    )
                                    FROM jsonb_array_elements(node.item->'conditions')
                                        WITH ORDINALITY AS leaf(item, ordinality)
                                )
                            )
                            WHEN node.item->>'field' = 'payee_id'
                             AND node.item->>'value' = duplicate.source_id::text
                            THEN jsonb_set(
                                node.item,
                                '{value}',
                                to_jsonb(duplicate.target_id::text)
                            )
                            ELSE node.item
                        END
                        ORDER BY node.ordinality
                    )::json
                    FROM jsonb_array_elements(conditions::jsonb)
                        WITH ORDINALITY AS node(item, ordinality)
                )
                WHERE jsonb_path_exists(
                    conditions::jsonb,
                    '$.** ? (@.field == "payee_id" && @.value == $source)',
                    jsonb_build_object('source', duplicate.source_id::text)
                );
            END LOOP;
        END $$
        """
    )
    op.execute(
        """
        DELETE FROM payees
        USING payee_dedupe_076 duplicates
        WHERE payees.id = duplicates.source_id
        """
    )
    op.create_index(
        "uq_payees_workspace_id_lower_name",
        "payees",
        ["workspace_id", sa.text("lower(btrim(name))")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payees_workspace_id_lower_name", table_name="payees")

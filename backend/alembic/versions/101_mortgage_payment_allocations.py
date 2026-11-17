"""store recorded mortgage payment breakdowns

Revision ID: 101
Revises: 100
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "mortgage_payment_allocations" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "mortgage_payment_allocations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "payment_transaction_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("transactions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "loan_account_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("principal_amount", sa.Numeric(15, 2), nullable=False),
            sa.Column("interest_amount", sa.Numeric(15, 2), nullable=False),
            sa.Column(
                "loan_transaction_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("transactions.id", ondelete="CASCADE"),
                nullable=True,
                unique=True,
            ),
            sa.CheckConstraint(
                "principal_amount >= 0", name="ck_mortgage_payment_principal_nonnegative"
            ),
            sa.CheckConstraint(
                "interest_amount >= 0", name="ck_mortgage_payment_interest_nonnegative"
            ),
            sa.UniqueConstraint(
                "payment_transaction_id", "loan_account_id", name="uq_mortgage_payment_loan"
            ),
        )

    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("mortgage_payment_allocations")
    }
    for index_name, column in (
        ("ix_mortgage_payment_allocations_workspace_id", "workspace_id"),
        ("ix_mortgage_payment_allocations_payment_transaction_id", "payment_transaction_id"),
        ("ix_mortgage_payment_allocations_loan_account_id", "loan_account_id"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "mortgage_payment_allocations", [column])

    # A bank payment can be deleted from many places (single and bulk delete,
    # account or user deletion, provider sync). Deleting it cascades to its
    # breakdown rows, and each of those takes its principal entry with it, or
    # the loan would stay reduced. AFTER DELETE on the breakdown table, because
    # a BEFORE trigger on transactions fails when one statement deletes both
    # the payment and its principal entry.
    op.execute("""
        CREATE OR REPLACE FUNCTION delete_mortgage_principal_entry() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM transactions
            WHERE id = OLD.loan_transaction_id
              AND source = 'mortgage_principal';
            RETURN NULL;
        END;
        $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_delete_mortgage_principal_entry'
                  AND tgrelid = 'mortgage_payment_allocations'::regclass
                  AND NOT tgisinternal
            ) THEN
                CREATE TRIGGER trg_delete_mortgage_principal_entry
                AFTER DELETE ON mortgage_payment_allocations
                FOR EACH ROW WHEN (OLD.loan_transaction_id IS NOT NULL)
                EXECUTE FUNCTION delete_mortgage_principal_entry();
            END IF;
        END;
        $$;
    """)


def downgrade() -> None:
    # This migration also upgrades databases from the legacy combined revision.
    # Do not remove mortgage data when rolling back the version marker.
    pass

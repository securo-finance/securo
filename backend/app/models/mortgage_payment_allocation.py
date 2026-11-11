import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Transaction.source of the principal entry a breakdown posts to its loan. The
# entry is owned by the breakdown: edit or remove it through the bank payment.
MORTGAGE_PRINCIPAL_SOURCE = "mortgage_principal"


class MortgagePaymentAllocation(Base):
    """Actual principal and interest components for one loan in a payment."""

    __tablename__ = "mortgage_payment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "payment_transaction_id", "loan_account_id", name="uq_mortgage_payment_loan"
        ),
        CheckConstraint("principal_amount >= 0", name="ck_mortgage_payment_principal_nonnegative"),
        CheckConstraint("interest_amount >= 0", name="ck_mortgage_payment_interest_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    payment_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        index=True,
    )
    loan_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    interest_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    loan_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )




def _install_principal_cleanup(target, connection, **kw) -> None:
    """Delete a principal entry whenever its breakdown row goes.

    A bank payment can be deleted from many places (single and bulk delete,
    account or user deletion, provider sync). Deleting it cascades to its
    breakdown rows, and each of those takes its principal entry with it, or the
    loan would stay reduced. Migration 097 installs the PostgreSQL trigger on
    existing databases; this keeps schemas built with metadata.create_all
    (tests) equivalent.

    PostgreSQL uses an AFTER trigger on the breakdown table: a BEFORE trigger
    on transactions would fail when one statement deletes both the payment and
    its principal entry. SQLite test databases do not enforce foreign keys, so
    there the trigger follows the payment row itself.
    """
    if connection.dialect.name == "postgresql":
        translate = connection.get_execution_options().get("schema_translate_map") or {}
        schema = translate.get(None)
        prefix = f'"{schema}".' if schema else ""
        connection.exec_driver_sql(
            f"""
            CREATE OR REPLACE FUNCTION {prefix}delete_mortgage_principal_entry()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                DELETE FROM {prefix}transactions
                WHERE id = OLD.loan_transaction_id
                  AND source = '{MORTGAGE_PRINCIPAL_SOURCE}';
                RETURN NULL;
            END;
            $$
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_delete_mortgage_principal_entry
            AFTER DELETE ON {prefix}mortgage_payment_allocations
            FOR EACH ROW WHEN (OLD.loan_transaction_id IS NOT NULL)
            EXECUTE FUNCTION {prefix}delete_mortgage_principal_entry()
            """
        )
    elif connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_delete_mortgage_principal_entry
            AFTER DELETE ON transactions
            FOR EACH ROW BEGIN
                DELETE FROM transactions
                WHERE source = '{MORTGAGE_PRINCIPAL_SOURCE}'
                  AND id IN (
                    SELECT loan_transaction_id
                    FROM mortgage_payment_allocations
                    WHERE payment_transaction_id = OLD.id
                  );
                DELETE FROM mortgage_payment_allocations
                WHERE payment_transaction_id = OLD.id OR loan_transaction_id = OLD.id;
            END
            """
        )


event.listen(MortgagePaymentAllocation.__table__, "after_create", _install_principal_cleanup)

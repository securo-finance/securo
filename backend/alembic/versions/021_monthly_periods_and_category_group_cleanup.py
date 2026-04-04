"""add monthly periods and remove category group persistence

Revision ID: 021
Revises: 020
Create Date: 2026-04-04
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _period_from_date(value) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def upgrade() -> None:
    op.create_table(
        "monthly_periods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "period", name="uq_monthly_periods_user_period"),
    )
    op.create_index("ix_monthly_periods_user_id", "monthly_periods", ["user_id"])

    op.add_column(
        "accounts",
        sa.Column("monthly_period_id", UUID(as_uuid=True), sa.ForeignKey("monthly_periods.id"), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("monthly_period_id", UUID(as_uuid=True), sa.ForeignKey("monthly_periods.id"), nullable=True),
    )
    op.create_index("ix_accounts_monthly_period_id", "accounts", ["monthly_period_id"])
    op.create_index("ix_transactions_monthly_period_id", "transactions", ["monthly_period_id"])

    conn = op.get_bind()
    inspector = sa.inspect(conn)

    monthly_periods = sa.table(
        "monthly_periods",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("user_id", UUID(as_uuid=True)),
        sa.column("period", sa.String(length=7)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    users = sa.table(
        "users",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("preferences", sa.JSON()),
    )
    accounts = sa.table(
        "accounts",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("user_id", UUID(as_uuid=True)),
        sa.column("monthly_period_id", UUID(as_uuid=True)),
    )
    transactions = sa.table(
        "transactions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("user_id", UUID(as_uuid=True)),
        sa.column("account_id", UUID(as_uuid=True)),
        sa.column("date", sa.Date()),
        sa.column("monthly_period_id", UUID(as_uuid=True)),
    )

    period_ids: dict[tuple[uuid.UUID, str], uuid.UUID] = {}

    def ensure_period(user_id: uuid.UUID, period: str) -> uuid.UUID:
        key = (user_id, period)
        existing = period_ids.get(key)
        if existing is not None:
            return existing
        period_id = uuid.uuid4()
        conn.execute(
            monthly_periods.insert().values(
                id=period_id,
                user_id=user_id,
                period=period,
                created_at=datetime.now(timezone.utc),
            )
        )
        period_ids[key] = period_id
        return period_id

    user_rows = conn.execute(sa.select(users.c.id, users.c.preferences)).fetchall()
    for row in user_rows:
        preferences = row.preferences or {}
        period = preferences.get("current_month_period")
        if isinstance(period, str) and len(period) == 7:
            ensure_period(row.id, period)

    transaction_rows = conn.execute(
        sa.select(transactions.c.id, transactions.c.user_id, transactions.c.account_id, transactions.c.date)
        .where(transactions.c.date.is_not(None))
    ).fetchall()
    account_periods: dict[uuid.UUID, uuid.UUID] = {}
    for row in transaction_rows:
        period = _period_from_date(row.date)
        period_id = ensure_period(row.user_id, period)
        conn.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(monthly_period_id=period_id)
        )
        account_periods.setdefault(row.account_id, period_id)

    account_rows = conn.execute(sa.select(accounts.c.id, accounts.c.user_id)).fetchall()
    for row in account_rows:
        period_id = account_periods.get(row.id)
        if period_id is None:
            preferences = next((u.preferences or {} for u in user_rows if u.id == row.user_id), {})
            period = preferences.get("current_month_period")
            if isinstance(period, str) and len(period) == 7:
                period_id = ensure_period(row.user_id, period)
        if period_id is not None:
            conn.execute(
                accounts.update()
                .where(accounts.c.id == row.id)
                .values(monthly_period_id=period_id)
            )

    if "categories" in inspector.get_table_names():
        for fk in inspector.get_foreign_keys("categories"):
            if "group_id" in (fk.get("constrained_columns") or []):
                op.drop_constraint(fk["name"], "categories", type_="foreignkey")
        category_columns = {column["name"] for column in inspector.get_columns("categories")}
        if "group_id" in category_columns:
            op.drop_column("categories", "group_id")

    if "category_groups" in inspector.get_table_names():
        op.drop_table("category_groups")


def downgrade() -> None:
    op.create_table(
        "category_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("icon", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
    )
    op.add_column(
        "categories",
        sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("category_groups.id"), nullable=True),
    )
    op.drop_index("ix_transactions_monthly_period_id", table_name="transactions")
    op.drop_index("ix_accounts_monthly_period_id", table_name="accounts")
    op.drop_column("transactions", "monthly_period_id")
    op.drop_column("accounts", "monthly_period_id")
    op.drop_index("ix_monthly_periods_user_id", table_name="monthly_periods")
    op.drop_table("monthly_periods")

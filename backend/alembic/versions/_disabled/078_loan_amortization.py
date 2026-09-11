"""Add loan amortization tables

Revision ID: 078_loan_amortization
Revises: 077_loan_and_sip_fields
Create Date: 2026-08-30 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '078_loan_amortization'
down_revision = '077_loan_and_sip_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Create loan_amortization_schedule table
    op.create_table(
        'loan_amortization_schedule',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('schedule_version', sa.Integer(), nullable=False),
        sa.Column('emi_number', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('principal_component', sa.Numeric(15, 2), nullable=False),
        sa.Column('interest_component', sa.Numeric(15, 2), nullable=False),
        sa.Column('emi_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('opening_balance', sa.Numeric(15, 2), nullable=False),
        sa.Column('closing_balance', sa.Numeric(15, 2), nullable=False),
        sa.Column('payment_status', sa.String(length=20), nullable=False),
        sa.Column('linked_transaction_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['linked_transaction_id'], ['transaction.id'], ondelete='SET NULL'),
    )

    # Create indexes for loan_amortization_schedule
    op.create_index(
        'idx_loan_schedule_account_version',
        'loan_amortization_schedule',
        ['account_id', 'schedule_version']
    )
    op.create_index(
        'idx_loan_schedule_due_date',
        'loan_amortization_schedule',
        ['due_date']
    )
    op.create_index(
        'idx_loan_schedule_status',
        'loan_amortization_schedule',
        ['payment_status']
    )
    op.create_index(
        'idx_loan_schedule_transaction',
        'loan_amortization_schedule',
        ['linked_transaction_id'],
        postgresql_where=sa.text('linked_transaction_id IS NOT NULL')
    )
    op.create_index(
        'idx_loan_schedule_workspace',
        'loan_amortization_schedule',
        ['workspace_id']
    )
    op.create_index(
        'idx_loan_schedule_composite',
        'loan_amortization_schedule',
        ['workspace_id', 'account_id', 'schedule_version', 'emi_number']
    )

    # Create loan_prepayment table
    op.create_table(
        'loan_prepayment',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prepayment_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('prepayment_date', sa.Date(), nullable=False),
        sa.Column('recalculation_method', sa.String(length=20), nullable=False),
        sa.Column('schedule_version_before', sa.Integer(), nullable=False),
        sa.Column('schedule_version_after', sa.Integer(), nullable=False),
        sa.Column('emi_change_amount', sa.Numeric(15, 2), nullable=True),
        sa.Column('tenure_change_months', sa.Integer(), nullable=True),
        sa.Column('interest_saved', sa.Numeric(15, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    )

    # Create indexes for loan_prepayment
    op.create_index(
        'idx_loan_prepayment_account',
        'loan_prepayment',
        ['account_id']
    )
    op.create_index(
        'idx_loan_prepayment_workspace',
        'loan_prepayment',
        ['workspace_id']
    )
    op.create_index(
        'idx_loan_prepayment_date',
        'loan_prepayment',
        ['prepayment_date']
    )

    # Add check constraints
    op.create_check_constraint(
        'ck_loan_schedule_payment_status',
        'loan_amortization_schedule',
        "payment_status IN ('scheduled', 'paid', 'partial', 'missed', 'skipped')"
    )
    op.create_check_constraint(
        'ck_loan_prepayment_method',
        'loan_prepayment',
        "recalculation_method IN ('reduce_emi', 'reduce_tenure')"
    )
    op.create_check_constraint(
        'ck_loan_schedule_positive_emi',
        'loan_amortization_schedule',
        'emi_number > 0'
    )
    op.create_check_constraint(
        'ck_loan_schedule_positive_version',
        'loan_amortization_schedule',
        'schedule_version > 0'
    )
    op.create_check_constraint(
        'ck_loan_prepayment_positive_amount',
        'loan_prepayment',
        'prepayment_amount > 0'
    )


def downgrade():
    # Drop tables (cascades will handle foreign keys)
    op.drop_table('loan_prepayment')
    op.drop_table('loan_amortization_schedule')

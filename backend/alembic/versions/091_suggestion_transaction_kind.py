"""letting a suggestion point at a transaction

The suggestion queue was built for promises somebody wrote down: an
invoice, or a recurring bill. Transfer pairing adds a third kind of
thing a payment can answer, and it is the odd one: the other leg is not
a promise at all, it is another statement line. Money leaving one
account *is* the expectation that the same money arrives in another, and
that is enough to be unsure about in exactly the same way.

So the check constraint learns one more word. Nothing else about the
table changes: the same pair is still asked about once, a declined pair
is still never offered again, and `expectation_id` still carries no
foreign key, for the same reason it never did. It has to point at
whichever table the kind names, and a column cannot point at three.

SQLite cannot alter a constraint in place and the test suite runs on it,
so the rebuild goes through a batch operation, which is a no-op shape on
Postgres beyond the DDL it emits anyway.
"""
from alembic import op

revision = "091"
down_revision = "090"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_suggestions"
_NAME = "ck_reconciliation_suggestion_kind"
_WITH_TRANSACTION = "expectation_kind IN ('invoice', 'recurring', 'transaction')"
_WITHOUT = "expectation_kind IN ('invoice', 'recurring')"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _WITH_TRANSACTION)


def downgrade() -> None:
    # The rows have to go before the constraint can come back, and they
    # are questions rather than records: a transfer suggestion nobody
    # answered is worth nothing once the feature that raised it is gone.
    op.execute(f"DELETE FROM {_TABLE} WHERE expectation_kind = 'transaction'")
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _WITHOUT)

"""let REGISTRATION_ENABLED decide until an administrator does

Migration 020 seeded `registration_enabled = 'true'` into app_settings.
A saved setting wins over the environment, the same way the saved
timezone wins over TZ, so the seeded row meant REGISTRATION_ENABLED was
never read on any install: setting it to false left registration open.

Only the seeded value goes. A row saying 'false' can only have been
written by an administrator, so it stays. A row saying 'true' looks the
same whether 020 or an administrator wrote it; dropping it changes
nothing unless REGISTRATION_ENABLED is false, which is exactly the
install that asked for registration to be closed.
"""
from alembic import op

revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM app_settings "
        "WHERE key = 'registration_enabled' AND LOWER(value) = 'true'"
    )


def downgrade() -> None:
    # Put the seed back where nothing has been saved since, so the
    # schema at 098 sees the row it always had.
    op.execute(
        "INSERT INTO app_settings (key, value) "
        "SELECT 'registration_enabled', 'true' "
        "WHERE NOT EXISTS "
        "(SELECT 1 FROM app_settings WHERE key = 'registration_enabled')"
    )

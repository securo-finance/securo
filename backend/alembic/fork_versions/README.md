# Fork migrations

This fork's migrations live here, on their own Alembic branch labelled `fork`.
Upstream's line in `../versions/` stays a single numbered chain, so its next
migration can never share an id with one of ours.

The branch starts at upstream revision `113`. The database has two heads, the
upstream head and `fork@head`, and every deploy runs `alembic upgrade heads`.

## Add a fork migration

1. From `backend/`, run:

   ```bash
   alembic revision --head fork@head --version-path alembic/fork_versions \
     --rev-id fork_004 -m "describe the change"
   ```

   Use the next free number. Alembic names the file `fork_004_<slug>.py`.
2. If the migration needs an upstream revision newer than `113`, add it to
   `depends_on`.

`tests/test_alembic_revisions.py` fails when the fork line forks, when it does
not start from an upstream revision, or when an upstream migration depends on
a fork one.

"""Fail-closed checks for historical Alembic revision collisions."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def reject_ambiguous_legacy_063(connection: Connection) -> None:
    """Reject the old local T212 repair stamp masquerading as upstream 063.

    Upstream revision 063 adds goals.asset_group_id. The superseded local T212
    repair also used revision id 063 but never created that column. Alembic
    identifies revisions solely by their string id, so continuing would silently
    skip upstream 063 and corrupt the migration lineage.
    """
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        return
    stamped = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if stamped != "063":
        return
    if "goals" not in inspector.get_table_names() or "asset_group_id" not in {
        item["name"] for item in inspector.get_columns("goals")
    }:
        raise RuntimeError(
            "ambiguous legacy Alembic revision 063 detected: this database may be stamped by "
            "the unreleased Trading 212 repair, not upstream goal tracking. Do not upgrade; "
            "restore/repair the schema and stamp the verified upstream lineage first."
        )

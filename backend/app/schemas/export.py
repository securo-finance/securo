from uuid import UUID

from pydantic import BaseModel, Field, SecretStr


class BackupRequest(BaseModel):
    """Options for a workspace backup.

    `password` is optional: omitted (or null) produces the plain zip. A
    SecretStr so the value never reaches a log line or a validation error
    through a repr. The floor is a length, not a character-class rule, since
    those push people towards shorter passwords they can satisfy.
    """

    password: SecretStr | None = Field(default=None, min_length=8, max_length=256)


class RestoreResult(BaseModel):
    """What a restore created, so the caller can open it and check the counts."""

    workspace_id: UUID
    workspace_name: str
    #: Rows written per table. `transaction_attachments` counts the files that
    #: were actually stored; the rest are reported in `attachments_missing`.
    entity_counts: dict[str, int]
    attachments_restored: int
    attachments_missing: int
    #: Rows the backup never carried, per family (`invoices`, `reconciliation`,
    #: `connections`, `plugins`, `ui_layouts`, `mortgage_allocations`), as the
    #: archive itself reported them. Empty for an archive that left nothing out
    #: and for one written before the export counted them.
    left_out: dict[str, int] = Field(default_factory=dict)

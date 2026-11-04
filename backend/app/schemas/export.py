from pydantic import BaseModel, Field, SecretStr


class BackupRequest(BaseModel):
    """Options for a workspace backup.

    `password` is optional: omitted (or null) produces the plain zip. A
    SecretStr so the value never reaches a log line or a validation error
    through a repr. The floor is a length, not a character-class rule, since
    those push people towards shorter passwords they can satisfy.
    """

    password: SecretStr | None = Field(default=None, min_length=8, max_length=256)

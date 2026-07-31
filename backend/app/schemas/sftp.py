"""Per-project SFTP account schemas."""

from pydantic import BaseModel, Field


class SftpEnableRequest(BaseModel):
    """SFTP credential setup request."""

    public_key: str | None = Field(default=None, max_length=16384)
    generate_password: bool = True


class SftpAccountResponse(BaseModel):
    """SFTP connection details with an optional one-time password."""

    enabled: bool
    username: str | None = None
    directory: str = "/uploads"
    one_time_password: str | None = None

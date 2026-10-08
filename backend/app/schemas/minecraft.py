"""Minecraft runtime, artifact and administration request schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION_PATTERN = r"^\d+(?:\.\d+){1,3}$"


class MinecraftConfigUpdate(BaseModel):
    """Validated Minecraft Forge server configuration."""

    java_version: int = Field(default=21)
    minecraft_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    forge_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    build_id: str | None = Field(default=None, pattern=r"^[0-9]{1,12}$")
    xms: str = Field(default="1G", pattern=r"^[1-9][0-9]{0,5}[MGmg]$")
    xmx: str = Field(default="4G", pattern=r"^[1-9][0-9]{0,5}[MGmg]$")
    server_jar: str = Field(default="forge-server.jar", pattern=r"^[A-Za-z0-9_.-]+\.jar$")
    launch_mode: str = Field(default="jar", pattern=r"^(jar|forge_script)$")
    game_port: int = Field(default=25565, ge=1024, le=65535)
    eula_accepted: bool
    rcon_enabled: bool = True
    rcon_port: int = Field(default=25575, ge=1024, le=65535)
    rcon_host_port: int = Field(default=25575, ge=1024, le=65535)
    rcon_password: str | None = Field(default=None, min_length=16, max_length=128)

    @field_validator("java_version")
    @classmethod
    def validate_java(cls, value: int) -> int:
        """Allow Java versions supported by current Forge generations."""
        if value not in {8, 11, 16, 17, 21, 25}:
            raise ValueError("Java version must be 8, 11, 16, 17, 21, or 25")
        return value

    @model_validator(mode="after")
    def validate_forge_pair(self) -> "MinecraftConfigUpdate":
        """Require Minecraft and Forge versions to be stored together."""
        if self.forge_version is not None and self.minecraft_version is None:
            raise ValueError("Minecraft and Forge versions must be provided together")
        return self


class MinecraftConfigResponse(BaseModel):
    """Minecraft configuration without its RCON secret."""

    configuration: dict[str, Any]
    rcon_configured: bool
    one_time_rcon_password: str | None = None
    engine: str = "forge"
    status: str = "created"
    active_job_id: str | None = None


class MinecraftTextFileResponse(BaseModel):
    """Editable Minecraft text file payload."""

    path: str
    content: str


class MinecraftTextFileUpdate(BaseModel):
    """Editable Minecraft text file replacement."""

    content: str = Field(max_length=2 * 1024 * 1024)


class MinecraftModEntry(BaseModel):
    """Installed Minecraft mod file metadata."""

    name: str
    path: str
    size_bytes: int


class MinecraftInstallRequest(BaseModel):
    """Automatic Forge coordinate or a legacy uploaded installer."""

    model_config = ConfigDict(extra="forbid")

    minecraft_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    forge_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    build_id: str | None = Field(default=None, pattern=r"^[0-9]{1,12}$")
    uploaded_jar: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_.-]+\.jar$", max_length=255
    )
    installer_jar: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.jar$",
        min_length=5,
        max_length=255,
    )

    @model_validator(mode="after")
    def validate_install_source(self) -> "MinecraftInstallRequest":
        """Require either one official version pair or one uploaded installer."""
        if self.uploaded_jar is not None:
            if (
                self.installer_jar is not None
                or self.forge_version is not None
                or self.build_id is not None
            ):
                raise ValueError("Uploaded JAR cannot be combined with an official build")
            return self
        sources = int(self.installer_jar is not None) + int(self.minecraft_version is not None)
        if sources != 1:
            raise ValueError("Provide exactly one installation source")
        if self.minecraft_version is not None and (
            (self.forge_version is None) == (self.build_id is None)
        ):
            raise ValueError("Provide either forge_version or build_id with minecraft_version")
        if self.minecraft_version is None and (
            self.forge_version is not None or self.build_id is not None
        ):
            raise ValueError("Build requires Minecraft version")
        return self


class MinecraftVersionOption(BaseModel):
    """Available Forge builds for one Minecraft release."""

    minecraft_version: str
    forge_versions: list[str]
    java_version: int


class MinecraftVersionCatalog(BaseModel):
    """Official Minecraft and Forge version catalog."""

    versions: list[MinecraftVersionOption]


class MinecraftBuildOption(BaseModel):
    """Selectable official build metadata without download credentials."""

    build_id: str
    channel: Literal["stable", "release", "unknown"]
    java_version: int


class MinecraftBuildCatalog(BaseModel):
    """Builds available for a selected release."""

    builds: list[MinecraftBuildOption]


class MinecraftPluginEntry(MinecraftModEntry):
    """Plugin JAR metadata and activation state."""

    enabled: bool


class MinecraftPluginAction(BaseModel):
    """Stopped-server plugin activation command."""

    action: Literal["enable", "disable"]


class MinecraftPlayerAction(BaseModel):
    """Validated Minecraft player administration command."""

    action: Literal["whitelist_add", "whitelist_remove", "op", "deop", "ban", "pardon", "kick"]
    player: str = Field(pattern=r"^[A-Za-z0-9_]{1,16}$")
    reason: str = Field(default="", max_length=256)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        """Reject command delimiters and control characters."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value

"""Minecraft Forge runtime configuration schemas."""

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

VERSION_PATTERN = r"^\d+(?:\.\d+){1,3}$"


class MinecraftConfigUpdate(BaseModel):
    """Validated Minecraft Forge server configuration."""

    java_version: int = Field(default=21)
    minecraft_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    forge_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
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
        if (self.minecraft_version is None) != (self.forge_version is None):
            raise ValueError("Minecraft and Forge versions must be provided together")
        return self


class MinecraftConfigResponse(BaseModel):
    """Minecraft configuration without its RCON secret."""

    configuration: dict[str, Any]
    rcon_configured: bool
    one_time_rcon_password: str | None = None


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

    minecraft_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    forge_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    installer_jar: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.jar$",
        min_length=5,
        max_length=255,
    )

    @model_validator(mode="after")
    def validate_install_source(self) -> "MinecraftInstallRequest":
        """Require either one official version pair or one uploaded installer."""
        has_versions = self.minecraft_version is not None or self.forge_version is not None
        if has_versions and (
            self.minecraft_version is None
            or self.forge_version is None
            or self.installer_jar is not None
        ):
            raise ValueError("Provide both versions without installer_jar")
        if not has_versions and self.installer_jar is None:
            raise ValueError("Provide Minecraft and Forge versions")
        return self


class MinecraftVersionOption(BaseModel):
    """Available Forge builds for one Minecraft release."""

    minecraft_version: str
    forge_versions: list[str]
    java_version: int


class MinecraftVersionCatalog(BaseModel):
    """Official Minecraft and Forge version catalog."""

    versions: list[MinecraftVersionOption]

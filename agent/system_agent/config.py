"""System agent environment configuration."""

from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_AGENT_TOKEN = "development-agent-token"  # noqa: S105


class AgentSettings(BaseSettings):
    """Validated agent configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    agent_token: str = DEVELOPMENT_AGENT_TOKEN
    storage_root: Path = Path("/srv/vps-panel")
    nginx_config_root: Path = Path("/etc/nginx/vps-panel")
    letsencrypt_root: Path = Path("/etc/letsencrypt")
    certbot_binary: Path = Path("/usr/bin/certbot")
    openssl_binary: Path = Path("/usr/bin/openssl")
    nginx_binary: Path = Path("/usr/sbin/nginx")
    systemctl_binary: Path = Path("/usr/bin/systemctl")
    docker_binary: Path = Path("/usr/bin/docker")
    mysql_binary: Path = Path("/usr/bin/mysql")
    mariadb_binary: Path = Path("/usr/bin/mariadb")
    psql_binary: Path = Path("/usr/bin/psql")
    pm2_runtime_binary: Path = Path("/usr/bin/pm2-runtime")
    useradd_binary: Path = Path("/usr/sbin/useradd")
    userdel_binary: Path = Path("/usr/sbin/userdel")
    runuser_binary: Path = Path("/usr/sbin/runuser")
    chpasswd_binary: Path = Path("/usr/sbin/chpasswd")
    sshd_binary: Path = Path("/usr/sbin/sshd")
    sshd_config_root: Path = Path("/etc/ssh/sshd_config.d")
    mount_binary: Path = Path("/usr/bin/mount")
    umount_binary: Path = Path("/usr/bin/umount")
    mountpoint_binary: Path = Path("/usr/bin/mountpoint")
    systemd_escape_binary: Path = Path("/usr/bin/systemd-escape")
    systemd_unit_root: Path = Path("/etc/systemd/system")
    project_runtime_user: str = "vps-panel"
    project_runtime_group: str = "vps-panel"
    project_runtime_user_prefix: str = "np"
    sudo_binary: Path = Path("/usr/bin/sudo")
    privileged_helper: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("privileged_helper", "AGENT_PRIVILEGED_HELPER"),
    )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "AgentSettings":
        """Reject development credentials outside local environments."""
        if self.environment.lower() not in {"development", "test"} and (
            self.agent_token == DEVELOPMENT_AGENT_TOKEN or len(self.agent_token) < 32
        ):
            raise ValueError("AGENT_TOKEN must be a unique secret with at least 32 characters")
        if self.environment.lower() not in {"development", "test"} and (
            self.privileged_helper is None or not self.privileged_helper.is_absolute()
        ):
            raise ValueError("AGENT_PRIVILEGED_HELPER is required in production")
        return self


settings = AgentSettings()

"""Core persistence entities for NectarinePanel."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class ProjectType(StrEnum):
    """Supported project categories."""

    WEB = "web"
    BACKEND = "backend"
    BOT = "bot"
    DOCKER = "docker"
    STATIC = "static"
    MINECRAFT_FORGE = "minecraft_forge"
    MINECRAFT_PAPER = "minecraft_paper"
    MINECRAFT_PURPUR = "minecraft_purpur"
    MINECRAFT_SPIGOT = "minecraft_spigot"


class RuntimeType(StrEnum):
    """Supported project runtime strategies."""

    DOCKER = "docker"
    DOCKER_COMPOSE = "docker_compose"
    SYSTEMD = "systemd"
    PM2 = "pm2"
    STATIC = "static"
    MINECRAFT_FORGE = "minecraft_forge"
    MINECRAFT_PAPER = "minecraft_paper"
    MINECRAFT_PURPUR = "minecraft_purpur"
    MINECRAFT_SPIGOT = "minecraft_spigot"


MINECRAFT_TYPES = frozenset(
    {"minecraft_forge", "minecraft_paper", "minecraft_purpur", "minecraft_spigot"}
)
BUKKIT_TYPES = MINECRAFT_TYPES - {"minecraft_forge"}


class ProjectStatus(StrEnum):
    """Project lifecycle states."""

    CREATED = "created"
    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class UserRole(StrEnum):
    """Supported global user roles."""

    OWNER = "owner"
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    VIEWER = "viewer"


class ProjectMemberRole(StrEnum):
    """Supported project-scoped member roles."""

    MAINTAINER = "maintainer"
    VIEWER = "viewer"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Panel account with a global role."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.OWNER, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    project_memberships: Mapped[list["ProjectMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hashed and revocable authentication refresh token."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class LoginChallenge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Short-lived Telegram two-factor login challenge."""

    __tablename__ = "login_challenges"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deployable project and its runtime configuration."""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    project_type: Mapped[str] = mapped_column(String(32))
    runtime_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.CREATED)
    source_type: Mapped[str | None] = mapped_column(String(32))
    repository_url: Mapped[str | None] = mapped_column(String(2048))
    branch: Mapped[str] = mapped_column(String(255), default="main")
    install_command: Mapped[str | None] = mapped_column(Text)
    build_command: Mapped[str | None] = mapped_column(Text)
    start_command: Mapped[str | None] = mapped_column(Text)
    output_directory: Mapped[str | None] = mapped_column(String(512))
    healthcheck_url: Mapped[str | None] = mapped_column(String(2048))
    notes: Mapped[str | None] = mapped_column(Text)
    runtime_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    environment_variables: Mapped[list["EnvironmentVariable"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["ProjectMembership"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    resource_policy: Mapped["ProjectResourcePolicy | None"] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Project-scoped user role assignment."""

    __tablename__ = "project_memberships"
    __table_args__ = (
        Index("uq_project_membership_project_user", "project_id", "user_id", unique=True),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    project: Mapped[Project] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="project_memberships")


class ProjectResourcePolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-project resource limit policy."""

    __tablename__ = "project_resource_policies"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cpu_cores: Mapped[float | None] = mapped_column(Float)
    memory_mb: Mapped[int | None] = mapped_column(Integer)
    disk_mb: Mapped[int | None] = mapped_column(Integer)
    project: Mapped[Project] = relationship(back_populates="resource_policy")


class ProjectRuntime(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized runtime state for a project."""

    __tablename__ = "project_runtimes"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    runtime_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="created")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )


class ProjectSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized project deployment source and encrypted credential."""

    __tablename__ = "project_sources"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    source_type: Mapped[str] = mapped_column(String(32))
    repository_url: Mapped[str | None] = mapped_column(String(2048))
    branch: Mapped[str | None] = mapped_column(String(255))
    encrypted_credential: Mapped[str | None] = mapped_column(Text)


class EnvironmentVariable(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Encrypted project environment variable."""

    __tablename__ = "environment_variables"
    __table_args__ = (Index("uq_environment_project_key", "project_id", "key", unique=True),)

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(255))
    encrypted_value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True)
    project: Mapped[Project] = relationship(back_populates="environment_variables")


class EnvironmentVariableVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable environment variable history entry."""

    __tablename__ = "environment_variable_versions"
    __table_args__ = (Index("ix_environment_versions_project_key", "project_id", "key"),)

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(255))
    encrypted_value: Mapped[str | None] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True)
    value_sha256: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class Deployment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable project deployment record."""

    __tablename__ = "deployments"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    source_revision: Mapped[str | None] = mapped_column(String(255))
    release_path: Mapped[str | None] = mapped_column(String(1024))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[Project] = relationship(back_populates="deployments")


class DeploymentLog(UUIDPrimaryKeyMixin, Base):
    """Ordered deployment log fragment."""

    __tablename__ = "deployment_logs"
    __table_args__ = (Index("ix_deployment_logs_order", "deployment_id", "sequence"),)

    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Domain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Domain and SSL configuration attached to a project."""

    __tablename__ = "domains"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    hostname: Mapped[str] = mapped_column(String(253), unique=True)
    upstream_port: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_status: Mapped[str] = mapped_column(String(32), default="disabled")
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SslCertificate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Certificate metadata associated with a domain."""

    __tablename__ = "ssl_certificates"

    domain_id: Mapped[str] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), unique=True
    )
    issuer: Mapped[str] = mapped_column(String(255), default="Let's Encrypt")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint_sha256: Mapped[str | None] = mapped_column(String(64))


class Backup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Project or database backup artifact."""

    __tablename__ = "backups"

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    database_id: Mapped[str | None] = mapped_column(String(36), index=True)
    backup_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    path: Mapped[str | None] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BackupItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One manifest item included in a backup."""

    __tablename__ = "backup_items"

    backup_id: Mapped[str] = mapped_column(
        ForeignKey("backups.id", ondelete="CASCADE"), index=True
    )
    item_type: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))


class BackupPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Scheduled project backup policy."""

    __tablename__ = "backup_policies"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[str] = mapped_column(String(120), default="0 3 * * *")
    retention_count: Mapped[int] = mapped_column(Integer, default=7)
    include_items: Mapped[list[str]] = mapped_column(JSON, default=lambda: [".env"])
    include_databases: Mapped[bool] = mapped_column(Boolean, default=True)
    encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable long-running file operation."""

    __tablename__ = "file_operations"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CronJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Scheduled project command executed by the worker."""

    __tablename__ = "cron_jobs"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    expression: Mapped[str] = mapped_column(String(120))
    command: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(32))


class SftpAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-project restricted SFTP account metadata."""

    __tablename__ = "sftp_accounts"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    username: Mapped[str] = mapped_column(String(32), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    public_key_fingerprint: Mapped[str | None] = mapped_column(String(64))


class DatabaseServer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Connection metadata for a managed database server."""

    __tablename__ = "database_servers"

    name: Mapped[str] = mapped_column(String(120), unique=True)
    engine: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    admin_username: Mapped[str] = mapped_column(String(255))
    encrypted_admin_password: Mapped[str] = mapped_column(Text)
    tls_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DatabaseInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Managed database assigned to a project."""

    __tablename__ = "database_instances"
    __table_args__ = (
        Index("uq_database_instance_server_name", "server_id", "name", unique=True),
    )

    server_id: Mapped[str] = mapped_column(
        ForeignKey("database_servers.id", ondelete="RESTRICT")
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(63))
    username: Mapped[str] = mapped_column(String(63))
    encrypted_password: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")


class DatabaseUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Managed database user with encrypted credentials."""

    __tablename__ = "database_users"

    database_id: Mapped[str] = mapped_column(
        ForeignKey("database_instances.id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str] = mapped_column(String(63))
    encrypted_password: Mapped[str] = mapped_column(Text)
    privileges: Mapped[list[str]] = mapped_column(JSON, default=list)


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Long-running job with realtime progress."""

    __tablename__ = "jobs"

    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only security and operations audit event."""

    __tablename__ = "audit_logs"

    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class MetricSample(UUIDPrimaryKeyMixin, Base):
    """Host or project monitoring sample."""

    __tablename__ = "metric_samples"

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON)


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent owner notification and alert."""

    __tablename__ = "notifications"

    notification_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Recorded privileged agent operation."""

    __tablename__ = "agent_commands"

    operation: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class TelegramAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Allowlisted Telegram owner binding."""

    __tablename__ = "telegram_accounts"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Webhook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authenticated deployment webhook configuration."""

    __tablename__ = "webhooks"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    secret_digest: Mapped[str] = mapped_column(String(64))
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Encrypted or plain application setting."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(255), unique=True)
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)


class OneTimeDownloadLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Expiring one-time reference to a protected artifact."""

    __tablename__ = "one_time_download_links"

    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Multi-user RBAC and project memberships.
- Project secret versioning, reveal audit, bulk import, and rollback.
- Resource guardrails and resource-limit notifications.
- Deploy Center with Git/ZIP deployment progress, logs, and rollback.
- Persistent Docker project mounts for application data.
- GitHub CI, dependency updates, contribution templates, and multilingual docs.
- Panel interface localization for English, Russian, Ukrainian, and Polish.

### Fixed

- Fresh SQLite Alembic migrations now upgrade through the current head.
- Production startup rejects missing or malformed field-encryption keys.
- Local Compose ports bind to loopback and service containers run unprivileged.

[Unreleased]: https://github.com/JanekDeveloper/NectarinePanel/compare/v0.1.0...HEAD

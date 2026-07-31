# Databases

**English** · [Українська](../uk/databases.md) · [Русский](../ru/databases.md) · [Polski](../pl/databases.md)

Database server credentials are stored separately from created database
instances. PostgreSQL and MySQL/MariaDB administration uses parameter-safe
driver APIs or validated identifiers in fixed statements. User-controlled
values are never passed to a shell.

Supported modes:

- PostgreSQL, MySQL and MariaDB: database/user provisioning, dumps, restore and
  deletion;
- SQLite: panel-contained files, native consistent backup, integrity-checked
  restore and deletion while the attached project is stopped;
- Redis and Valkey: encrypted connection storage plus bounded health, version,
  uptime, client and memory information. The panel does not fake logical
  database isolation or ACL provisioning where the server cannot enforce the
  requested isolation.

Exports and imports run as background jobs with bounded logs. Restore requires
explicit confirmation and a validated engine-compatible dump. Adminer is an
optional private Compose profile and must only be published through an
authenticated panel route.

`GET /api/v1/databases/{id}/tables` returns a bounded basic table list for ready
PostgreSQL, MySQL/MariaDB and SQLite instances. Redis and Valkey return a
conflict response because they do not expose relational tables.

Deletion is also asynchronous and requires the exact database name. The worker
first drops the PostgreSQL/MySQL database and its dedicated role/user. Local
metadata is removed only after the remote operation succeeds; failures remain
visible as `delete_failed`. An attached `DATABASE_URL` is removed only when its
decrypted value exactly matches the deleted instance, so unrelated project
configuration is preserved.

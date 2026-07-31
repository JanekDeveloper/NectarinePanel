# Release process

**English** · [Українська](../uk/releasing.md) · [Русский](../ru/releasing.md) · [Polski](../pl/releasing.md)

1. Run the full local gate:

   ```bash
   make check
   docker compose --profile telegram build backend frontend worker agent telegram-bot
   ```

2. Review `git status`, the complete diff, migration order, dependency audit,
   and generated frontend lockfile. Confirm no `.env`, database, backup, token,
   private key, or private hostname was added.
3. Move relevant entries from `Unreleased` into a versioned changelog section.
4. Update `APP_VERSION` in `backend/app/version.py` and
   `RESTORE_COMPATIBILITY` only when backup compatibility changes.
5. Merge only after CI passes, then create a signed `vX.Y.Z` tag and a GitHub
   release with upgrade and rollback notes.
6. Publish the tagged installer URL and checksum. Never recommend piping a
   moving branch into a root shell.

Before announcing the release, verify installation on a clean Ubuntu 24.04 VPS
and restore a backup on a disposable host.

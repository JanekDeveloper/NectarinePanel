"""Typed backend client used by Telegram handlers."""

from pathlib import Path
from typing import Any, cast

import httpx

from nectarine_bot.config import settings


class PanelApi:
    """Call authenticated internal bot endpoints on the panel backend."""

    def __init__(self) -> None:
        """Initialize a bounded asynchronous HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=settings.backend_internal_url,
            timeout=httpx.Timeout(30, connect=5),
            headers={"X-Telegram-Service-Token": settings.telegram_api_token},
        )

    async def close(self) -> None:
        """Close pooled backend connections."""
        await self._client.aclose()

    async def runtime_config(self) -> dict[str, Any]:
        """Load effective Telegram credentials over the internal service channel."""
        response = await self._client.get("/telegram/runtime-config")
        response.raise_for_status()
        return self._json_object(response)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        """Decode a JSON response object with an explicit runtime shape."""
        return cast(dict[str, Any], response.json())

    @staticmethod
    def _json_string_object(response: httpx.Response) -> dict[str, str]:
        """Decode a string-only JSON response object."""
        return cast(dict[str, str], response.json())

    @staticmethod
    def _json_object_list(response: httpx.Response) -> list[dict[str, Any]]:
        """Decode a JSON response list with object items."""
        return cast(list[dict[str, Any]], response.json())

    async def projects(self) -> list[dict[str, Any]]:
        """Return projects visible to the owner bot."""
        response = await self._client.get("/telegram/projects")
        response.raise_for_status()
        return self._json_object_list(response)

    async def bind_owner(self, telegram_user_id: int, username: str | None) -> dict[str, Any]:
        """Bind the configured Telegram owner to the panel account."""
        response = await self._client.post(
            "/telegram/bind",
            json={"telegram_user_id": telegram_user_id, "username": username},
        )
        response.raise_for_status()
        return self._json_object(response)

    async def approve_2fa(
        self, challenge_token: str, telegram_user_id: int, username: str | None
    ) -> dict[str, Any]:
        """Approve a pending Telegram two-factor login challenge."""
        response = await self._client.post(
            f"/telegram/2fa/{challenge_token}/approve",
            json={"telegram_user_id": telegram_user_id, "username": username},
        )
        response.raise_for_status()
        return self._json_object(response)

    async def project_action(self, project_id: str, action: str) -> dict[str, Any]:
        """Queue a confirmed project lifecycle action."""
        response = await self._client.post(
            f"/telegram/projects/{project_id}/actions", json={"action": action}
        )
        response.raise_for_status()
        return self._json_object(response)

    async def databases(self) -> list[dict[str, Any]]:
        """Return managed databases available for export."""
        response = await self._client.get("/telegram/databases")
        response.raise_for_status()
        return self._json_object_list(response)

    async def backups(self) -> list[dict[str, Any]]:
        """Return ready backup artifacts."""
        response = await self._client.get("/telegram/backups")
        response.raise_for_status()
        return self._json_object_list(response)

    async def backup_artifact(self, backup_id: str) -> tuple[str, bytes]:
        """Download a ready backup over the private service API."""
        response = await self._client.get(f"/telegram/backups/{backup_id}/artifact")
        response.raise_for_status()
        disposition = response.headers.get("content-disposition", "")
        filename = disposition.split("filename=", 1)[-1].strip('"') or "backup.bin"
        return filename, response.content

    async def backup_metadata(self, backup_id: str) -> dict[str, Any]:
        """Return artifact metadata without downloading the file."""
        response = await self._client.get(f"/telegram/backups/{backup_id}")
        response.raise_for_status()
        return self._json_object(response)

    async def backup_download_link(self, backup_id: str) -> dict[str, str]:
        """Create an expiring link for an oversized backup."""
        response = await self._client.post(f"/telegram/backups/{backup_id}/download-link")
        response.raise_for_status()
        return self._json_string_object(response)

    async def request_export(self, resource: str, resource_id: str) -> dict[str, Any]:
        """Queue a database or backup export."""
        response = await self._client.post(f"/telegram/{resource}/{resource_id}/export")
        response.raise_for_status()
        return self._json_object(response)

    async def upload_restore(
        self,
        resource: str,
        resource_id: str,
        file_path: Path,
        telegram_user_id: int,
        username: str | None,
    ) -> dict[str, Any]:
        """Upload an owner-provided restore artifact."""
        with file_path.open("rb") as handle:
            response = await self._client.post(
                f"/telegram/{resource}/{resource_id}/restore",
                data={
                    "telegram_user_id": str(telegram_user_id),
                    "username": username or "",
                },
                files={"file": (file_path.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()
        return self._json_object(response)

    async def restore_backup(
        self, backup_id: str, telegram_user_id: int, username: str | None
    ) -> dict[str, Any]:
        """Queue restore of an existing project backup."""
        response = await self._client.post(
            f"/telegram/backups/{backup_id}/restore",
            json={"telegram_user_id": telegram_user_id, "username": username},
        )
        response.raise_for_status()
        return self._json_object(response)

"""Authenticated client for the local system agent."""

from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import Settings


class AgentClient:
    """Call allowlisted local system operations with bounded timeouts."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the agent client from application settings."""
        self._url = settings.agent_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.agent_token}"}

    async def execute(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Execute one named operation and return its result."""
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.post(
                f"{self._url}/v1/operations",
                headers=self._headers,
                json={"operation": operation, "parameters": parameters or {}},
            )
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        return cast(dict[str, Any], payload["result"])

    def websocket_url(self, path: str) -> str:
        """Build an internal WebSocket URL for an agent streaming endpoint."""
        parsed = urlsplit(self._url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        endpoint = f"{base_path}/{path.lstrip('/')}"
        return urlunsplit((scheme, parsed.netloc, endpoint, "", ""))

    @property
    def authorization_headers(self) -> dict[str, str]:
        """Return a copy of the internal agent authorization headers."""
        return dict(self._headers)

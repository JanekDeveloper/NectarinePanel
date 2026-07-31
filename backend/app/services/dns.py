"""DNS provider contracts and hostname verification."""

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class DnsVerification:
    """Resolved DNS state for a hostname."""

    hostname: str
    addresses: tuple[str, ...]
    matches_expected_ip: bool


class DnsProvider(ABC):
    """Contract for DNS verification and future record management."""

    @abstractmethod
    async def verify(self, hostname: str, expected_ip: str | None) -> DnsVerification:
        """Verify that a hostname resolves as expected."""


class ManualDnsProvider(DnsProvider):
    """Read-only provider for manually managed DNS records."""

    async def verify(self, hostname: str, expected_ip: str | None) -> DnsVerification:
        """Resolve A/AAAA records through the system resolver."""
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                hostname,
                None,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError("Hostname does not resolve") from exc
        addresses = tuple(
            sorted({record[4][0] for record in records}, key=ipaddress.ip_address)
        )
        matches = expected_ip is None or expected_ip in addresses
        return DnsVerification(hostname, addresses, matches)


class CloudflareDnsProvider(ManualDnsProvider):
    """Experimental Cloudflare adapter limited to verification."""

    def __init__(self, api_token: str) -> None:
        """Store a token for the future record-management implementation."""
        if not api_token.strip():
            raise ValueError("Cloudflare API token is required")
        self._api_token = api_token

    async def create_record(self, hostname: str, address: str) -> None:
        """Reject record creation until the adapter is explicitly enabled."""
        del hostname, address
        raise RuntimeError("Cloudflare record management is experimental and disabled")

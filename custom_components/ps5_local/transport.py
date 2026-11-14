"""Read-only PS5 discovery and device-identity validation."""

import asyncio
import re
import socket
from dataclasses import dataclass
from ipaddress import IPv4Address

from ._remoteplay import DeviceStatus

_QUERY = b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n"
_PORT = 9302
_STATUSES = {200: DeviceStatus.AWAKE, 620: DeviceStatus.STANDBY}


class ConsoleUnavailable(Exception):
    """No verified status reply arrived for the configured console."""


@dataclass(frozen=True)
class Console:
    """Status fields supplied by a single PS5 discovery reply."""

    host: str
    identifier: str
    name: str
    status: DeviceStatus
    firmware: str | None
    activity: str | None
    title_id: str | None


def parse_reply(payload: bytes, host: str) -> Console | None:
    """Accept known status codes and a stable PS5 hardware identifier."""
    try:
        lines = payload.decode().splitlines()
    except UnicodeDecodeError:
        return None
    if not lines:
        return None
    status_line = re.fullmatch(r"HTTP/1\.1 (200|620)(?: .*)?", lines[0])
    if status_line is None:
        return None
    fields = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.lower(), value.strip())
    identifier = fields.get("host-id", "").lower()
    if fields.get("host-type") != "PS5" or not re.fullmatch(
        r"[0-9a-f]{12}", identifier
    ):
        return None
    status = _STATUSES[int(status_line[1])]
    # A standby reply may carry stale activity. Only expose it while awake.
    return Console(
        host,
        identifier,
        fields.get("host-name") or "PlayStation 5",
        status,
        fields.get("system-version") or None,
        (fields.get("running-app-name") or None)
        if status == DeviceStatus.AWAKE
        else None,
        (fields.get("running-app-titleid") or None)
        if status == DeviceStatus.AWAKE
        else None,
    )


def console_address(value: str) -> str:
    """Require a usable unicast IPv4 address for manual setup."""
    address = IPv4Address(value.strip())
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or int(address) == 0xFFFFFFFF
    ):
        raise ValueError("Invalid console address")
    return str(address)


async def discover(
    host: str | None = None,
    *,
    expected_id: str | None = None,
    timeout: float = 3.0,
) -> list[Console]:
    """Collect bounded replies over a per-request socket without shared ports."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    consoles: dict[str, Console] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        if host is None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        target = (host or "255.255.255.255", _PORT)
        while loop.time() < deadline:
            await loop.sock_sendto(sock, _QUERY, target)
            retry_at = min(deadline, loop.time() + 1)
            while (remaining := retry_at - loop.time()) > 0:
                try:
                    async with asyncio.timeout(remaining):
                        payload, (sender, _) = await loop.sock_recvfrom(sock, 4096)
                except TimeoutError:
                    break
                if host is not None and sender != host:
                    continue
                console = parse_reply(payload, sender)
                if console is None or (
                    expected_id and console.identifier != expected_id
                ):
                    continue
                if host is not None:
                    return [console]
                consoles[console.identifier] = console
    return list(consoles.values())


async def read_console(host: str, expected_id: str | None = None) -> Console:
    """Treat missing or mismatched replies as unavailable."""
    results = await discover(host, expected_id=expected_id)
    if not results:
        raise ConsoleUnavailable
    return results[0]

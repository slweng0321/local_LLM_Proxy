from __future__ import annotations

import aiohttp
from dataclasses import dataclass, field


@dataclass(slots=True)
class RuntimeClients:
    """
    Shared async clients to avoid repeated connection setup.
    """
    session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=None)
            connector = aiohttp.TCPConnector(
                limit=100,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
            )
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                raise_for_status=False,
            )

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
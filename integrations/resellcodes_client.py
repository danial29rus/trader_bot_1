from __future__ import annotations

import asyncio
from typing import Any

import httpx


class ReSellCodesClient:
    """Read-only catalogue client. It intentionally has no order methods."""
    base_url = "https://resell.codes/api/v1"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("RESELLCODES_API_KEY is not configured")
        self.client = httpx.AsyncClient(base_url=self.base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=httpx.Timeout(25.0, connect=10.0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(5):
            response = await self.client.get(path, params=params)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            wait = min(int(response.headers.get("Retry-After", "1")), 60)
            await asyncio.sleep(wait + attempt)
        raise RuntimeError("ReSellCodes rate limit retry limit exceeded")

    async def list_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", 100)
        output: list[dict[str, Any]] = []
        while True:
            payload = await self.get(path, params)
            output.extend(payload.get("data", []))
            cursor = payload.get("next_cursor")
            if not payload.get("has_more") or cursor is None:
                return output
            params["cursor"] = cursor


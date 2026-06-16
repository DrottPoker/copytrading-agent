from typing import Any

import httpx

from app.core.config import Settings, get_settings


class HyperliquidLeaderboardError(RuntimeError):
    pass


class HyperliquidLeaderboardClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def get_leaderboard(self) -> dict[str, Any]:
        url = self.settings.leaderboard_import_url or self.default_leaderboard_url
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise HyperliquidLeaderboardError(
                f"Hyperliquid leaderboard request failed with status {response.status_code}."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise HyperliquidLeaderboardError("Hyperliquid leaderboard response was not an object.")
        return payload

    @property
    def default_leaderboard_url(self) -> str:
        network = "Testnet" if self.settings.hyperliquid_network == "testnet" else "Mainnet"
        return f"https://stats-data.hyperliquid.xyz/{network}/leaderboard"

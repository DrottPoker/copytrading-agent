from pydantic import Field

from app.schemas.base import CamelModel


class LeaderboardWalletImport(CamelModel):
    rank: int
    address: str
    display_name: str | None
    account_value: str | None
    window_pnl: str | None = None
    window_roi: str | None = None
    account_role: str = "master"
    parent_address: str | None = None
    subaccount_name: str | None = None


class LeaderboardFillImport(CamelModel):
    wallet_address: str
    fetched: int = 0
    inserted: int = 0
    duplicate: int = 0
    error: str | None = None


class LeaderboardImportResponse(CamelModel):
    fetched: int
    valid: int
    inserted: int
    duplicate: int
    skipped: int
    limit: int
    window: str
    sort_metric: str
    imported: list[LeaderboardWalletImport]
    fill_imports: list[LeaderboardFillImport]
    fill_import_wallets: int = 0
    fill_import_fetched: int = 0
    fill_import_inserted: int = 0
    fill_import_duplicate: int = 0
    fill_import_failed: int = 0
    pruned_non_perp_wallets: int = 0
    pruned_non_perp_addresses: list[str] = Field(default_factory=list)

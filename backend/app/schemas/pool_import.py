from app.schemas.base import CamelModel


class PoolFillImportItem(CamelModel):
    wallet_address: str
    fetched: int = 0
    inserted: int = 0
    duplicate: int = 0
    error: str | None = None


class PoolFillImportResponse(CamelModel):
    scanned: int
    imported_wallets: int
    fetched: int
    inserted: int
    duplicate: int
    failed: int
    limit: int
    items: list[PoolFillImportItem]

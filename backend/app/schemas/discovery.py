from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelModel


class DiscoverySourceRead(CamelModel):
    key: str
    label: str
    provider: str
    enabled: bool
    configured: bool
    notes: str | None = None


class DiscoverySourceListResponse(CamelModel):
    items: list[DiscoverySourceRead]


class DiscoveryCandidateRead(CamelModel):
    id: UUID
    wallet_address: str
    source: str
    source_rank: int | None
    source_label: str | None
    source_cohort: str | None
    account_value: Decimal | None
    source_pnl: Decimal | None
    source_roi: Decimal | None
    source_copy_score: Decimal | None
    account_role: str
    parent_address: str | None
    subaccount_name: str | None
    status: str
    fail_reason: str | None
    backfill_status: str
    backfill_error: str | None
    last_backfilled_at: datetime | None
    backfill_fetched_count: int
    backfill_inserted_count: int
    backfill_duplicate_count: int
    fill_count: int
    closed_trade_count: int
    open_trade_count: int
    ignored_fill_count: int
    net_pnl_usd: Decimal | None
    profit_factor: Decimal | None
    win_rate: Decimal | None
    max_drawdown_pct: Decimal | None
    average_trade_notional_usd: Decimal | None
    last_trade_time_ms: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class DiscoveryCandidateListResponse(CamelModel):
    items: list[DiscoveryCandidateRead]
    total: int
    limit: int
    offset: int


class DiscoveryImportRunRead(CamelModel):
    id: UUID
    source: str
    status: str
    requested_limit: int
    fetched_count: int
    candidate_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class DiscoveryImportRunListResponse(CamelModel):
    items: list[DiscoveryImportRunRead]
    total: int
    limit: int
    offset: int


class DiscoveryPrefilterResponse(CamelModel):
    evaluated: int
    accepted: int
    rejected: int
    unchanged: int
    reject_reasons: dict[str, int]
    candidates: list[DiscoveryCandidateRead]


class DiscoveryBackfillItem(CamelModel):
    wallet_address: str
    source: str
    status: str
    fail_reason: str | None
    pool_action: str | None = None
    fetched: int
    inserted: int
    duplicate: int
    fill_count: int
    closed_trade_count: int
    open_trade_count: int
    ignored_fill_count: int
    net_pnl_usd: Decimal | None
    profit_factor: Decimal | None
    win_rate: Decimal | None
    max_drawdown_pct: Decimal | None
    error: str | None = None


class DiscoveryBackfillResponse(CamelModel):
    scanned: int
    backfilled: int
    accepted: int
    rejected: int
    promoted: int
    pool_inserted: int
    pool_duplicate: int
    failed: int
    skipped: int
    reject_reasons: dict[str, int]
    items: list[DiscoveryBackfillItem]


class DiscoveryPromoteItem(CamelModel):
    wallet_address: str
    source: str
    action: str
    label: str | None
    already_in_pool: bool
    reason: str | None = None


class DiscoveryPromoteResponse(CamelModel):
    scanned: int
    promoted: int
    inserted: int
    duplicate: int
    skipped: int
    items: list[DiscoveryPromoteItem]


class DiscoveryImportResponse(CamelModel):
    requested_sources: list[str]
    limit: int
    runs: list[DiscoveryImportRunRead]
    candidates: list[DiscoveryCandidateRead]
    fetched: int
    candidate_count: int
    inserted: int
    updated: int
    skipped: int
    skip_reasons: dict[str, int]
    failed_sources: int
    prefilter: DiscoveryPrefilterResponse | None = None
    backfill: DiscoveryBackfillResponse | None = None

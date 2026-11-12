"""Batch results and source allocations shared by the paper and live copy engines."""

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.copy_execution_cache import AllocationSnapshotCache

ZERO = Decimal("0")


@dataclass(frozen=True)
class CopySourceAllocation:
    source_wallet: str
    source_label: str | None
    rank: int
    pool_rank: int | None
    score: Decimal | None
    allocation_pct: Decimal
    active: bool
    has_realtime_slot: bool
    status_reason: str


CopyAllocationCache = AllocationSnapshotCache[dict[str, CopySourceAllocation]]


@dataclass(frozen=True)
class CopyBatchResult:
    processed_fills: int = 0
    skipped_fills: int = 0
    accounts_updated: int = 0
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    skip_reasons: dict[str, int] = field(default_factory=dict)


def combine_batch_results(
    left: CopyBatchResult,
    right: CopyBatchResult,
) -> CopyBatchResult:
    return CopyBatchResult(
        processed_fills=left.processed_fills + right.processed_fills,
        skipped_fills=left.skipped_fills + right.skipped_fills,
        accounts_updated=left.accounts_updated + right.accounts_updated,
        realized_pnl_usd=left.realized_pnl_usd + right.realized_pnl_usd,
        fee_usd=left.fee_usd + right.fee_usd,
        skip_reasons=combine_skip_reasons(left.skip_reasons, right.skip_reasons),
    )


def combine_skip_reasons(
    left: dict[str, int],
    right: dict[str, int],
) -> dict[str, int]:
    reasons = dict(left)
    for reason, count in right.items():
        reasons[reason] = reasons.get(reason, 0) + count
    return reasons

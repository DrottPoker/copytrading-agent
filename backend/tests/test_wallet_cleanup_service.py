import pytest

from app.schemas.wallet_cleanup import (
    LowScorePruneResponse,
    MaxDrawdownPruneResponse,
    MinClosedTradesPruneResponse,
    OrphanFillPruneResponse,
    StaleFillPruneResponse,
    ZeroFillWalletPruneResponse,
)
from app.services import wallet_cleanup_service


@pytest.mark.asyncio
async def test_prune_all_does_not_run_current_drawdown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def prune_orphan_stub(*_args: object, **_kwargs: object) -> OrphanFillPruneResponse:
        return OrphanFillPruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            items=[],
        )

    async def prune_zero_stub(*_args: object, **_kwargs: object) -> ZeroFillWalletPruneResponse:
        return ZeroFillWalletPruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            items=[],
        )

    async def prune_stale_stub(*_args: object, **_kwargs: object) -> StaleFillPruneResponse:
        return StaleFillPruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            min_days_without_fill=30,
            items=[],
        )

    async def prune_min_closed_stub(
        *_args: object,
        **_kwargs: object,
    ) -> MinClosedTradesPruneResponse:
        return MinClosedTradesPruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            min_closed_trades=5,
            items=[],
        )

    async def prune_max_drawdown_stub(
        *_args: object,
        **_kwargs: object,
    ) -> MaxDrawdownPruneResponse:
        return MaxDrawdownPruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            threshold_pct="0.60",
            items=[],
        )

    async def prune_low_score_stub(*_args: object, **_kwargs: object) -> LowScorePruneResponse:
        return LowScorePruneResponse(
            dry_run=True,
            scanned_wallets=1,
            candidate_wallets=0,
            deleted_wallets=0,
            deleted_fills=0,
            min_closed_trades=50,
            score_threshold="50",
            score_operator="lt",
            items=[],
        )

    async def prune_current_drawdown_stub(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prune-all must not run current drawdown cleanup.")

    monkeypatch.setattr(wallet_cleanup_service, "prune_orphan_fill_wallets", prune_orphan_stub)
    monkeypatch.setattr(wallet_cleanup_service, "prune_zero_fill_wallets", prune_zero_stub)
    monkeypatch.setattr(wallet_cleanup_service, "prune_stale_fill_wallets", prune_stale_stub)
    monkeypatch.setattr(
        wallet_cleanup_service,
        "prune_min_closed_trades_wallets",
        prune_min_closed_stub,
    )
    monkeypatch.setattr(
        wallet_cleanup_service,
        "prune_max_drawdown_wallets",
        prune_max_drawdown_stub,
    )
    monkeypatch.setattr(wallet_cleanup_service, "prune_low_score_wallets", prune_low_score_stub)
    monkeypatch.setattr(
        wallet_cleanup_service,
        "prune_current_drawdown_wallets",
        prune_current_drawdown_stub,
    )

    result = await wallet_cleanup_service.prune_all_wallets(
        object(),  # type: ignore[arg-type]
        dry_run=True,
        use_lock=False,
    )

    assert result.scanned_wallets == 6
    assert [rule.key for rule in result.rules] == [
        "orphan_fills",
        "zero_fill",
        "stale_fills",
        "min_closed_trades",
        "max_drawdown",
        "low_score",
    ]

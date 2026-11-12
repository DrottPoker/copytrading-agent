"""Patch a name wherever the live trading modules use it.

Live trading is split over several modules that call each other through
imported names. A test that replaces a collaborator, for example the
reconciliation, means every one of those call sites, so the replacement is
applied to each live module that has the name.
"""

from types import ModuleType
from typing import Any

import pytest

from app.services import (
    live_account_access,
    live_account_state,
    live_close_service,
    live_closed_trades,
    live_exchange_fetch,
    live_exchange_parsing,
    live_fill_reconciliation,
    live_order_submission,
    live_performance_service,
    live_position_reconciliation,
    live_reconciliation_service,
    live_trading_service,
)

LIVE_MODULES: tuple[ModuleType, ...] = (
    live_account_access,
    live_account_state,
    live_close_service,
    live_closed_trades,
    live_exchange_fetch,
    live_exchange_parsing,
    live_fill_reconciliation,
    live_order_submission,
    live_performance_service,
    live_position_reconciliation,
    live_reconciliation_service,
    live_trading_service,
)


def patch_live(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    patched = False
    for module in LIVE_MODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(f"No live trading module has {name!r}.")

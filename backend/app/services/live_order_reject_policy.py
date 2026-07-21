"""Stable classification of definitive Hyperliquid order rejections."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveOrderRejectClassification:
    code: str
    transient: bool
    retry_delay_seconds: int = 0


IOC_NO_MATCH = LiveOrderRejectClassification(
    code="exchange_ioc_no_match",
    transient=True,
    retry_delay_seconds=5,
)
OPEN_INTEREST_CAP = LiveOrderRejectClassification(
    code="exchange_open_interest_cap",
    transient=True,
    retry_delay_seconds=10,
)
INSUFFICIENT_MARGIN = LiveOrderRejectClassification(
    code="exchange_insufficient_margin",
    transient=False,
)
INVALID_ORDER = LiveOrderRejectClassification(
    code="exchange_invalid_order",
    transient=False,
)
UNKNOWN_REJECT = LiveOrderRejectClassification(
    code="exchange_rejected",
    transient=False,
)
OPEN_INTEREST_CAP_COOLDOWN_REASON = "live_exchange_open_interest_cap_cooldown"


def classify_live_order_reject(message: str | None) -> LiveOrderRejectClassification:
    """Classify only definite exchange rejects. Transport failures stay uncertain."""

    normalized = " ".join(str(message or "").casefold().split())
    compact = "".join(character for character in normalized if character.isalnum())
    if "could not immediately match" in normalized or "ioccancel" in compact:
        return IOC_NO_MATCH
    if "ioc" in normalized and "match" in normalized:
        return IOC_NO_MATCH
    if any(
        phrase in normalized
        for phrase in (
            "open interest cap",
            "open interest is at cap",
            "open interest limit",
            "open interest exceeded",
            "open interest too large",
            "too much open interest",
            "position would increase open interest",
        )
    ) or any(
        marker in compact
        for marker in (
            "positionincreaseatopeninterestcap",
            "positionflipatopeninterestcap",
            "tooaggressiveatopeninterestcap",
            "openinterestincrease",
        )
    ):
        return OPEN_INTEREST_CAP
    if "insufficient margin" in normalized or "insufficient balance" in normalized:
        return INSUFFICIENT_MARGIN
    if any(
        phrase in normalized
        for phrase in (
            "invalid tick",
            "tick size",
            "minimum notional",
            "minimum order",
            "min trade",
            "reduce-only",
            "reduce only",
            "lot size",
        )
    ):
        return INVALID_ORDER
    return UNKNOWN_REJECT


def live_copy_retry_delay_seconds(reason: str | None) -> int:
    """Return a conservative cooldown for retryable exchange decision reasons."""

    if str(reason or "").startswith(OPEN_INTEREST_CAP.code):
        return OPEN_INTEREST_CAP.retry_delay_seconds
    if reason == OPEN_INTEREST_CAP_COOLDOWN_REASON:
        return OPEN_INTEREST_CAP.retry_delay_seconds
    if str(reason or "").startswith(IOC_NO_MATCH.code):
        return IOC_NO_MATCH.retry_delay_seconds
    return 0

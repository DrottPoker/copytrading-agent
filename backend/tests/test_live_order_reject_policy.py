from app.services.live_order_reject_policy import (
    OPEN_INTEREST_CAP_COOLDOWN_REASON,
    classify_live_order_reject,
    live_copy_retry_delay_seconds,
)


def test_ioc_no_match_is_a_retryable_definitive_exchange_reject() -> None:
    classification = classify_live_order_reject(
        "Order could not immediately match against resting liquidity."
    )

    assert classification.code == "exchange_ioc_no_match"
    assert classification.transient is True
    assert classification.retry_delay_seconds == 5


def test_open_interest_cap_variants_are_retryable_with_a_cooldown() -> None:
    for message in (
        "Open interest cap reached.",
        "Order rejected: too much open interest.",
        "Position would increase open interest beyond the open interest limit.",
    ):
        classification = classify_live_order_reject(message)
        assert classification.code == "exchange_open_interest_cap"
        assert classification.transient is True
    assert classification.retry_delay_seconds == 10


def test_documented_exchange_status_codes_use_the_same_stable_classification() -> None:
    assert classify_live_order_reject("iocCancelRejected").code == "exchange_ioc_no_match"
    assert (
        classify_live_order_reject("positionIncreaseAtOpenInterestCapRejected").code
        == "exchange_open_interest_cap"
    )


def test_open_interest_cooldown_uses_the_bounded_retry_delay() -> None:
    assert live_copy_retry_delay_seconds(OPEN_INTEREST_CAP_COOLDOWN_REASON) == 10


def test_margin_and_unknown_rejects_are_terminal() -> None:
    assert classify_live_order_reject("Insufficient margin.").transient is False
    assert classify_live_order_reject("Unexpected exchange rejection.").transient is False

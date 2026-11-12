"""Errors raised by live trading, each with the HTTP status the API answers with."""


class LiveTradingServiceError(Exception):
    status_code = 400
    detail = "Live trading request failed."

    def __init__(self, detail: str | None = None, *, status_code: int | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail
        if status_code is not None:
            self.status_code = status_code


class LiveAccountNotFoundError(LiveTradingServiceError):
    status_code = 404
    detail = "Live account was not found."


class LiveAccountCreateError(LiveTradingServiceError):
    detail = "Live account could not be created."


class LiveAccountDeleteError(LiveTradingServiceError):
    detail = "Live account could not be deleted."


class LiveOrderSubmitError(LiveTradingServiceError):
    detail = "Live order could not be submitted."


class LiveCopyEntryLifecycleDeferred(LiveOrderSubmitError):
    """A copied entry changed lifecycle state before its order was materialized."""

    def __init__(self, reason: str, *, state_reclassified: bool = False) -> None:
        super().__init__(reason, status_code=409)
        self.reason = reason
        self.state_reclassified = state_reclassified


class LiveReconciliationError(LiveTradingServiceError):
    detail = "Live reconciliation failed."

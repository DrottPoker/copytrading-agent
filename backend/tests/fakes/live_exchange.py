from dataclasses import dataclass, field

from app.db.models import TradingAccount
from app.integrations.hyperliquid_live_client import LiveOrderResult
from app.services.trading_core import TradeIntent


class SimulatedProcessCrash(BaseException):
    """Simulate abrupt process termination without application exception handling."""


@dataclass
class FaultInjectingTradingClient:
    behavior: str
    submitted_client_order_ids: list[str] = field(default_factory=list)

    def validate_account_order(self, *, account: TradingAccount, intent: TradeIntent) -> None:
        if account.key != intent.account_key:
            raise AssertionError("Test intent must target the supplied account.")

    async def submit_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> LiveOrderResult:
        self.validate_account_order(account=account, intent=intent)
        self.submitted_client_order_ids.append(intent.client_order_id)
        if self.behavior == "accepted_then_timeout":
            raise TimeoutError("Exchange accepted the order but the response was lost.")
        if self.behavior == "accepted_then_process_crash":
            raise SimulatedProcessCrash("Process terminated after exchange acceptance.")
        if self.behavior == "partial_fill":
            return LiveOrderResult(
                status="partially_filled",
                client_order_id=intent.client_order_id,
                exchange_order_id="123",
                filled_size=intent.size / 2,
                average_fill_price=intent.limit_price,
                raw_response={"status": "ok", "faultBehavior": self.behavior},
            )
        return LiveOrderResult(
            status="filled",
            client_order_id=intent.client_order_id,
            exchange_order_id="123",
            filled_size=intent.size,
            average_fill_price=intent.limit_price,
            raw_response={"status": "ok", "faultBehavior": self.behavior},
        )

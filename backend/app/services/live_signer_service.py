"""Verify that live orders are signed by a Hyperliquid API wallet.

An API wallet (an "agent" in Hyperliquid terms) can place and cancel orders for
the account that approved it but cannot withdraw funds. Production refuses live
entries signed by any other key, so a leaked server key cannot drain the
account. The check result is stored on the live account and refreshed during
reconciliation; the entry gate only reads it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import Settings
from app.db.models import TradingAccount

LIVE_SIGNER_PAYLOAD_KEY = "liveSigner"
LIVE_SIGNER_CHECK_INTERVAL = timedelta(hours=1)
LIVE_SIGNER_RETRY_INTERVAL = timedelta(minutes=5)

SIGNER_API_WALLET = "api_wallet"
SIGNER_MAIN_WALLET = "main_wallet"
SIGNER_OTHER_WALLET = "other_wallet"
SIGNER_WRONG_ACCOUNT = "wrong_account"
SIGNER_NOT_REGISTERED = "not_registered"
SIGNER_UNAVAILABLE = "unavailable"
SIGNER_UNCHECKED = "unchecked"

# Results that come from Hyperliquid itself and stay valid until the next check.
DEFINITIVE_SIGNER_STATUSES = {
    SIGNER_API_WALLET,
    SIGNER_MAIN_WALLET,
    SIGNER_OTHER_WALLET,
    SIGNER_WRONG_ACCOUNT,
    SIGNER_NOT_REGISTERED,
}

SIGNER_STATUS_DESCRIPTIONS = {
    SIGNER_API_WALLET: "an API wallet approved by this account",
    SIGNER_MAIN_WALLET: "the account's own wallet key, which can withdraw funds",
    SIGNER_OTHER_WALLET: "a full wallet key, which can withdraw funds from its own account",
    SIGNER_WRONG_ACCOUNT: "an API wallet approved by a different account",
    SIGNER_NOT_REGISTERED: "not a registered API wallet",
    SIGNER_UNAVAILABLE: "not verified because Hyperliquid could not be reached",
    SIGNER_UNCHECKED: "not verified yet",
}


class UserRoleClient(Protocol):
    async def user_role(self, *, user: str) -> Any: ...


@dataclass(frozen=True)
class LiveSignerCheck:
    status: str
    signer_address: str
    master_address: str
    role: str | None
    checked_at: datetime | None
    attempted_at: datetime | None
    error: str | None = None

    @property
    def description(self) -> str:
        return SIGNER_STATUS_DESCRIPTIONS.get(self.status, self.status)

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "signerAddress": self.signer_address,
            "masterAddress": self.master_address,
            "role": self.role,
            "checkedAt": self.checked_at.isoformat() if self.checked_at else None,
            "attemptedAt": self.attempted_at.isoformat() if self.attempted_at else None,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, value: object) -> LiveSignerCheck | None:
        if not isinstance(value, dict):
            return None
        status = value.get("status")
        signer = value.get("signerAddress")
        master = value.get("masterAddress")
        if (
            not isinstance(status, str)
            or not isinstance(signer, str)
            or not isinstance(master, str)
        ):
            return None
        role = value.get("role")
        error = value.get("error")
        return cls(
            status=status,
            signer_address=signer,
            master_address=master,
            role=role if isinstance(role, str) else None,
            checked_at=parse_datetime(value.get("checkedAt")),
            attempted_at=parse_datetime(value.get("attemptedAt")),
            error=error if isinstance(error, str) else None,
        )


@dataclass(frozen=True)
class LiveSignerBlock:
    rule: str
    message: str
    status: str
    trip_account: bool


@lru_cache(maxsize=4)
def live_signer_address(private_key: str | None) -> str | None:
    """Return the address of the configured signing key, or None without a usable key."""

    if not private_key:
        return None
    try:
        from eth_account import Account

        return str(Account.from_key(private_key).address).lower()
    except Exception:
        # Never surface the key or the parser's message, which may echo it.
        return None


def live_signer_master_address(account: TradingAccount, *, settings: Settings) -> str | None:
    """The wallet that must have approved the signer: the account owner, not a vault."""

    address = account.wallet_address or settings.hyperliquid_wallet_address
    return address.strip().lower() if address and address.strip() else None


def classify_user_role(*, master: str, response: object) -> tuple[str, str | None]:
    """Map a Hyperliquid userRole response for the signer to a signer status."""

    if not isinstance(response, dict):
        raise ValueError("Hyperliquid userRole returned an unexpected shape.")
    role = response.get("role")
    if not isinstance(role, str):
        raise ValueError("Hyperliquid userRole returned no role.")
    if role == "agent":
        data = response.get("data")
        approved_by = data.get("user") if isinstance(data, dict) else None
        if isinstance(approved_by, str) and approved_by.lower() == master:
            return SIGNER_API_WALLET, role
        return SIGNER_WRONG_ACCOUNT, role
    if role == "missing":
        return SIGNER_NOT_REGISTERED, role
    return SIGNER_OTHER_WALLET, role


async def check_live_signer(
    client: UserRoleClient,
    *,
    signer: str,
    master: str,
    now: datetime,
) -> LiveSignerCheck:
    if signer == master:
        return LiveSignerCheck(
            status=SIGNER_MAIN_WALLET,
            signer_address=signer,
            master_address=master,
            role="user",
            checked_at=now,
            attempted_at=now,
        )
    try:
        status, role = classify_user_role(
            master=master,
            response=await client.user_role(user=signer),
        )
    except Exception as exc:
        return LiveSignerCheck(
            status=SIGNER_UNAVAILABLE,
            signer_address=signer,
            master_address=master,
            role=None,
            checked_at=None,
            attempted_at=now,
            error=exc.__class__.__name__,
        )
    return LiveSignerCheck(
        status=status,
        signer_address=signer,
        master_address=master,
        role=role,
        checked_at=now,
        attempted_at=now,
    )


def account_live_signer(account: TradingAccount) -> LiveSignerCheck | None:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    return LiveSignerCheck.from_payload(payload.get(LIVE_SIGNER_PAYLOAD_KEY))


def live_signer_check_due(
    previous: LiveSignerCheck | None,
    *,
    signer: str,
    master: str,
    now: datetime,
) -> bool:
    if previous is None or previous.signer_address != signer or previous.master_address != master:
        return True
    last_attempt = previous.attempted_at or previous.checked_at
    if last_attempt is None:
        return True
    if previous.status in DEFINITIVE_SIGNER_STATUSES and previous.checked_at == last_attempt:
        return now - last_attempt >= LIVE_SIGNER_CHECK_INTERVAL
    return now - last_attempt >= LIVE_SIGNER_RETRY_INTERVAL


async def refresh_live_account_signer(
    account: TradingAccount,
    *,
    client: UserRoleClient,
    settings: Settings,
    now: datetime | None = None,
    force: bool = False,
) -> bool:
    """Re-check the signer when due and store the result. Returns whether it changed."""

    signer = live_signer_address(settings.hyperliquid_private_key)
    master = live_signer_master_address(account, settings=settings)
    if signer is None or master is None:
        return False
    checked_at = now or datetime.now(UTC)
    previous = account_live_signer(account)
    if not force and not live_signer_check_due(
        previous,
        signer=signer,
        master=master,
        now=checked_at,
    ):
        return False
    result = await check_live_signer(client, signer=signer, master=master, now=checked_at)
    if (
        result.status == SIGNER_UNAVAILABLE
        and previous is not None
        and previous.status in DEFINITIVE_SIGNER_STATUSES
        and previous.signer_address == signer
        and previous.master_address == master
    ):
        # A network failure never discards a verified answer for the same key.
        result = LiveSignerCheck(
            status=previous.status,
            signer_address=signer,
            master_address=master,
            role=previous.role,
            checked_at=previous.checked_at,
            attempted_at=checked_at,
            error=result.error,
        )
    payload = dict(account.config_payload or {})
    payload[LIVE_SIGNER_PAYLOAD_KEY] = result.payload()
    account.config_payload = payload
    return True


def current_live_signer(account: TradingAccount, *, settings: Settings) -> LiveSignerCheck:
    """The stored check when it is for the configured key and account, else unchecked."""

    signer = live_signer_address(settings.hyperliquid_private_key) or ""
    master = live_signer_master_address(account, settings=settings) or ""
    stored = account_live_signer(account)
    if stored is not None and stored.signer_address == signer and stored.master_address == master:
        return stored
    return LiveSignerCheck(
        status=SIGNER_UNCHECKED,
        signer_address=signer,
        master_address=master,
        role=None,
        checked_at=None,
        attempted_at=None,
    )


def live_signer_required(settings: Settings) -> bool:
    return settings.app_env == "production"


def live_signer_entry_block(
    account: TradingAccount,
    *,
    settings: Settings,
) -> LiveSignerBlock | None:
    """Why production must refuse a live entry for this account's signer, if it must."""

    if not live_signer_required(settings):
        return None
    signer = current_live_signer(account, settings=settings)
    if signer.status == SIGNER_API_WALLET:
        return None
    if signer.status in DEFINITIVE_SIGNER_STATUSES:
        return LiveSignerBlock(
            rule="live_signer_not_api_wallet",
            message=(
                "Production live trading requires a Hyperliquid API wallet; "
                f"the configured signing key is {signer.description}."
            ),
            status=signer.status,
            trip_account=True,
        )
    return LiveSignerBlock(
        rule="live_signer_unverified",
        message=(
            "Production live entries wait until the signing key is verified as a "
            f"Hyperliquid API wallet; it is {signer.description}."
        ),
        status=signer.status,
        trip_account=False,
    )


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelModel
from app.schemas.score import WalletScoreRead

WALLET_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
POLLING_TIERS = {"pool", "candidate", "active", "cooldown"}


def normalize_wallet_address(address: str) -> str:
    normalized = address.strip().lower()
    if not WALLET_ADDRESS_PATTERN.fullmatch(normalized):
        raise ValueError("Address must be a 0x-prefixed 40 character hex wallet address.")
    return normalized


class WalletCreate(CamelModel):
    address: str = Field(min_length=42, max_length=42)
    label: str | None = Field(default=None, max_length=120)
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return normalize_wallet_address(value)


class WalletUpdate(CamelModel):
    label: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    eligible: bool | None = None
    copy_enabled: bool | None = None
    polling_tier: str | None = None
    cooldown_until: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("polling_tier")
    @classmethod
    def validate_polling_tier(cls, value: str | None) -> str | None:
        if value is not None and value not in POLLING_TIERS:
            raise ValueError(f"pollingTier must be one of: {', '.join(sorted(POLLING_TIERS))}.")
        return value


class WalletRead(CamelModel):
    id: UUID
    address: str
    label: str | None
    enabled: bool
    eligible: bool
    copy_enabled: bool
    polling_tier: str
    cooldown_until: datetime | None
    last_polled_at: datetime | None
    last_seen_fill_at: datetime | None
    notes: str | None
    score: WalletScoreRead | None = None
    created_at: datetime
    updated_at: datetime


class WalletListResponse(CamelModel):
    items: list[WalletRead]
    total: int
    limit: int
    offset: int

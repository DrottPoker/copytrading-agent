from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.schemas.wallet import normalize_wallet_address

DISCOVERY_IGNORED_WALLET_ADDRESSES_KEY = "discovery_ignored_wallet_addresses"


async def load_ignored_wallet_addresses(session: AsyncSession) -> set[str]:
    setting = await session.get(Setting, DISCOVERY_IGNORED_WALLET_ADDRESSES_KEY)
    if setting is None or not isinstance(setting.value, dict):
        return set()

    raw_addresses = setting.value.get("addresses")
    if not isinstance(raw_addresses, list):
        return set()

    ignored_addresses: set[str] = set()
    for raw_address in raw_addresses:
        if not isinstance(raw_address, str):
            continue
        try:
            ignored_addresses.add(normalize_wallet_address(raw_address))
        except ValueError:
            continue
    return ignored_addresses


async def add_ignored_wallet_addresses(
    session: AsyncSession,
    *,
    addresses: list[str],
    reason: str,
) -> None:
    setting = await session.get(Setting, DISCOVERY_IGNORED_WALLET_ADDRESSES_KEY)
    existing_addresses: list[str] = []
    if setting is not None and isinstance(setting.value, dict):
        raw_addresses = setting.value.get("addresses")
        if isinstance(raw_addresses, list):
            existing_addresses = [
                str(address).lower() for address in raw_addresses if isinstance(address, str)
            ]

    merged_addresses = sorted(
        set(existing_addresses) | {address.lower() for address in addresses}
    )
    stmt = insert(Setting).values(
        key=DISCOVERY_IGNORED_WALLET_ADDRESSES_KEY,
        value={"addresses": merged_addresses, "reason": reason},
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value},
        )
    )

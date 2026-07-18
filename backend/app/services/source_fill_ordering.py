"""Canonical ordering for source fills across realtime and recovery paths."""

from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
SOURCE_CLOSE_DIRECTIONS = frozenset({"Close Long", "Close Short", "Long > Short", "Short > Long"})


def source_fill_order_key(
    fill: dict[str, Any],
) -> tuple[int, str, int, Decimal, int, Decimal, str]:
    raw_json = source_fill_raw_json(fill)
    direction = str(raw_json.get("dir") or "")
    start_position = decimal_or_none(raw_json.get("startPosition"))
    source_position = start_position.copy_abs() if start_position is not None else ZERO
    direction_rank = 0 if direction in SOURCE_CLOSE_DIRECTIONS else 1
    position_rank = -source_position if direction_rank == 0 else source_position
    source_fill_id = str(fill.get("externalFillId") or "")
    source_fill_id_numeric = source_fill_id_numeric_value(source_fill_id)
    return (
        int(fill.get("timestampMs") or 0),
        str(fill.get("coin") or ""),
        direction_rank,
        position_rank,
        0 if source_fill_id_numeric is not None else 1,
        source_fill_id_numeric or ZERO,
        source_fill_id,
    )


def source_fill_order_components(fill: dict[str, Any]) -> tuple[int, Decimal, Decimal | None]:
    """Return the sortable fields persisted with a live-copy fill plan."""

    _, _, direction_rank, position_rank, _, numeric_fill_id, source_fill_id = source_fill_order_key(
        fill
    )
    return (
        direction_rank,
        position_rank,
        numeric_fill_id if source_fill_id.isdigit() else None,
    )


def source_fill_id_numeric_value(source_fill_id: str) -> Decimal | None:
    if not source_fill_id.isdigit():
        return None
    try:
        return Decimal(source_fill_id)
    except InvalidOperation:
        return None


def source_fill_raw_json(fill: dict[str, Any]) -> dict[str, Any]:
    raw_json = fill.get("rawJson", fill.get("raw_json"))
    return raw_json if isinstance(raw_json, dict) else {}


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

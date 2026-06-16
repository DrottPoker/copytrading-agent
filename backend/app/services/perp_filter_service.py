from typing import Any

PERP_FILL_DIRECTIONS = {
    "Open Long",
    "Close Long",
    "Open Short",
    "Close Short",
    "Long > Short",
    "Short > Long",
}


def is_perp_direction(direction: str | None) -> bool:
    if not direction:
        return False
    if direction in PERP_FILL_DIRECTIONS:
        return True
    return (
        "Long" in direction
        or "Short" in direction
        or "Liquidated" in direction
        or direction == "Auto-Deleveraging"
    )


def is_perp_fill(fill: dict[str, Any]) -> bool:
    direction = fill.get("dir")
    return is_perp_direction(str(direction) if direction is not None else None)

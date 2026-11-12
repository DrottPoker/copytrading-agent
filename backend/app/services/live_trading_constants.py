"""Names shared by every live trading module."""

from decimal import Decimal

POSITION_EPSILON = Decimal("0.000000000001")
# Source wallet of rows that describe the exchange account itself, not a copied source.
LIVE_EXCHANGE_SOURCE = "__exchange__"
LIVE_MANUAL_TEST_SOURCE = "__manual_testnet__"

from fastapi import status

from app.api.routes_wallets import wallet_error
from app.services.wallet_cleanup_service import WalletDataProtectedError


def test_wallet_error_maps_protected_delete_to_conflict() -> None:
    error = WalletDataProtectedError({"0x" + "a" * 40: ("open_trading_position",)})

    response = wallet_error(error)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "open_trading_position" in response.detail

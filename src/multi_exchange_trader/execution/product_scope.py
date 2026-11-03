"""Pure private product selection values; no grants or execution authority."""

import re
from dataclasses import dataclass

from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.trading_product import TradingProduct, bybit_category

BYBIT_PERMISSION_PROFILE_REVISION = "BYBIT_SPOT_LINEAR_NO_FUNDS_V1"
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}")


def _validate_account(account: ProtectionScope) -> None:
    if type(account) is not ProtectionScope:
        raise ValueError("Exact ProtectionScope required")
    try:
        account_id, environment = account.account_id, account.environment
    except AttributeError:
        raise ValueError("Complete ProtectionScope required") from None
    if (type(account_id) is not str or not 1 <= len(account_id) <= 256
            or account_id != account_id.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in account_id)):
        raise ValueError("Account identifier outside local technical envelope")
    if type(environment) is not str or environment not in ("DEMO", "LIVE"):
        raise ValueError("Exact DEMO/LIVE environment required")


def _validate_scope(scope: "ProductScope") -> None:
    if type(scope) is not ProductScope:
        raise ValueError("Exact ProductScope required")
    try:
        account, product = scope.account, scope.product
    except AttributeError:
        raise ValueError("Complete ProductScope required") from None
    _validate_account(account)
    bybit_category(product)


@dataclass(frozen=True)
class ProductScope:
    account: ProtectionScope
    product: TradingProduct

    def __post_init__(self) -> None:
        _validate_account(self.account)
        bybit_category(self.product)


@dataclass(frozen=True)
class ProductExecutionPolicy:
    scope: ProductScope
    revision: str
    allowed_products: frozenset[TradingProduct]
    selected_product: TradingProduct
    permission_profile_revision: str

    def __post_init__(self) -> None:
        _validate_scope(self.scope)
        if type(self.revision) is not str or _REVISION.fullmatch(self.revision) is None:
            raise ValueError("Policy revision outside local technical envelope")
        if type(self.allowed_products) is not frozenset or not 1 <= len(self.allowed_products) <= 2:
            raise ValueError("Exact nonempty supported product frozenset required")
        for product in self.allowed_products:
            bybit_category(product)
        bybit_category(self.selected_product)
        if (self.selected_product not in self.allowed_products
                or self.selected_product != self.scope.product):
            raise ValueError("Selected product must match allowed products and scope")
        if (type(self.permission_profile_revision) is not str
                or self.permission_profile_revision != BYBIT_PERMISSION_PROFILE_REVISION):
            raise ValueError("Supported permission profile revision required")

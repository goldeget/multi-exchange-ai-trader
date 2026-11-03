"""Pure local permission observations; never execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from multi_exchange_trader.trading_product import TradingProduct

from .product_scope import BYBIT_PERMISSION_PROFILE_REVISION

if TYPE_CHECKING:
    from .account_binding import BybitAccountIdentity

_ALLOWED_PAIRS = frozenset({
    ("ContractTrade", "Order"), ("ContractTrade", "Position"),
    ("Derivatives", "DerivativesTrade"), ("Spot", "SpotTrade"),
})
_INVALID_CODES = (
    "READ_ONLY_METADATA_INVALID", "PERMISSION_METADATA_INVALID",
    "UNKNOWN_PERMISSION_GROUP", "UNKNOWN_PERMISSION_GRANT",
)
_NORMAL_CODES = (
    "READ_ONLY", "WALLET_PERMISSION_METADATA_MISSING", "FORBIDDEN_PERMISSION_GRANT",
    "NO_SUPPORTED_PRODUCT_GRANT",
)
_MISSING = object()


@dataclass(frozen=True)
class CredentialPermissionProfile:
    """Validated local observation; caller construction conveys no provenance."""

    revision: str
    granted_products: frozenset[TradingProduct]
    forbidden_grants: tuple[tuple[str, str], ...]
    metadata_complete: bool
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        failure = "INVALID_CREDENTIAL_PERMISSION_PROFILE"
        if (type(self.revision) is not str
                or self.revision != BYBIT_PERMISSION_PROFILE_REVISION
                or type(self.granted_products) is not frozenset
                or len(self.granted_products) > 2
                or any(type(product) is not str or product not in ("SPOT", "LINEAR")
                       for product in self.granted_products)
                or type(self.metadata_complete) is not bool
                or type(self.forbidden_grants) is not tuple
                or type(self.blockers) is not tuple or len(self.blockers) > 4
                or any(type(code) is not str for code in self.blockers)):
            raise ValueError(failure)

        from .account_binding import _PERMISSIONS

        if len(self.forbidden_grants) > sum(len(grants) for grants in _PERMISSIONS.values()):
            raise ValueError(failure)
        for pair in self.forbidden_grants:
            if (type(pair) is not tuple or len(pair) != 2
                    or type(pair[0]) is not str or type(pair[1]) is not str):
                raise ValueError(failure)
            group, grant = pair
            if (group not in _PERMISSIONS or grant not in _PERMISSIONS[group]
                    or pair in _ALLOWED_PAIRS):
                raise ValueError(failure)
        if self.forbidden_grants != tuple(sorted(set(self.forbidden_grants))):
            raise ValueError(failure)

        if any(code in _INVALID_CODES for code in self.blockers):
            if (len(self.blockers) != 1 or self.granted_products or self.forbidden_grants
                    or self.metadata_complete):
                raise ValueError(failure)
            return
        if (self.blockers != tuple(code for code in _NORMAL_CODES if code in self.blockers)
                or (("WALLET_PERMISSION_METADATA_MISSING" in self.blockers)
                    != (not self.metadata_complete))
                or (("FORBIDDEN_PERMISSION_GRANT" in self.blockers) != bool(self.forbidden_grants))
                or (("NO_SUPPORTED_PRODUCT_GRANT" in self.blockers) != (not self.granted_products))):
            raise ValueError(failure)


def _metadata_error(permissions: object, schema: dict[str, frozenset[str]]) -> str | None:
    """Finish bounded shape/duplicate checks before choosing an unknown code."""
    if type(permissions) is not tuple or not 1 <= len(permissions) <= len(schema):
        return "PERMISSION_METADATA_INVALID"
    max_grants = max(len(grants) for grants in schema.values())
    groups: set[str] = set()
    unknown_group = False
    unknown_grant = False
    for entry in permissions:
        if type(entry) is not tuple or len(entry) != 2:
            return "PERMISSION_METADATA_INVALID"
        group, grants = entry
        if type(group) is not str or type(grants) is not tuple or len(grants) > max_grants:
            return "PERMISSION_METADATA_INVALID"
        if group in groups:
            return "PERMISSION_METADATA_INVALID"
        groups.add(group)
        known = schema.get(group)
        if known is not None and len(grants) > len(known):
            return "PERMISSION_METADATA_INVALID"
        seen: set[str] = set()
        for grant in grants:
            if type(grant) is not str:
                return "PERMISSION_METADATA_INVALID"
            if grant in seen:
                return "PERMISSION_METADATA_INVALID"
            seen.add(grant)
            if known is not None and grant not in known:
                unknown_grant = True
        if known is None:
            unknown_group = True
    if unknown_group:
        return "UNKNOWN_PERMISSION_GROUP"
    if unknown_grant:
        return "UNKNOWN_PERMISSION_GRANT"
    return None


def _invalid_profile(code: str) -> CredentialPermissionProfile:
    return CredentialPermissionProfile(
        BYBIT_PERMISSION_PROFILE_REVISION, frozenset(), (), False, (code,),
    )


def evaluate_bybit_permission_profile(
    identity: BybitAccountIdentity, *, profile_revision: str,
) -> CredentialPermissionProfile:
    """Classify normalized metadata under local rules, without admitting execution."""
    if (type(profile_revision) is not str
            or profile_revision != BYBIT_PERMISSION_PROFILE_REVISION):
        raise ValueError("SUPPORTED_PERMISSION_PROFILE_REVISION_REQUIRED")

    from .account_binding import _PERMISSIONS, BybitAccountIdentity

    if type(identity) is not BybitAccountIdentity:
        raise ValueError("EXACT_BYBIT_ACCOUNT_IDENTITY_REQUIRED")
    read_only = getattr(identity, "read_only", _MISSING)
    if type(read_only) is not bool:
        return _invalid_profile("READ_ONLY_METADATA_INVALID")
    permissions = getattr(identity, "permissions", _MISSING)
    error = _metadata_error(permissions, _PERMISSIONS)
    if error is not None:
        return _invalid_profile(error)

    # The complete shape/schema pass above precedes mapping or any published values.
    entries = dict(identity.permissions)
    products: set[TradingProduct] = set()
    if "SpotTrade" in entries.get("Spot", ()):
        products.add("SPOT")
    if all(grant in entries.get("ContractTrade", ()) for grant in ("Order", "Position")):
        products.add("LINEAR")
    forbidden = tuple(sorted(
        (group, grant) for group, grants in identity.permissions for grant in grants
        if (group, grant) not in _ALLOWED_PAIRS
    ))
    complete = "Wallet" in entries
    blockers = tuple(code for code, include in zip(
        _NORMAL_CODES, (read_only, not complete, bool(forbidden), not products), strict=True,
    ) if include)
    return CredentialPermissionProfile(
        BYBIT_PERMISSION_PROFILE_REVISION, frozenset(products), forbidden, complete, blockers,
    )

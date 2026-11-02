"""Process-local history capability and data-only observation view.

Only an issuing account session's exact handle registry grants authority. These
types have no execution codec registration and never restore live authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, Self, SupportsIndex

if TYPE_CHECKING:
    from .history import HistorySnapshot


class AuthenticatedHistoryReceipt:
    """Opaque handle; construction and serialization cannot recreate issuance."""

    __slots__ = ()

    def __new__(cls) -> Self:
        raise ValueError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")

    def __repr__(self) -> str:
        return "AuthenticatedHistoryReceipt(opaque=True)"

    def __copy__(self) -> NoReturn:
        raise ValueError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise ValueError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise ValueError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")


@dataclass(frozen=True, repr=False)
class HistoryReceiptView:
    """Immutable observation data, never a capability or a trading grant."""

    snapshot: HistorySnapshot
    binding_reference: str

    def __repr__(self) -> str:
        return "HistoryReceiptView(observation=REDACTED)"

"""Nonserializable process-local monitoring capability; only session slots authorize."""

from typing import NoReturn, Self, SupportsIndex


class AuthenticatedMonitoringReceipt:
    __slots__ = ()

    def __new__(cls) -> Self:
        raise ValueError("AUTHENTICATED_MONITORING_RECEIPT_REQUIRED")

    def __copy__(self) -> Self:
        raise TypeError("MONITORING_RECEIPT_NOT_COPYABLE")

    def __deepcopy__(self, memo: object) -> Self:
        raise TypeError("MONITORING_RECEIPT_NOT_COPYABLE")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("MONITORING_RECEIPT_NOT_SERIALIZABLE")

    def __repr__(self) -> str:
        return "AuthenticatedMonitoringReceipt(opaque=True)"

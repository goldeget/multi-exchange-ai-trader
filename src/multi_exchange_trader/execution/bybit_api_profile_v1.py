"""Pure API-profile data, without fee applicability or financial authority.

Schema checked 2026-11-04:
https://bybit-exchange.github.io/docs/v5/user/apikey-info
All four optional profile fields are strings. Missing means unknown; unknown
wire spellings and empty strings are preserved rather than interpreted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .bybit_account_projections_v1 import _result
from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1


class BybitApiProfileError(ValueError):
    """Fixed data-validation codes without raw profile or credential values."""


@dataclass(frozen=True, repr=False)
class BybitApiProfileV1:
    source: BybitModelSourceRefV1
    uid: str
    parent_uid: str
    is_master: bool
    vip_level: str | None
    mkt_maker_level: str | None
    kyc_level: str | None
    kyc_region: str | None
    fee_applicability: Literal[False] = field(default=False, init=False)
    financial_authority: Literal[False] = field(default=False, init=False)
    live_permission: Literal[False] = field(default=False, init=False)

    def __repr__(self) -> str:
        return "BybitApiProfileV1(data_only=True, profile=REDACTED)"


def _optional_string(result: dict[str, object], key: str) -> str | None:
    if key not in result:
        return None
    value = result[key]
    if (
        type(value) is not str
        or len(value) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise BybitApiProfileError("BYBIT_API_PROFILE_INVALID")
    return value


def project_bybit_api_profile_v1(
    source: BybitModelSourceRefV1, raw_body: bytes
) -> BybitApiProfileV1:
    """Project safe fields from matching bytes; only the original Session grants custody."""
    result = _result(source, raw_body, path="/v5/user/query-api", queries=((),))
    uid, parent, master = result.get("userID"), result.get("parentUid"), result.get("isMaster")
    api_key = result.get("apiKey")
    if (
        type(uid) is not int
        or not 0 < uid < 10**20
        or type(parent) is not str
        or re.fullmatch(r"0|[1-9][0-9]{0,19}", parent) is None
        or type(master) is not bool
        or source.account_scope is None
        or source.account_scope.account_id != str(uid)
        or parent == str(uid)
        or type(api_key) is not str
        or not api_key
        or result.get("secret") != ""
    ):
        raise BybitApiProfileError("BYBIT_API_PROFILE_INVALID")
    values = tuple(
        _optional_string(result, name)
        for name in ("vipLevel", "mktMakerLevel", "kycLevel", "kycRegion")
    )
    if any(api_key in value for value in (str(uid), parent, *values) if value is not None):
        raise BybitApiProfileError("BYBIT_API_PROFILE_INVALID")
    return BybitApiProfileV1(source, str(uid), parent, master, *values)

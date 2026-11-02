"""Explicit bounded OpenRouter metadata GETs; never generation or balance authority."""

import json
import math
import re
import ssl
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from typing import Literal, cast

import httpx

_ORIGIN = "https://openrouter.ai/api/v1/"
_KEY = re.compile(r"sk-or-v1-[A-Za-z0-9_-]{8,480}", re.ASCII)
_MODEL = re.compile(
    r"[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9][a-z0-9_.-]{0,127}(?::[a-z0-9_-]{1,32})?",
    re.ASCII,
)
_DATE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
    re.ASCII,
)
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,30})(?:\.[0-9]{1,64})?(?:[eE][+-]?[0-9]{1,3})?", re.ASCII)


class OpenRouterMetadataFailure(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    REDIRECT_REJECTED = "REDIRECT_REJECTED"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    TLS_ERROR = "TLS_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


@dataclass(frozen=True, slots=True)
class OpenRouterKeyMetadata:
    usage: Decimal
    limit: Decimal | None
    limit_remaining: Decimal | None
    is_free_tier: bool
    is_management_key: bool | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class OpenRouterKeyResult:
    status: Literal["VERIFIED", "FAILED"]
    failure: OpenRouterMetadataFailure | None
    verified_at: datetime | None
    metadata: OpenRouterKeyMetadata | None
    generation_enabled: Literal[False] = field(default=False, init=False)
    provider_balance: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class OpenRouterModelMetadata:
    resolved_model_id: str
    display_name: str
    context_length: int
    pricing: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class OpenRouterModelResult:
    requested_model_id: str
    status: Literal["CATALOGUE_FOUND", "FAILED"]
    failure: OpenRouterMetadataFailure | None
    retrieved_at: datetime | None
    metadata: OpenRouterModelMetadata | None
    model_access_verified: Literal[False] = field(default=False, init=False)
    generation_enabled: Literal[False] = field(default=False, init=False)
    provider_balance: None = field(default=None, init=False)


def _model(value: object) -> str:
    if (
        not isinstance(value, str)
        or not _MODEL.fullmatch(value)
        or ".." in value
        or "sk-" in value
        or "bearer" in value.lower()
    ):
        raise ValueError("Invalid metadata configuration")
    return value


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Invalid metadata response")
    return cast(dict[str, object], value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Invalid metadata response")
        result[name] = value
    return result


def _number(raw: str) -> Decimal:
    if len(raw) > 128:
        raise ValueError("Invalid metadata response")
    value = Decimal(raw)
    if not value.is_finite() or abs(value.adjusted()) > 308:
        raise ValueError("Invalid metadata response")
    return value


def _integer(raw: str) -> int:
    if len(raw) > 128:
        raise ValueError("Invalid metadata response")
    return int(raw)


def _constant(_: str) -> None:
    raise ValueError("Invalid metadata response")


def _bounded(value: object, key: str, path: tuple[str, ...] = (), depth: int = 0) -> int:
    """Bound every field, even discarded fields. Only a redacted key label is tolerated."""
    if depth > 12:
        raise ValueError("Invalid metadata response")
    count = 1
    if isinstance(value, str):
        lower = value.lower()
        redacted_label = path == ("data", "label") and re.fullmatch(
            r"sk-or-v1-[A-Za-z0-9_-]{1,16}\.\.\.[A-Za-z0-9_-]{1,16}", value
        )
        if (
            len(value) > 4096
            or key in value
            or "bearer " in lower
            or ("sk-" in lower and not redacted_label)
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)
        ):
            raise ValueError("Invalid metadata response")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("Invalid metadata response")
        for name, item in value.items():
            count += _bounded(name, key, (), depth + 1)
            count += _bounded(item, key, (*path, name), depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("Invalid metadata response")
        for item in value:
            count += _bounded(item, key, (*path, "[]"), depth + 1)
    if count > 4096:
        raise ValueError("Invalid metadata response")
    return count


def _parse(body: bytes, key: str) -> dict[str, object]:
    value: object = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_float=_number,
        parse_int=_integer,
        parse_constant=_constant,
    )
    _bounded(value, key)
    return _object(_object(value)["data"])


def _money(value: object) -> Decimal:
    if type(value) not in (int, Decimal):
        raise ValueError("Invalid metadata response")
    number = Decimal(cast("int | Decimal", value))
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid metadata response")
    return number


def _key_metadata(data: dict[str, object]) -> OpenRouterKeyMetadata:
    free = data["is_free_tier"]
    management = data.get("is_management_key")
    if type(free) is not bool or (management is not None and type(management) is not bool):
        raise ValueError("Invalid metadata response")
    expiry = data.get("expires_at")
    if expiry is not None and (not isinstance(expiry, str) or not _DATE.fullmatch(expiry)):
        raise ValueError("Invalid metadata response")
    return OpenRouterKeyMetadata(
        _money(data["usage"]),
        None if data["limit"] is None else _money(data["limit"]),
        None if data["limit_remaining"] is None else _money(data["limit_remaining"]),
        free,
        management,
        None if expiry is None else datetime.fromisoformat(expiry).astimezone(UTC),
    )


def _model_metadata(data: dict[str, object], requested: str) -> OpenRouterModelMetadata:
    resolved = _model(data["id"])
    name, context = data["name"], data["context_length"]
    if (
        resolved != requested
        or not isinstance(name, str)
        or not name.strip()
        or len(name) > 128
        or type(context) is not int
        or not 1 <= context <= 2**31 - 1
    ):
        raise ValueError("Invalid metadata response")
    prices = _object(data["pricing"])
    result: list[tuple[str, str]] = []
    for item in ("prompt", "completion", "request", "image"):
        if item not in prices and item in ("request", "image"):
            continue
        price = prices[item]
        if not isinstance(price, str) or not _PRICE.fullmatch(price):
            raise ValueError("Invalid metadata response")
        _money(_number(price))
        result.append((item, price))
    return OpenRouterModelMetadata(resolved, name, context, tuple(result))


def _status(code: int) -> OpenRouterMetadataFailure | None:
    if code == 200:
        return None
    mapped = {
        401: OpenRouterMetadataFailure.UNAUTHORIZED,
        403: OpenRouterMetadataFailure.FORBIDDEN,
        404: OpenRouterMetadataFailure.NOT_FOUND,
        429: OpenRouterMetadataFailure.RATE_LIMITED,
    }
    if code in mapped:
        return mapped[code]
    if 300 <= code < 400:
        return OpenRouterMetadataFailure.REDIRECT_REJECTED
    if 500 <= code < 600:
        return OpenRouterMetadataFailure.PROVIDER_UNAVAILABLE
    return OpenRouterMetadataFailure.HTTP_ERROR


class OpenRouterMetadataClient:
    """No I/O at construction. Each explicit method performs at most one fixed-origin GET.

    Credentials are retained only for transport; results never carry labels or account IDs.
    Neither successful method authorizes generation, proves model access, or fetches credits.
    """

    __slots__ = ("__key", "__max_body", "__model", "__timeout", "__transport")

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 5.0,
        max_body_bytes: int = 65_536,
    ) -> None:
        try:
            valid = (
                isinstance(api_key, str)
                and _KEY.fullmatch(api_key)
                and api_key not in _model(model_id)
                and type(timeout_seconds) in (int, float)
                and math.isfinite(timeout_seconds)
                and 0.1 <= timeout_seconds <= 30
                and type(max_body_bytes) is int
                and 1024 <= max_body_bytes <= 65_536
            )
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Invalid metadata configuration") from None
        self.__key, self.__model, self.__transport = api_key, model_id, transport
        self.__timeout, self.__max_body = float(timeout_seconds), max_body_bytes

    def __repr__(self) -> str:
        return "OpenRouterMetadataClient()"

    def _get(self, path: str) -> dict[str, object] | OpenRouterMetadataFailure:
        try:
            started = monotonic()
            with (
                httpx.Client(
                    transport=self.__transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self.__timeout,
                ) as client,
                client.stream(
                    "GET",
                    _ORIGIN + path,
                    headers={
                        "Authorization": "Bearer " + self.__key,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                failure = _status(response.status_code)
                if failure is not None:
                    return failure
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return OpenRouterMetadataFailure.INVALID_RESPONSE
                length = response.headers.get("content-length")
                if length is not None:
                    if not re.fullmatch(r"[0-9]{1,20}", length):
                        return OpenRouterMetadataFailure.INVALID_RESPONSE
                    if int(length) > self.__max_body:
                        return OpenRouterMetadataFailure.RESPONSE_TOO_LARGE
                body = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() - started > self.__timeout:
                        return OpenRouterMetadataFailure.TIMEOUT
                    if len(body) + len(chunk) > self.__max_body:
                        return OpenRouterMetadataFailure.RESPONSE_TOO_LARGE
                    body.extend(chunk)
                parsed = _parse(bytes(body), self.__key)
            if monotonic() - started > self.__timeout:
                return OpenRouterMetadataFailure.TIMEOUT
            return parsed
        except httpx.TimeoutException:
            return OpenRouterMetadataFailure.TIMEOUT
        except ssl.SSLError:
            return OpenRouterMetadataFailure.TLS_ERROR
        except httpx.TransportError as error:
            cause: BaseException | None = error
            for _ in range(5):
                if isinstance(cause, ssl.SSLError):
                    return OpenRouterMetadataFailure.TLS_ERROR
                cause = (cause.__cause__ or cause.__context__) if cause is not None else None
            return OpenRouterMetadataFailure.NETWORK_ERROR
        except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
            return OpenRouterMetadataFailure.INVALID_RESPONSE
        except Exception:  # noqa: BLE001 - never expose arbitrary transport exception secrets.
            return OpenRouterMetadataFailure.TRANSPORT_ERROR

    def verify_key(self) -> OpenRouterKeyResult:
        data = self._get("key")
        if isinstance(data, OpenRouterMetadataFailure):
            return OpenRouterKeyResult("FAILED", data, None, None)
        try:
            metadata = _key_metadata(data)
        except (ValueError, KeyError, TypeError, OverflowError):
            return OpenRouterKeyResult(
                "FAILED", OpenRouterMetadataFailure.INVALID_RESPONSE, None, None
            )
        return OpenRouterKeyResult("VERIFIED", None, datetime.now(UTC), metadata)

    def retrieve_model(self) -> OpenRouterModelResult:
        data = self._get("model/" + self.__model)
        if isinstance(data, OpenRouterMetadataFailure):
            return OpenRouterModelResult(self.__model, "FAILED", data, None, None)
        try:
            metadata = _model_metadata(data, self.__model)
        except (ValueError, KeyError, TypeError, OverflowError):
            return OpenRouterModelResult(
                self.__model, "FAILED", OpenRouterMetadataFailure.INVALID_RESPONSE, None, None
            )
        return OpenRouterModelResult(
            self.__model, "CATALOGUE_FOUND", None, datetime.now(UTC), metadata
        )

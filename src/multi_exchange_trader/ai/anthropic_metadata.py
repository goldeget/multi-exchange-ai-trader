"""Explicit, bounded Models GET only. This module never authorizes generation."""

import json
import math
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from typing import Literal, cast

import httpx

_VERSION = "2026-10-21"
_BASE = "https://api.anthropic.com/v1/models/"
_MODEL = re.compile(r"claude-[a-z0-9][a-z0-9.-]{0,119}", re.ASCII)
_KEY = re.compile(r"[A-Za-z0-9_.-]{8,512}", re.ASCII)
_DATE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_BASIC = ("batch", "citations", "code_execution", "image_input", "pdf_input", "structured_outputs")


class MetadataFailure(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
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
class ModelMetadata:
    resolved_model_id: str
    display_name: str
    created_at: datetime
    deprecated_at: datetime | None
    retires_at: datetime | None
    lifecycle: Literal["active", "deprecated", "retired"]
    line: str | None
    max_input_tokens: int | None
    max_tokens: int | None
    # Only documented paths, never arbitrary provider objects.
    capabilities: tuple[tuple[str, bool | None], ...] | None


@dataclass(frozen=True, slots=True)
class MetadataResult:
    requested_model_id: str
    status: Literal["VERIFIED", "FAILED"]
    failure: MetadataFailure | None
    verified_at: datetime | None
    metadata: ModelMetadata | None
    generation_enabled: Literal[False] = field(default=False, init=False)
    provider_balance: None = field(default=None, init=False)


def _model(value: object) -> str:
    if not isinstance(value, str) or not _MODEL.fullmatch(value) or "sk-ant-" in value:
        raise TypeError("Invalid metadata response")
    return value


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Invalid metadata response")
    return cast(dict[str, object], value)


def _date(value: object) -> datetime:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("Invalid metadata response")
    return datetime.fromisoformat(value).astimezone(UTC)


def _nullable_date(value: object) -> datetime | None:
    return None if value is None else _date(value)


def _tokens(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**31 - 1:
        raise ValueError("Invalid metadata response")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid metadata response")
        result[key] = value
    return result


def _number(raw: str) -> Decimal:
    if len(raw) > 128:
        raise ValueError("Invalid metadata response")
    value = Decimal(raw)
    if not value.is_finite() or abs(value.adjusted()) > 308:
        raise ValueError("Invalid metadata response")
    return value


def _constant(raw: str) -> None:
    raise ValueError("Invalid metadata response")


def _bounded(value: object, key: str, depth: int = 0) -> None:
    """Validate discarded future fields too, including escaped credential echoes."""
    if depth > 12:
        raise ValueError("Invalid metadata response")
    if isinstance(value, str):
        lower = value.lower()
        if (
            len(value) > 4096
            or key in value
            or "sk-ant-" in lower
            or "bearer " in lower
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Invalid metadata response")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("Invalid metadata response")
        for name, item in value.items():
            _bounded(name, key, depth + 1)
            _bounded(item, key, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("Invalid metadata response")
        for item in value:
            _bounded(item, key, depth + 1)
    elif isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 10**308:
            raise ValueError("Invalid metadata response")


def _capabilities(value: object) -> tuple[tuple[str, bool | None], ...] | None:
    if value is None:
        return None
    data = _object(value)
    result: list[tuple[str, bool | None]] = []

    def support(item: object, path: str, *, nullable: bool = False) -> None:
        if nullable and item is None:
            result.append((path, None))
            return
        supported = _object(item)["supported"]
        if type(supported) is not bool:
            raise ValueError("Invalid metadata response")
        result.append((path, supported))

    for name in _BASIC:
        support(data[name], name)
    for name, children, nullable in (
        (
            "context_management",
            ("clear_thinking_20251015", "clear_tool_uses_20250919", "compact_20260112"),
            True,
        ),
        ("effort", ("high", "low", "max", "medium", "xhigh"), False),
        ("server_tools", ("code_execution", "web_search"), False),
    ):
        item = _object(data[name])
        support(item, name)
        for child in children:
            support(
                item[child],
                name + "." + child,
                nullable=nullable or (name == "effort" and child == "xhigh"),
            )
    thinking = _object(data["thinking"])
    support(thinking, "thinking")
    types = _object(thinking["types"])
    for name in ("adaptive", "between_tools", "disabled", "enabled"):
        support(types[name], "thinking.types." + name)
    return tuple(result)


def _parse(body: bytes, key: str) -> ModelMetadata:
    value: object = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_float=_number,
        parse_constant=_constant,
    )
    _bounded(value, key)
    data = _object(value)
    if data["type"] != "model":
        raise ValueError("Invalid metadata response")
    name = data["display_name"]
    if not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.strip():
        raise ValueError("Invalid metadata response")
    lifecycle = data["lifecycle"]
    if lifecycle not in ("active", "deprecated", "retired"):
        raise ValueError("Invalid metadata response")
    line = data["line"]
    if line is not None and (
        not isinstance(line, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", line)
    ):
        raise ValueError("Invalid metadata response")
    return ModelMetadata(
        _model(data["id"]),
        name,
        _date(data["created_at"]),
        _nullable_date(data["deprecated_at"]),
        _nullable_date(data["retires_at"]),
        lifecycle,
        line,
        _tokens(data["max_input_tokens"]),
        _tokens(data["max_tokens"]),
        _capabilities(data["capabilities"]),
    )


def _status(code: int) -> MetadataFailure | None:
    if code == 200:
        return None
    mapped = {
        401: MetadataFailure.UNAUTHORIZED,
        403: MetadataFailure.FORBIDDEN,
        404: MetadataFailure.MODEL_NOT_FOUND,
        429: MetadataFailure.RATE_LIMITED,
    }
    if code in mapped:
        return mapped[code]
    if 300 <= code < 400:
        return MetadataFailure.REDIRECT_REJECTED
    if 500 <= code < 600:
        return MetadataFailure.PROVIDER_UNAVAILABLE
    return MetadataFailure.HTTP_ERROR


class AnthropicMetadataClient:
    """No I/O at construction. Each explicit retrieve performs at most one GET.

    A private credential is retained only for this transport, never a public
    config/result/repr. No environment, file, SDK, fallback or Messages path.
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
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Invalid metadata configuration") from None
        self.__key = api_key
        self.__model = model_id
        self.__transport = transport
        self.__timeout = float(timeout_seconds)
        self.__max_body = max_body_bytes

    def __repr__(self) -> str:
        return "AnthropicMetadataClient()"

    def _failure(self, failure: MetadataFailure) -> MetadataResult:
        return MetadataResult(self.__model, "FAILED", failure, None, None)

    def retrieve(self) -> MetadataResult:
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
                    _BASE + self.__model,
                    headers={
                        "Authorization": "Bearer " + self.__key,
                        "anthropic-version": _VERSION,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                failure = _status(response.status_code)
                if failure is not None:
                    return self._failure(failure)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return self._failure(MetadataFailure.INVALID_RESPONSE)
                length = response.headers.get("content-length")
                if length is not None:
                    if not re.fullmatch(r"[0-9]{1,20}", length):
                        return self._failure(MetadataFailure.INVALID_RESPONSE)
                    if int(length) > self.__max_body:
                        return self._failure(MetadataFailure.RESPONSE_TOO_LARGE)
                body = bytearray()
                # iter_bytes preserves MockTransport pre-buffered responses; encoding
                # was rejected first, so no decompression can expand the body.
                for chunk in response.iter_bytes():
                    if monotonic() - started > self.__timeout:
                        return self._failure(MetadataFailure.TIMEOUT)
                    if len(body) + len(chunk) > self.__max_body:
                        return self._failure(MetadataFailure.RESPONSE_TOO_LARGE)
                    body.extend(chunk)
                metadata = _parse(bytes(body), self.__key)
            return MetadataResult(self.__model, "VERIFIED", None, datetime.now(UTC), metadata)
        except httpx.TimeoutException:
            return self._failure(MetadataFailure.TIMEOUT)
        except ssl.SSLError:
            return self._failure(MetadataFailure.TLS_ERROR)
        except httpx.TransportError as error:
            cause: BaseException | None = error
            for _ in range(5):
                if isinstance(cause, ssl.SSLError):
                    return self._failure(MetadataFailure.TLS_ERROR)
                cause = (cause.__cause__ or cause.__context__) if cause is not None else None
            return self._failure(MetadataFailure.NETWORK_ERROR)
        except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
            return self._failure(MetadataFailure.INVALID_RESPONSE)
        except Exception:  # noqa: BLE001 - transport exceptions must never expose credentials.
            # Injected transports can raise arbitrary errors carrying request secrets.
            return self._failure(MetadataFailure.TRANSPORT_ERROR)

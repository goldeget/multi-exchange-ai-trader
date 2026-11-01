"""Only synthetic credentials and in-memory HTTP transports are used here."""

import importlib
import json
import ssl
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

KEY = "synthetic-metadata-key-123456"
MODEL = "claude-haiku-5-5"


def payload() -> dict[str, Any]:
    support = {"supported": True}
    return {
        "type": "model",
        "id": MODEL,
        "display_name": "Claude Haiku 5.5",
        "created_at": "2026-10-07T09:30:00+09:00",
        "deprecated_at": None,
        "retires_at": None,
        "lifecycle": "active",
        "line": "haiku",
        "max_input_tokens": 1_000_000,
        "max_tokens": 128_000,
        "capabilities": {
            **{
                name: support.copy()
                for name in (
                    "batch",
                    "citations",
                    "code_execution",
                    "image_input",
                    "pdf_input",
                    "structured_outputs",
                )
            },
            "context_management": {
                "supported": True,
                **{
                    name: None
                    for name in (
                        "clear_thinking_20251015",
                        "clear_tool_uses_20250919",
                        "compact_20260112",
                    )
                },
            },
            "effort": {
                "supported": True,
                "xhigh": None,
                **{name: support.copy() for name in ("low", "medium", "high", "max")},
            },
            "server_tools": {
                "supported": True,
                "code_execution": support.copy(),
                "web_search": support.copy(),
            },
            "thinking": {
                "supported": True,
                "types": {
                    name: support.copy()
                    for name in ("adaptive", "between_tools", "disabled", "enabled")
                },
            },
        },
    }


def client(handler: Any, **kwargs: Any) -> Any:
    module = importlib.import_module("multi_exchange_trader.ai.anthropic_metadata")
    return module.AnthropicMetadataClient(
        KEY, MODEL, transport=httpx.MockTransport(handler), **kwargs
    )


def test_one_explicit_get_current_headers_and_safe_frozen_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://api.anthropic.com/v1/models/claude-haiku-5-5"
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert request.headers["anthropic-version"] == "2026-10-21"
        assert "x-api-key" not in request.headers
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.content == b""
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 5.0,
            "write": 5.0,
            "pool": 5.0,
        }
        return httpx.Response(200, json=payload())

    instance = client(handler)
    assert not requests
    result = instance.retrieve()
    assert len(requests) == 1
    assert result.status == "VERIFIED"
    assert result.failure is None
    assert result.requested_model_id == MODEL
    assert result.metadata.resolved_model_id == MODEL
    assert result.metadata.display_name == "Claude Haiku 5.5"
    assert result.metadata.created_at == datetime(2026, 10, 7, 0, 30, tzinfo=UTC)
    assert result.metadata.max_input_tokens == 1_000_000
    assert result.metadata.max_tokens == 128_000
    assert result.metadata.line == "haiku"
    assert result.metadata.lifecycle == "active"
    assert result.metadata.capabilities
    assert result.verified_at.tzinfo is UTC
    assert result.generation_enabled is False
    assert result.provider_balance is None
    assert KEY not in repr(instance) + repr(result) + repr(asdict(result))
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"


def test_documented_alias_resolution_has_no_second_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        data = payload()
        data["id"] = "claude-haiku-5-5-20261007"
        data["line"] = "future_line"
        data["future"] = {"bounded": [1, 2, None]}
        return httpx.Response(200, json=data)

    result = client(handler).retrieve()
    assert result.status == "VERIFIED"
    assert result.requested_model_id == MODEL
    assert result.metadata.resolved_model_id == "claude-haiku-5-5-20261007"
    assert result.metadata.line == "future_line"
    assert "future" not in asdict(result.metadata)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "MODEL_NOT_FOUND"),
        (429, "RATE_LIMITED"),
        (500, "PROVIDER_UNAVAILABLE"),
        (503, "PROVIDER_UNAVAILABLE"),
        (302, "REDIRECT_REJECTED"),
        (307, "REDIRECT_REJECTED"),
        (400, "HTTP_ERROR"),
    ],
)
def test_safe_status_mapping_never_retries_or_follows_redirects(status: int, expected: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            text=KEY,
            headers={"Location": "https://example.invalid/" + KEY, "Retry-After": "0"},
        )

    result = client(handler).retrieve()
    assert calls == 1
    assert result.status == "FAILED"
    assert result.failure == expected
    assert result.metadata is None and result.verified_at is None
    assert result.generation_enabled is False and result.provider_balance is None
    assert KEY not in repr(result)


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (httpx.ReadTimeout(KEY), "TIMEOUT"),
        (httpx.ConnectError(KEY), "NETWORK_ERROR"),
        (ssl.SSLError(KEY), "TLS_ERROR"),
        (RuntimeError(KEY), "TRANSPORT_ERROR"),
    ],
)
def test_exception_text_never_escapes(exception: Exception, expected: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise exception

    result = client(handler).retrieve()
    assert calls == 1
    assert result.failure == expected
    assert KEY not in repr(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "message"),
        ("id", "https://evil.invalid/model"),
        ("id", "claude-sk-ant-secret"),
        ("display_name", KEY),
        ("display_name", "Bearer another-secret"),
        ("display_name", "Claude\nHaiku"),
        ("display_name", "x" * 129),
        ("created_at", "2026-01-01"),
        ("created_at", "2026-01-01T00:00:00"),
        ("created_at", True),
        ("deprecated_at", 1),
        ("retires_at", "bad-date"),
        ("lifecycle", "unknown"),
        ("line", True),
        ("line", KEY),
        ("max_tokens", True),
        ("max_tokens", 1.0),
        ("max_tokens", -1),
        ("max_tokens", 2**31),
        ("max_input_tokens", "1000000"),
        ("capabilities", []),
        ("capabilities", {"batch": {"supported": 1}}),
        ("future", {"nested": KEY}),
        ("future", {KEY: "value"}),
        ("future", "sk-ant-another-test-secret"),
        ("future", "x" * 4097),
    ],
)
def test_invalid_or_secret_bearing_metadata_is_rejected(field: str, value: object) -> None:
    data = payload()
    data[field] = value
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.failure == "INVALID_RESPONSE"
    assert result.metadata is None
    assert KEY not in repr(result)


@pytest.mark.parametrize("field", tuple(payload()))
def test_every_documented_top_level_field_is_required(field: str) -> None:
    data = payload()
    del data[field]
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


@pytest.mark.parametrize("field", ["created_at", "deprecated_at", "retires_at"])
@pytest.mark.parametrize(
    "offset",
    [
        "+00:60",
        "-00:60",
        "+01:99",
        "-01:99",
        "+23:60",
        "-23:60",
        "+24:00",
        "-24:00",
        "+99:00",
        "-99:00",
    ],
)
def test_invalid_timezone_components_are_rejected_before_normalization(
    field: str,
    offset: str,
) -> None:
    data = payload()
    data[field] = "2026-10-07T12:00:00" + offset
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.status == "FAILED"
    assert result.failure == "INVALID_RESPONSE"
    assert result.metadata is None
    assert result.verified_at is None


@pytest.mark.parametrize("field", ["created_at", "deprecated_at", "retires_at"])
@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        ("+00:00", datetime(2026, 10, 7, 12, 0, tzinfo=UTC)),
        ("+00:59", datetime(2026, 10, 7, 11, 1, tzinfo=UTC)),
        ("-00:59", datetime(2026, 10, 7, 12, 59, tzinfo=UTC)),
        ("+01:30", datetime(2026, 10, 7, 10, 30, tzinfo=UTC)),
        ("-01:30", datetime(2026, 10, 7, 13, 30, tzinfo=UTC)),
        ("+23:59", datetime(2026, 10, 6, 12, 1, tzinfo=UTC)),
        ("-23:59", datetime(2026, 10, 8, 11, 59, tzinfo=UTC)),
    ],
)
def test_valid_timezone_component_boundaries_preserve_utc_conversion(
    field: str,
    offset: str,
    expected: datetime,
) -> None:
    data = payload()
    data[field] = "2026-10-07T12:00:00" + offset
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.status == "VERIFIED"
    assert getattr(result.metadata, field) == expected


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id":"one","id":"two"}',
        b'{"future":{"supported":true,"supported":false}}',
        b'{"future":NaN}',
        b'{"future":Infinity}',
        b'{"future":1e999999}',
        b"[]",
        b"null",
        b"\xff",
        b"{} trailing",
    ],
)
def test_malformed_duplicate_or_nonfinite_json_fails_closed(raw: bytes) -> None:
    result = client(lambda request: httpx.Response(200, content=raw)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


def test_nullable_documented_fields_are_accepted_without_inference() -> None:
    data = payload()
    for name in ("capabilities", "line", "max_tokens", "max_input_tokens"):
        data[name] = None
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.status == "VERIFIED"
    assert result.metadata.line is None
    assert result.metadata.max_tokens is None
    assert result.metadata.capabilities is None
    assert result.generation_enabled is False


@pytest.mark.parametrize(
    "path",
    [
        ("batch",),
        ("batch", "supported"),
        ("context_management", "supported"),
        ("context_management", "compact_20260112"),
        ("effort", "high"),
        ("effort", "xhigh"),
        ("server_tools", "web_search"),
        ("thinking", "types", "disabled"),
        ("thinking", "types", "between_tools", "supported"),
    ],
)
def test_nested_documented_capability_fields_cannot_be_omitted(path: tuple[str, ...]) -> None:
    data = payload()
    node = data["capabilities"]
    for name in path[:-1]:
        node = node[name]
    del node[path[-1]]
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


def test_duplicate_keys_inside_otherwise_valid_metadata_are_rejected() -> None:
    raw = json.dumps(payload()).replace(
        '"supported": true', '"supported": true, "supported": false', 1
    )
    result = client(lambda request: httpx.Response(200, content=raw)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


def test_json_escaped_key_echo_is_rejected_and_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    data = payload()
    data["display_name"] = KEY
    raw = json.dumps(data).replace(KEY, "".join(f"\\u{ord(char):04x}" for char in KEY))
    result = client(lambda request: httpx.Response(200, content=raw)).retrieve()
    assert result.failure == "INVALID_RESPONSE"
    assert KEY not in repr(result) + caplog.text


@pytest.mark.parametrize(
    "future",
    [
        [None] * 129,
        {f"item_{index}": None for index in range(129)},
        10**309,
    ],
)
def test_unknown_fields_are_bounded_even_when_discarded(future: object) -> None:
    data = payload()
    data["future"] = future
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


def test_deep_json_is_rejected() -> None:
    data = payload()
    nested: object = None
    for _ in range(14):
        nested = [nested]
    data["future"] = nested
    result = client(lambda request: httpx.Response(200, json=data)).retrieve()
    assert result.failure == "INVALID_RESPONSE"


def test_wrapped_tls_failure_keeps_safe_tls_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLError(KEY)
        except ssl.SSLError as cause:
            raise httpx.ConnectError(KEY) from cause

    result = client(handler).retrieve()
    assert result.failure == "TLS_ERROR"
    assert KEY not in repr(result)


def test_implicit_tls_exception_context_keeps_safe_tls_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLError(KEY)
        except ssl.SSLError:
            raise httpx.ConnectError(KEY) from None

    result = client(handler).retrieve()
    assert result.failure == "TLS_ERROR"
    assert KEY not in repr(result)


def test_stream_deadline_does_not_allow_trickle_to_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("multi_exchange_trader.ai.anthropic_metadata")
    times = iter([100.0, 106.0])
    monkeypatch.setattr(module, "monotonic", lambda: next(times))
    stream = Chunks()
    result = client(lambda request: httpx.Response(200, stream=stream)).retrieve()
    assert result.failure == "TIMEOUT"
    assert stream.read == 1


class Chunks(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.read = 0

    def __iter__(self) -> Any:
        for _ in range(20):
            self.read += 1
            yield b"x" * 1024


def test_stream_is_stopped_at_body_bound_without_trusting_content_length() -> None:
    stream = Chunks()
    result = client(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Length": "1"}),
        max_body_bytes=2048,
    ).retrieve()
    assert result.failure == "RESPONSE_TOO_LARGE"
    assert stream.read == 3


def test_declared_oversize_is_rejected_before_body_read() -> None:
    stream = Chunks()
    result = client(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Length": "999999"})
    ).retrieve()
    assert result.failure == "RESPONSE_TOO_LARGE"
    assert stream.read == 0


def test_encoded_body_is_rejected_without_decompression() -> None:
    stream = Chunks()
    result = client(
        lambda request: httpx.Response(200, stream=stream, headers={"Content-Encoding": "gzip"})
    ).retrieve()
    assert result.failure == "INVALID_RESPONSE"
    assert stream.read == 0


@pytest.mark.parametrize(
    ("key", "model", "kwargs"),
    [
        ("", MODEL, {}),
        ("test\nkey", MODEL, {}),
        (KEY, "../messages", {}),
        (KEY, "claude-x?key=value", {}),
        (KEY, "claude-sk-ant-secret", {}),
        (KEY, MODEL, {"timeout_seconds": float("nan")}),
        (KEY, MODEL, {"timeout_seconds": float("inf")}),
        (KEY, MODEL, {"timeout_seconds": True}),
        (KEY, MODEL, {"max_body_bytes": True}),
        (KEY, MODEL, {"max_body_bytes": 1_000_000}),
    ],
)
def test_invalid_constructor_inputs_are_fixed_errors_without_network(
    key: str,
    model: str,
    kwargs: dict[str, object],
) -> None:
    module = importlib.import_module("multi_exchange_trader.ai.anthropic_metadata")
    with pytest.raises(ValueError, match="^Invalid metadata configuration$") as captured:
        module.AnthropicMetadataClient(key, model, **kwargs)
    assert KEY not in str(captured.value)

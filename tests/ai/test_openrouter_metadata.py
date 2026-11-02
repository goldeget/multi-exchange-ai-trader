"""Only synthetic credentials and in-memory transports; never real provider calls."""

import importlib
import json
import ssl
from dataclasses import FrozenInstanceError, asdict
from decimal import Decimal
from typing import Any

import httpx
import pytest

KEY = "sk-or-v1-synthetic-test-only-123456789"
MODEL = "anthropic/claude-haiku-5.5"


def key_payload() -> dict[str, Any]:
    return {
        "data": {
            "label": "sk-or-v1-abc...xyz",
            "usage": 0.25,
            "limit": None,
            "limit_remaining": None,
            "is_free_tier": False,
            "is_management_key": False,
            "expires_at": None,
            "workspace_id": "synthetic-workspace",
            "organization_id": None,
            "creator_user_id": "synthetic-user",
            "include_byok_in_limit": False,
            "byok_usage": 0,
            "usage_daily": 0.25,
            "usage_monthly": 0.25,
            "usage_weekly": 0.25,
            "limit_reset": None,
            "rate_limit": {"requests": -1, "interval": "10s"},
        }
    }


def model_payload() -> dict[str, Any]:
    return {
        "data": {
            "id": MODEL,
            "canonical_slug": MODEL,
            "name": "Anthropic: Claude Haiku 5.5",
            "context_length": 200000,
            "created": 1791321600,
            "description": "Synthetic catalogue entry.",
            "expiration_date": None,
            "knowledge_cutoff": None,
            "default_parameters": None,
            "per_request_limits": None,
            "supported_voices": None,
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "tokenizer": "Claude",
                "instruct_type": None,
                "modality": "text->text",
            },
            "pricing": {
                "prompt": "0.000001",
                "completion": "0.000005",
                "request": "0",
                "image": "0",
            },
            "top_provider": {
                "context_length": 200000,
                "max_completion_tokens": 64000,
                "is_moderated": True,
            },
            "supported_parameters": ["temperature"],
        }
    }


def client(handler: Any, **kwargs: Any) -> Any:
    module = importlib.import_module("multi_exchange_trader.ai.openrouter_metadata")
    return module.OpenRouterMetadataClient(
        KEY, MODEL, transport=httpx.MockTransport(handler), **kwargs
    )


def test_explicit_single_gets_distinct_truthful_frozen_results() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.content == b""
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.extensions["timeout"]["read"] == 5.0
        seen.append(str(request.url))
        return httpx.Response(
            200, json=key_payload() if request.url.path == "/api/v1/key" else model_payload()
        )

    api = client(handler)
    assert seen == []  # Constructor performs no I/O.
    key = api.verify_key()
    model = api.retrieve_model()
    assert seen == [
        "https://openrouter.ai/api/v1/key",
        "https://openrouter.ai/api/v1/model/anthropic/claude-haiku-5.5",
    ]
    assert key.status == "VERIFIED" and key.verified_at.tzinfo is not None
    assert key.metadata.usage == Decimal("0.25")
    assert key.metadata.limit is None and key.metadata.limit_remaining is None
    assert key.metadata.is_management_key is False and key.metadata.expires_at is None
    assert model.status == "CATALOGUE_FOUND" and model.retrieved_at.tzinfo is not None
    assert model.metadata.resolved_model_id == MODEL
    assert model.metadata.context_length == 200000
    assert dict(model.metadata.pricing)["prompt"] == "0.000001"
    assert model.model_access_verified is False
    for result in (key, model):
        assert result.failure is None and result.provider_balance is None
        assert result.generation_enabled is False
        assert KEY not in repr(result) and "synthetic-workspace" not in repr(result)
        assert "sk-or-v1-abc" not in repr(asdict(result))
        with pytest.raises(FrozenInstanceError):
            result.generation_enabled = True
    assert KEY not in repr(api)


def test_money_preserves_exact_decimal_and_nullable_limit() -> None:
    body = json.dumps(key_payload()).replace('"usage": 0.25', '"usage": 0.123456789012345678901')
    body = body.replace('"limit": null', '"limit": 100').replace(
        '"limit_remaining": null', '"limit_remaining": 0.000000000000000000001'
    )
    result = client(lambda _: httpx.Response(200, content=body)).verify_key()
    assert result.status == "VERIFIED"
    assert result.metadata.usage == Decimal("0.123456789012345678901")
    assert result.metadata.limit == Decimal(100)
    assert result.metadata.limit_remaining == Decimal("0.000000000000000000001")
    assert result.provider_balance is None


@pytest.mark.parametrize("method", ["verify_key", "retrieve_model"])
@pytest.mark.parametrize(
    ("code", "failure"),
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (429, "RATE_LIMITED"),
        (500, "PROVIDER_UNAVAILABLE"),
        (302, "REDIRECT_REJECTED"),
        (400, "HTTP_ERROR"),
        (201, "HTTP_ERROR"),
    ],
)
def test_http_failures_never_retry_follow_redirect_or_echo(
    method: str, code: int, failure: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            code, headers={"Location": "https://invalid.example/" + KEY}, text=KEY
        )

    result = getattr(client(handler), method)()
    assert result.status == "FAILED" and result.failure == failure
    assert result.metadata is None and len(requests) == 1 and KEY not in repr(result)


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (httpx.ReadTimeout(KEY), "TIMEOUT"),
        (httpx.ConnectError(KEY), "NETWORK_ERROR"),
        (ssl.SSLError(KEY), "TLS_ERROR"),
        (RuntimeError(KEY), "TRANSPORT_ERROR"),
    ],
)
def test_transport_errors_are_safe(error: Exception, failure: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    result = client(handler).verify_key()
    assert result.failure == failure and KEY not in repr(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("usage", None),
        ("usage", True),
        ("usage", "0"),
        ("usage", -1),
        ("limit", -0.1),
        ("limit", "1"),
        ("limit_remaining", False),
        ("is_free_tier", 0),
        ("is_management_key", "false"),
        ("expires_at", "tomorrow"),
    ],
)
def test_invalid_key_schema_fails_closed(field: str, value: object) -> None:
    body = key_payload()
    body["data"][field] = value
    result = client(lambda _: httpx.Response(200, json=body)).verify_key()
    assert result.failure == "INVALID_RESPONSE" and result.metadata is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "anthropic/another-model"),
        ("context_length", True),
        ("context_length", 0),
        ("name", ""),
        ("pricing", {"prompt": 0, "completion": "0"}),
        ("pricing", {"prompt": "NaN", "completion": "0"}),
        ("pricing", {"prompt": "-1", "completion": "0"}),
    ],
)
def test_invalid_model_schema_or_identity_fails_closed(field: str, value: object) -> None:
    body = model_payload()
    body["data"][field] = value
    result = client(lambda _: httpx.Response(200, json=body)).retrieve_model()
    assert result.failure == "INVALID_RESPONSE" and result.metadata is None


@pytest.mark.parametrize("method", ["verify_key", "retrieve_model"])
@pytest.mark.parametrize(
    "addition",
    [
        KEY,
        "Bearer " + KEY,
        {KEY: "value"},
        "sk-or-v1-another-unredacted-credential",
        "a" * 4097,
        [0] * 129,
        {str(i): i for i in range(129)},
        json.loads("[" * 13 + "0" + "]" * 13),
    ],
)
def test_full_tree_bounds_and_credential_reflection(method: str, addition: object) -> None:
    body = key_payload() if method == "verify_key" else model_payload()
    body["future"] = addition
    result = getattr(client(lambda _: httpx.Response(200, json=body)), method)()
    assert result.failure == "INVALID_RESPONSE" and KEY not in repr(result)


@pytest.mark.parametrize(
    "body",
    [
        b'{"data":{},"data":{}}',
        b'{"future":NaN}',
        b'{"future":Infinity}',
        b'{"future":1e9999}',
        b"[]",
        b"bad-json",
        b"\xff",
        b'{"future":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
    ],
)
def test_invalid_json_rejected(body: bytes) -> None:
    assert (
        client(lambda _: httpx.Response(200, content=body)).verify_key().failure
        == "INVALID_RESPONSE"
    )


@pytest.mark.parametrize(
    ("headers", "body", "failure"),
    [
        ({"Content-Length": "65537"}, b"", "RESPONSE_TOO_LARGE"),
        ({}, b"x" * 65537, "RESPONSE_TOO_LARGE"),
        ({"Content-Length": "invalid"}, b"", "INVALID_RESPONSE"),
        ({"Content-Encoding": "gzip"}, b"", "INVALID_RESPONSE"),
    ],
)
def test_response_bounds(headers: dict[str, str], body: bytes, failure: str) -> None:
    result = client(lambda _: httpx.Response(200, headers=headers, content=body)).verify_key()
    assert result.failure == failure


@pytest.mark.parametrize(
    ("key", "model", "kwargs"),
    [
        ("", MODEL, {}),
        ("sk-ant-wrong-provider", MODEL, {}),
        (KEY + "\n", MODEL, {}),
        (KEY, "https://evil.example/x", {}),
        (KEY, "anthropic/../x", {}),
        (KEY, "anthropic/" + KEY, {}),
        (KEY, "anthropic/x?key=value", {}),
        (KEY, MODEL, {"timeout_seconds": float("nan")}),
        (KEY, MODEL, {"timeout_seconds": True}),
        (KEY, MODEL, {"max_body_bytes": 65537}),
    ],
)
def test_configuration_rejects_unsafe_values_without_echo(
    key: str, model: str, kwargs: dict[str, Any]
) -> None:
    module = importlib.import_module("multi_exchange_trader.ai.openrouter_metadata")
    with pytest.raises(ValueError, match="^Invalid metadata configuration$") as error:
        module.OpenRouterMetadataClient(key, model, **kwargs)
    assert KEY not in str(error.value)


def test_expiry_and_management_are_metadata_only() -> None:
    body = key_payload()
    body["data"].update(is_management_key=True, expires_at="2026-01-01T00:00:00Z")
    result = client(lambda _: httpx.Response(200, json=body)).verify_key()
    assert result.status == "VERIFIED" and result.metadata.is_management_key is True
    assert result.metadata.expires_at.year == 2026 and result.generation_enabled is False


def test_unknown_fields_are_discarded_and_total_node_count_is_bounded() -> None:
    body = key_payload()
    body["future"] = {"safe": [None, True, -1, "text"]}
    result = client(lambda _: httpx.Response(200, json=body)).verify_key()
    assert result.status == "VERIFIED" and "future" not in asdict(result)
    body["future"] = [[0] * 128 for _ in range(40)]
    result = client(lambda _: httpx.Response(200, json=body)).verify_key()
    assert result.failure == "INVALID_RESPONSE"


@pytest.mark.parametrize("ticks", [[0.0, 6.0], [0.0, 0.0, 6.0]])
def test_read_deadline_applies_across_stream_and_after_parsing(
    monkeypatch: pytest.MonkeyPatch,
    ticks: list[float],
) -> None:
    module = importlib.import_module("multi_exchange_trader.ai.openrouter_metadata")
    times = iter(ticks)
    monkeypatch.setattr(module, "monotonic", lambda: next(times))
    result = client(lambda _: httpx.Response(200, json=key_payload())).verify_key()
    assert result.failure == "TIMEOUT"


def test_body_limit_applies_to_streamed_chunks() -> None:
    class Chunks(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"x" * 800
            yield b"y" * 800

    result = client(
        lambda _: httpx.Response(200, stream=Chunks()), max_body_bytes=1024
    ).verify_key()
    assert result.failure == "RESPONSE_TOO_LARGE"


def test_tls_exception_wrapped_by_transport_is_classified_without_echo() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(KEY) from ssl.SSLError(KEY)

    result = client(handler).verify_key()
    assert result.failure == "TLS_ERROR" and KEY not in repr(result)


@pytest.mark.parametrize("value", ["a\u0085b", "a\u202eb", {"secret": KEY}, {"x": "Bearer abc"}])
def test_control_and_escaped_secret_in_unknown_fields_are_rejected(value: object) -> None:
    body = key_payload()
    body["future"] = value
    encoded = json.dumps(body, ensure_ascii=True)
    result = client(lambda _: httpx.Response(200, content=encoded)).verify_key()
    assert result.failure == "INVALID_RESPONSE"

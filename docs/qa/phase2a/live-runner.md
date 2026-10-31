# Repeatable public market evidence

Run only when this environment's policy permits the configured exchange hosts, or
manually in your own authorized local environment. Existing cloud CONNECT denials
remain blockers; do not retry denied hosts here, change proxies, use relays, or
substitute alternate hosts to obtain a passing result.

From the repository root:

```sh
uv sync --locked --group dev
uv run python scripts/verify_phase2a_live.py --output work/phase2a-live.json
```

The default output is `work/phase2a-live.json`. `--output` changes only the local
artifact destination. There are no host, credential, proxy, or private-route
options. The script reuses the current Binance and Bybit adapters and Pydantic
validators for BTCUSDT/ETHUSDT × spot/perpetual: eight markets, each with one
ticker and one 1m candle request (limit 2). OKX remains unverified and excluded.

Transport uses inherited environment proxy and TLS verification, five-second
request timeouts, no retries, no redirects, and the exact configured public
endpoint allowlist. Any HTTP 403 or proxy CONNECT 403 blocks that host for the
remaining run. Subsequent checks for that host are recorded as skipped, with no
further network attempt. Other configured hosts are assessed independently.

JSON contains start/end UTC times, checked-out commit SHA, per-request host/path,
HTTP response status (null if no exchange response arrived), duration, sanitized
failure classification (including `proxy_connect` versus `upstream_http` denial),
and each market's normalized decimal price, candle count and OHLCV candle list,
source URLs, fetch times and available provider timestamps. Headers, response
bodies, environment variables and exception messages are never included.

`valid` requires adapter/schema validation, a positive ticker price, nonempty
candles, and freshness within 120 seconds (with at most five seconds future
clock skew). Ticker freshness checks the available provider timestamp. Binance
spot's public price endpoint has no such timestamp: `observed_at` remains null,
and `freshness_basis` explicitly says receipt only. A fresh latest candle is
checked independently for every market. Bybit's ticker timestamp is its response
envelope time, not a last-trade time. These limitations remain visible even when
the matrix passes. Local clock accuracy is required.

`evidence_mode` is `live_network` for the CLI and `injected_transport` when tests
supply a transport. `live_verified` is true only for a passing matrix using the
normal live network transport. Injected fixtures always produce `live_verified:
false`, even if `matrix_passed` is true; they cannot establish live evidence.

Exit 0 means all eight market checks passed this limited runner; exit 1 means at
least one failed or was blocked. `schema`, `stale`, `provider`, `transport`, `timeout`, and
`blocked` distinguish failure classes. `matrix_passed` never becomes true after a
failed check. This report does not by itself complete Phase 2A: browser evidence,
CI/settings checks, and review of timestamp limitations are separate requirements.
A source SHA identifies the checkout; it does not certify a clean working tree.

Offline verification (no exchange access):

```sh
uv run pytest tests/qa/test_live_runner.py -q
```

These tests inject `httpx.MockTransport` below the real adapters. Passing fixture
tests establish runner behavior, not live provider availability. No live evidence
was generated while building this runner because the cloud host denials remain.

# Phase 2A external-settings live recheck

Checked: 2026-10-30 21:57:29 UTC. Baseline: `24b625a6e18a6467624eff98921449615321869e` on `codex/initial-platform`.

Result: **BLOCKED**. The configured exchange allowlist is not present in the current attached runtime. All three configured public exchange hosts are still rejected by the inherited proxy before a TLS tunnel or exchange response is obtained. No actual ticker or candle sample was returned.

## Current runtime evidence

A fresh `cloud_environment.environment_status` observation reports:

| Field | Observed value |
| --- | --- |
| Environment ID | `ccarenv_b64_Y2NhcmVudl8yNGJmOTNjY2IxYzA4MTkxODhiZjJmZDcyMDZiYjljNg` |
| Source config ID | `48bc58a4-94d3-4f4a-9be9-ae3e35def1df~cecfg_6ae4f2cc06308193a2fbe43d20278aa1` |
| Source config version ID | `48bc58a4-94d3-4f4a-9be9-ae3e35def1df~cecfgver_6ae4f35f6ae881939d2b90c28e381c20` |
| `spec_revision` / `observed_spec_revision` | `1` / `1` |
| `observations_current` | `true` |
| HTTP policy mode / state | `restricted` / `enforced` |
| Custom `allowed_hosts` | `[]` |
| Presets | `["package_managers"]` |

The runtime effective host list omits `data-api.binance.vision`, `fapi.binance.com`, and `api.bybit.com`. The executor's `/etc/codex/network-policy.json` independently reports a version-1 restricted policy with none of those three hosts, `vpn_configured: false`, and empty TCP domain/IP grants. The evidence describes this attached runtime; it does not establish whether another environment or a saved configuration draft was edited.

## Sanitized host probes

Each configured host was attempted exactly once with a public unauthenticated GET, using inherited HTTP/HTTPS proxy settings and CA trust with TLS verification enabled. Command form: `curl --silent --show-error --include --connect-timeout 10 --max-time 20 <URL>`. No retry option was used. Times below are measured wall time.

### binance_spot

- UTC start: `2026-10-30T21:57:29.667756+00:00`
- URL: `https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT`
- Elapsed: `0.0128 s`
- Curl exit: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:57:29 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

### binance_perpetual

- UTC start: `2026-10-30T21:57:29.680586+00:00`
- URL: `https://fapi.binance.com/fapi/v2/ticker/price?symbol=BTCUSDT`
- Elapsed: `0.0098 s`
- Curl exit: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:57:29 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

### bybit

- UTC start: `2026-10-30T21:57:29.690374+00:00`
- URL: `https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT`
- Elapsed: `0.0087 s`
- Curl exit: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:57:29 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

## Eight-market ticker and candle matrix

`BLOCKED (host)` means the host-level CONNECT denial prevents verification. Only the three probe URLs above were requested; the remaining symbols/categories/candles were not individually requested after the host denial. Candle scope is all supported intervals: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.

| Exchange | Market | Symbol | Configured public host | Ticker | Candles |
| --- | --- | --- | --- | --- | --- |
| Binance | Spot | BTCUSDT | `data-api.binance.vision` | BLOCKED (host) | BLOCKED (host) |
| Binance | Spot | ETHUSDT | `data-api.binance.vision` | BLOCKED (host) | BLOCKED (host) |
| Binance | USDT perpetual | BTCUSDT | `fapi.binance.com` | BLOCKED (host) | BLOCKED (host) |
| Binance | USDT perpetual | ETHUSDT | `fapi.binance.com` | BLOCKED (host) | BLOCKED (host) |
| Bybit | Spot | BTCUSDT | `api.bybit.com` | BLOCKED (host) | BLOCKED (host) |
| Bybit | Spot | ETHUSDT | `api.bybit.com` | BLOCKED (host) | BLOCKED (host) |
| Bybit | USDT perpetual | BTCUSDT | `api.bybit.com` | BLOCKED (host) | BLOCKED (host) |
| Bybit | USDT perpetual | ETHUSDT | `api.bybit.com` | BLOCKED (host) | BLOCKED (host) |

No ticker price/schema, upstream timestamp, candle count, candle ordering, OHLCV constraint, or freshness check can be marked passed. The proxy's HTTP `Date` header is not a market-data timestamp. This result establishes an environment egress blocker, not exchange service availability or adapter correctness.

## Browser with actual exchange prices

**BLOCKED** for all eight markets. No live exchange data was available for price comparison or actual-data chart rendering. Existing unavailable-state browser evidence, fixture tests, and demonstration data do not establish actual-price browser verification. No sample data was substituted during this recheck.

## Resume condition

Apply the three exact configured hosts to this attached environment using the supported cloud environment configuration workflow. Then verify a fresh runtime observation reports the intended configuration version, current observations, enforced policy, and those hosts in the effective allowlist before rerunning live verification. If an upstream exchange then rejects the request, record that provider response separately from this proxy denial.

The recheck used no API keys, private/account/order endpoints, alternate hosts, direct connections, policy-file edits, paid services, OKX, or Phase 3 work. No additional exchange retries followed the host denials.

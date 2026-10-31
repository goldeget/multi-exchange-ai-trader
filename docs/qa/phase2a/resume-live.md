# Phase 2A resumed live public API verification

Checked: 2026-10-30 21:48:49 UTC. Baseline: `90efa83c` on `codex/initial-platform`.

Result: **BLOCKED** for all eight markets. No successful live ticker or candle response was obtained. These results establish an environment egress blocker, not provider API availability or adapter correctness.

## Environment evidence

The managed environment status reported `observations_current: true`, `spec_revision: "1"`, `observed_spec_revision: "1"`, HTTP policy `restricted`, and policy state `enforced`. Only the `package_managers` preset was enabled; custom `allowed_hosts` was empty. The effective host list and `/etc/codex/network-policy.json` omitted all three configured public exchange hosts. VPN was not configured.

Probes preserved the inherited HTTP/HTTPS proxy and CA trust, kept TLS verification enabled, and used only unauthenticated public ticker GET requests. Each configured host was attempted once. The proxy rejected CONNECT before an exchange response was obtained; therefore repeating the denial for ETH, candles, or other categories on the same host would add no live verification. No keys, order endpoints, alternate hosts, direct connections, policy edits, paid services, or OKX were used.

## Actual sanitized probe evidence

Command form: `curl --silent --show-error --include --connect-timeout 10 --max-time 20 <public URL>`. Curl retry was not enabled.

### binance_spot

- UTC start: `2026-10-30T21:48:49.824470+00:00`
- URL: `https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT`
- Curl exit code: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:48:49 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

### binance_perpetual

- UTC start: `2026-10-30T21:48:49.839569+00:00`
- URL: `https://fapi.binance.com/fapi/v2/ticker/price?symbol=BTCUSDT`
- Curl exit code: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:48:49 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

### bybit

- UTC start: `2026-10-30T21:48:49.853427+00:00`
- URL: `https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT`
- Curl exit code: `56`

```text
HTTP/1.1 403 Forbidden
content-length: 16
content-type: text/plain
date: Fri, 30 Oct 2026 21:48:49 GMT
server: envoy
connection: close
curl: (56) CONNECT tunnel failed, response 403
```

## Eight-market matrix

`BLOCKED (host)` means the configured host was denied by the proxy; this row was not individually requested after that denial. Candle scope is all supported intervals: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. No candle counts, ordering, OHLCV, timestamps, or live price accuracy could be verified.

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

## Resume condition

Use the supported environment configuration workflow to allow `data-api.binance.vision`, `fapi.binance.com`, and `api.bybit.com`, then confirm current readiness and policy state `enforced` before retrying. If policy permits access but a provider returns geographic or service restrictions, record that actual upstream result separately. Live verification remains incomplete until ticker and candle requests for each of the eight markets return validated market data.

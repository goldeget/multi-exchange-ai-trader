# Phase 2-A verification evidence — 2026-10-31 KST

Status: **PARTIAL / EXTERNAL PREREQUISITES BLOCKED**, not complete or live-ready.

## CI

Baseline: `1e6d833c3e6c4aab4ce046cd3680bddf1cbfc2c1`. Repair pushed as `6297cc2a077a3fa5f44e2d000a1dbf02d7a9e487`.

[GitHub run 41128173889](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41128173889) reports Failure with zero jobs and one annotation:

> Invalid workflow file: .github/workflows/ci.yml#L100
>
> You have an error in your yaml syntax on line 100

PyYAML reproduced an unexpected scalar at original line 102 column 47. A block scalar fixes the quoted executable followed by unquoted shell arguments; the command itself is unchanged. Fixed YAML parses with all four jobs retained and Actionlint v1.7.7 exits 0. All five pinned action commits match official version tags. Runner scheduling and billing cannot explain this recorded parser failure; their actual availability remains unknown. Actions policy and dependency-review availability also remain unknown.

The valid workflow runs for PR events or pushes to `main`. Work-branch pushes do not run it. A draft PR targeting `main` is required for the intended verification without changing `main`. GitHub API access returned destination Forbidden before a GitHub JSON response, so no draft PR, new hosted run, or hosted PASS is claimed. No repeated workflow dispatches were attempted.

## Environment and live matrix

Runtime spec/observed revision 7, observations current; policy state `unknown`. The configured exchange hosts are `data-api.binance.vision`, `fapi.binance.com`, `api.bybit.com` and `www.okx.com`. Local policy also lists these hosts, but the runtime skill requires `enforced` before readiness is established. No exchange request was sent in this phase.

Saved draft additions: `api.github.com`, `developers.binance.com`, `bybit-exchange.github.io`; existing hosts and package-manager preset preserved. Draft save reports `requires_publish: true`. User review/save and publication when required remain outstanding. Saving does not apply policy. Public API hosts provide read endpoints used by the adapters; this phase adds no credentials or authenticated request. No OKX request is permitted before contract verification.

| Exchange | Instrument | Market | Price / candles / source time / delay / limits |
|---|---|---|---|
| Binance | BTCUSDT | Spot | UNRUN: policy enforcement unconfirmed |
| Binance | ETHUSDT | Spot | UNRUN: policy enforcement unconfirmed |
| Binance | BTCUSDT | USDT perpetual | UNRUN: policy enforcement unconfirmed |
| Binance | ETHUSDT | USDT perpetual | UNRUN: policy enforcement unconfirmed |
| Bybit | BTCUSDT | Spot | UNRUN: policy enforcement unconfirmed |
| Bybit | ETHUSDT | Spot | UNRUN: policy enforcement unconfirmed |
| Bybit | BTCUSDT | USDT perpetual | UNRUN: policy enforcement unconfirmed |
| Bybit | ETHUSDT | USDT perpetual | UNRUN: policy enforcement unconfirmed |

Current behavior inspected, not live-qualified: upstream requests time out at 10 seconds, redirects are disabled, HTTP 429 maps to `rate_limited`, and there are no immediate backend retries. Browser polling retries tickers at 5 seconds and candles at 30 seconds with no overlapping requests per resource. Provider-specific rate-limit headers/backoff and successful recovery after actual provider rejection remain unverified. The generic Korean UI connection error does not expose the original provider HTTP status; exact live diagnosis must retain sanitized upstream status separately.

## Browser error-path QA

Chromium with Playwright, real-mode URL (no `preview=1`), viewport 1440×1000 and 390×844. All market requests were aborted locally by a browser route installed before navigation. This verifies browser network-error handling only; it is not evidence of actual provider failure or successful live connectivity. No prices or candles were injected. Forty requests were aborted and no browser JavaScript error was observed.

Both sizes display Korean connection errors, unavailable prices (`—`), empty candle state and disabled OKX. ETH, perpetual, 1h and the Bybit row selection update the chart heading consistently. Document scroll width equals viewport width (1440/390), with no overflowing elements. Real price rendering, populated charts, initial live loading, source timestamps/delay, and stale-after-success require a later live pass. Existing fixture tests cover loading/stale behavior but cannot establish live behavior.

| Size | Initial spot/BTC | Selected perpetual/Bybit/ETH/1h |
|---|---|---|
| Desktop 1440 | [Screenshot](1440-spot-btc-error.png) | [Screenshot](1440-perp-bybit-eth-1h-error.png) |
| Mobile 390 | [Screenshot](390-spot-btc-error.png) | [Screenshot](390-perp-bybit-eth-1h-error.png) |

DOM measurements and intercepted request URLs: [browser-qa.json](browser-qa.json).

## Local regression

| Check | Result |
|---|---|
| Backend pytest | 242 passed |
| Ruff / mypy | PASS / PASS (11 source files) |
| Frontend Vitest | 8 passed |
| Frontend lint / typecheck / build | PASS / PASS / PASS |
| Gitleaks v8.30.1 | PASS; verified release archive checksum; 18 baseline commits, no leaks |
| Actionlint v1.7.7 | PASS; verified official release checksum |
| Local health | `GET /api/v1/health` → `{"status":"ok"}` |
| Hosted Actions after repair | UNRUN: API destination unavailable, PR not created |

## Resume gates

1. Review/save the environment draft and publish when required. Confirm current observations and `network_policy.state == enforced`, with exchange and GitHub API hosts allowed. No credential is needed for the market endpoints.
2. Inspect repository Actions/runner/billing settings read-only; create a draft PR from `codex/initial-platform` into `main`, attach it to the task, and observe one PR CI run. Do not merge or purchase capacity.
3. Probe ticker and candle responses for all eight matrix rows, retaining sanitized status, payload shape, price, source/fetch timestamps, latency and rate-limit headers. Distinguish upstream HTTP errors, provider errors, proxy errors and timeouts. Do not retry unchanged access denials or bypass regional restrictions.
4. Repeat desktop/mobile QA with actual data and no request interception. Verify populated charts, exchange/market choices, stale states and overflow. Never replace a failed provider with samples.
5. Update this report and WORK_LOG, commit/push each completed slice and confirm remote SHA. Phase 3 PAPER implementation remains gated on completion of these checks.

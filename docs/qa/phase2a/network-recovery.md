# Phase 2-A network recovery gate — 2026-10-31 KST

Status: **BLOCKED / INCOMPLETE**. Checked 2026-10-30 22:36 UTC (2026-10-31 07:36 KST). Phase 3 has not started.

## Repository checkpoint

`git ls-remote origin refs/heads/codex/initial-platform refs/heads/main refs/pull/1/head` returned:

| Ref | SHA before this documentation checkpoint |
| --- | --- |
| `codex/initial-platform` | `ed9ac0c083c27d83ab3a6280699cdf08e887c61a` |
| PR #1 head | `ed9ac0c083c27d83ab3a6280699cdf08e887c61a` |
| `main` | `7ddcded673ab9b0168fccf1aac106d02ff5a0b6d` |

The clean checkout initially used local branch `work` at the main SHA. Fetched the existing remote work branch and created its same-named local checkout from `FETCH_HEAD`; no new remote branch was created. All changes in this checkpoint are documentation/evidence only. Existing [Draft PR #1](https://github.com/goldeget/multi-exchange-ai-trader/pull/1) is retained without metadata changes, merge, or a replacement PR. Draft status is carried forward from prior verified evidence; it was not freshly queried through the denied API host.

## Applied network policy

After environment startup completed, `cloud_environment.environment_status({})` reported current observations, running/connected, spec and observed revision `1`, and HTTP policy `restricted/enforced`. Custom `allowed_hosts` is empty; the only preset is `package_managers`. Full non-secret runtime policy and source configuration IDs are preserved in [network-recovery-gate.json](network-recovery-gate.json).

| Required host | Current effective policy | Local startup policy | Gate |
| --- | --- | --- | --- |
| `data-api.binance.vision` | absent | absent | BLOCKED |
| `fapi.binance.com` | absent | absent | BLOCKED |
| `api.bybit.com` | absent | absent | BLOCKED |
| `api.github.com` | absent | absent | BLOCKED |

Independent inspection of `/etc/codex/network-policy.json` found version `1`, restricted HTTP policy, `vpn_configured: false`, and empty TCP domains/IP ranges. No wildcard grants any required host. `github.com` is allowed, so Git fetch/ref inspection and the authorized documentation push use the normal inherited proxy/TLS path. This does not establish access to `api.github.com`.

**The required allowlist is not applied.** No exchange probe, executor GitHub API call, retry, alternate host, proxy change, or policy-file edit was performed. Startup's earlier `unknown` policy state was superseded by the running observation above. Desired settings or earlier sessions do not establish this environment's permission. Available Cloud tools expose read-only runtime/diagnostic information, with no configuration publication operation; no policy repair is claimed.

## Eight-market actual-data result

Per the user's prerequisite, `uv run python scripts/verify_phase2a_live.py --output work/phase2a-live.json` was **not executed**. No collector output was fabricated. The JSON linked above is a policy-gate record, not live market evidence.

| Exchange | Market | Symbol | Ticker / candles | Price / OHLCV | Observed / fetched at | Response latency / provider HTTP |
| --- | --- | --- | --- | --- | --- | --- |
| Binance | Spot | BTCUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Binance | Spot | ETHUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Binance | USDT perpetual | BTCUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Binance | USDT perpetual | ETHUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Bybit | Spot | BTCUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Bybit | Spot | ETHUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Bybit | Linear perpetual | BTCUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |
| Bybit | Linear perpetual | ETHUSDT | BLOCKED | unavailable | unavailable | not measured / not requested |

Error state for all rows: `network_policy_host_not_allowed` (preflight classification, not an exchange response). The policy check time is not a market observation or collection timestamp. Zero markets verified; all eight blocked. Synthetic data was not used.

## Browser QA and remaining acceptance

| Viewport | Charts / actual prices / refresh | Connection error and recovery after live success | Normal-data screenshot |
| --- | --- | --- | --- |
| PC 1440×1000 | BLOCKED / UNRUN | BLOCKED / UNRUN | not captured |
| Mobile 390×844 | BLOCKED / UNRUN | BLOCKED / UNRUN | not captured |

Historical mock/error-path screenshots remain supplementary evidence only. There is no fresh browser QA pass or normal-price screenshot in this checkpoint.

The prior [four hosted CI checks](final-recheck-ci.md) and [257 Python / 8 frontend tests](final-recheck-tests.md) remain historical PASS evidence. `git diff --quiet 6c19097a2f265a37db004bafc3c8f44f1266c192 HEAD -- .github src tests scripts apps pyproject.toml uv.lock package.json package-lock.json` exited 0 before this report. No code, dependencies or workflow changed; no full test/CI rerun was requested. Documentation commits use `[skip ci]`.

Resume only after current runtime observations confirm an enforced policy allowing all four required exact hosts. Then execute the collector, retain actual price/candle/timestamp/latency/error evidence for all eight markets, and perform desktop/mobile live chart, refresh, error/recovery and screenshot QA. Until those gates pass, **Phase 2-A is incomplete**.

No exchange keys, account/position reads, order operations, paid services, force pushes, main changes or Phase 3 development were used. Existing Bybit positions were untouched.

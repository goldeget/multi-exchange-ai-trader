# Production host guard and chart continuity — 2026-10-31 KST

**Authorized A/B scope: PASS. Full PHASE2A: PARTIAL / six markets BLOCKED.** Baseline6acd2680cad70ddb5a3d3442a20997194439b791 and existing work/backups/main/Draft PR preserved. [Sequential plan](PLAN.md) was saved before implementation. No subagents, credentials, accounts, positions, orders, purchases, deployments, regional bypass or Phase3.

## A. Production denial protection

Source[9ef0e244491c2a6508e169bb4f6c65a275c947b5](https://github.com/goldeget/multi-exchange-ai-trader/commit/9ef0e244491c2a6508e169bb4f6c65a275c947b5), [code and meaningful regression diff](code.diff). This corrects production, not only the verification CLI. The default FastAPI lifespan shares one PublicMarketClient across adapters. Each supported host has its own asyncio lock enclosing blocked-state check, HTTPX send and response classification. First403/451 stores terminal host state before releasing the lock; queued/future ticker/candle/symbol/market requests make zero additional sends. Other hosts use independent locks. Body-less GETs are restricted to fixed public market URLs; redirects are forced off, no retry/probe/TTL was added. Existing inherited proxy/CA/TLS behavior is retained.

The bounded fixed-phrase classifier was moved to market_data/denials.py and reused by production/base adapters and verifier. A bare403/451 is access_restricted, not proof of country. Only recognized geographic response phrases produce geographic_restriction. Raw bodies, IPs, identifiers and arbitrary provider messages are never serialized. The unchanged API503 handler emits only safe reason and fixed generic message.

| Reason | Meaning | UI |
| --- | --- | --- |
| operator_disabled | Seeded by deployment environment before first send | 운영 설정으로 비활성화 · 재시도 중단 |
| access_restricted | Upstream/proxy denial without geographic evidence | 상류 접근 제한 · 재시도 중단 |
| geographic_restriction | Fixed geographic phrase recognized | 지역 제한 확인 · 재시도 중단 |

UI stops that resource's polling timer on these reasons. Previous value, receipt/observation timestamp and source remain explicitly retained, with terminal status taking precedence over stale/success. Candle receipt/source is shown too. Generic timeout/transient/unknown errors continue recovery polling; changing resources starts the new selection without old data. React StrictMode can issue two initial local API calls, but counts did not increase after terminal status during the34-second observation. Those are local API calls, not external sends.

### Environment and operational boundary

The prior unconfigured QA server was stopped before any new app QA. The current server was started only with:

```sh
MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com uv run uvicorn multi_exchange_trader.main:app --host 127.0.0.1 --port 8000
```

Accepted names are exactly data-api.binance.vision, fapi.binance.com, api.bybit.com (currently supported production public hosts). Comma-separated whitespace is trimmed, duplicates coalesce; an empty setting means no operator preblock. URLs, ports, paths, alternative/uppercase/unknown names or empty entries in a nonempty list fail startup with a safe named configuration error. No global permanent Bybit disable is hardcoded. OKX remains independently unverified/disabled, with zero requests.

**State is process-local and app-lifespan scoped.** Dynamic denial is not shared between workers. Every worker must receive the same reviewed preblock environment: each independently seeds its guard before sends, guaranteeing configured hosts zero first transmissions across restarts/workers. No automatic reset, TTL, health probe or reset endpoint exists. Unblocking requires review of the authorized operational environment/settings and a controlled restart; it does not authorize retrying the known geographically restricted hosts here. Keep both hosts configured in this QA environment. Do not remove proxy/TLS verification or use alternate endpoints.

[Outgoing observation](outgoing-counts.json): fapi0, Bybit0 since the configured QA server restart, including RED, GREEN and final1m rolling QA. Only Binance spot had sends. The QA-only[server wrapper](qa-server.py) installs a pre-dispatch HTTPX request hook after the guard and asserts/records any forbidden attempt before network dispatch. The hook never fired for blocked hosts. It adds no public endpoint and changes no provider response. Actual QA observed one worker; two independent lifespans in offline production tests exercise worker/restart seeding. Browser refresh and Bybit/perpetual selection were tested through the real API, with no browser route abort substituting for backend protection.

### Offline RED/GREEN

[Concise RED](red-summary.txt): backend12 failed/1 passed, including concurrent fapi transmissions5 instead of1 and failure to preblock first send. Frontend5 failed/10 passed: no differentiated terminal message/poll stop. Chart lifecycle2 failed: re-created chart and uncleared empty data. These are offline httpx.MockTransport/UI fixtures, never real provider success.

Backend GREEN13 dedicated production tests: concurrent403/451, host independence while denied host waits, zero first sends over ticker/candle/symbol/spot/perpetual on two independent configured lifespans, invalid configuration, safe classification/no sensitive body leakage, timeout recovery. Full[Python277](python-final.txt),[Ruff](ruff-final.txt),[mypy14 files](mypy-final.txt) PASS. Initial frontend[17 tests](frontend-final.txt),[ESLint](lint-final.txt),[TypeScript](types-final.txt),[build](build-final.txt) PASS. Import/context formatting errors were corrected before final checks. [KST execution times](execution-times.json).

## B. Chart continuity, selections and mobile interaction

Initial actual chart RED: [before/after SDK ranges](chart-live-red.json) show each viewport re-created1→2 charts and reset user range to0..199 on a real30-second poll. The minimum lifecycle change creates/removes per selected key, updates the two series in place, fits initial/new selection only, and clears series on empty data. The key includes venue/symbol/market/interval, so selection changes discard old data and chart state. Offline lifecycle tests verify empty→data, periodic updates, new key, series clear and unmount removal.

[Actual desktop](live-desktop.json) and[actual mobile](live-mobile.json) QA,08:29:23–08:30:15 KST: same-selection real30-second response updated data without chart instance/range change; Bybit terminal local API counts remained unchanged after termination. BTC/ETH and six intervals, venue/spot/perpetual switching, empty blocked selections (no old canvas), return to spot, keyboard ArrowDown/Enter, focus-visible, accessible labels/status roles, refresh, resize and restore all passed. A single live chart and height380px were checked, document width matched1440/390. Uncaught page errors0. This is basic keyboard/status testing, not full accessibility certification.

Mobile pinch used actual CDP two-touch start/move/end events, not a mocked zoom: logical span changed from about180.82 to97.23 on the same chart. [Pinch evidence](mobile-pinch.png). Device touch was emulated in Chromium, not a physical phone. The local module route instruments chart SDK references/removal/range only; no market response, price or time was injected. A Vite variable-renaming mismatch initially broke the harness; its matcher was corrected without a product workaround.

### Rolling-window review defect and correction

The first live30-second test exercised a15m batch without leading-bar removal; it did not establish rolling-window correctness. User code review correctly found that restoring the old logical range unchanged shifts historical time when a200-bar window drops its first bar. This is recorded as an initial missed boundary, not hidden by the earlier PASS.

[Rolling RED](rolling-red.txt):3 failed/3 passed. Drop-first expected{-0.5,0.5} but got{0.5,1.5}; prepend and no-shared-timestamp cases failed too. Append-only and existing lifecycle cases passed. Source[fbe5349549ea9c892bbbfc3757b4f53dbba75dc4](https://github.com/goldeget/multi-exchange-ai-trader/commit/fbe5349549ea9c892bbbfc3757b4f53dbba75dc4),[separate rolling diff](rolling-code.diff), touches only CandleChart and its tests; production guard unchanged.

For a common timestamp anchor, shift the old logical range by newIndex-oldIndex. Same-batch/append offset is0; drop-first is−1; prepend is+1. Width remains unchanged. If there is no shared timestamp (or first data after empty), explicitly fit the new batch instead of forcing an obsolete empty viewport. Selection-key initialization remains unchanged. [Final frontend21](rolling-green.txt),[lint](rolling-lint.txt),[typecheck](rolling-types.txt),[build](rolling-build.txt) PASS; backend code was not redundantly changed/retested locally for this frontend-only correction.

Final real1m rolling QA: [desktop](rolling-live-desktop.json),[mobile](rolling-live-mobile.json). Waited for the exchange's actual new minute, with normal30-second polling only; first candle dropped exactly1. Both logical ranges shifted exactly−1, SDK visible timestamp endpoints stayed identical, zoom width and chart instance count stayed unchanged, height380/no horizontal overflow/no pageerror. Four real before/after screenshots retained. No synthetic batch replaced the real market data. Offline fixtures separately prove prepend and disjoint-batch policies.

## CI and remaining gates

[CI41144951973](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41144951973) on9ef0e24 and the separately authorized[CI41145424278](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41145424278) onfbe5349 both completedSUCCESS: Python checks, Frontend checks, Secret scan and Dependency review allPASS. Actual job/step records:[initial](ci.json),[rolling](rolling-ci.json). Exactly two new source runs; no rerun, weakened gate, dependency/lock/workflow/permission change. Detailed hosted logs on an unallowed storage host were not retried; API results satisfy the hosted result check, local test counts are labeled as local. Existing workflow backup artifacts ran naturally; no manual duplicate backup or deletion. Final evidence-only commits use[skip ci].

| Acceptance | Result |
| --- | --- |
| Production denial serialization/preblock/reasons/poll stop | PASS (offline production API + actual configured server) |
| PC/mobile same-batch and actual rolling timestamp continuity | PASS |
| Mobile pinch, basic keyboard/status/focus,380px layout | PASS within stated emulation/basic scope |
| Two normal Binance spot markets | Actual-data QA PASS within this scope |
| Previously restricted Binance perpetual and Bybit six markets | BLOCKED; no new external requests |
| Full PHASE2A acceptance | NOT COMPLETE |
| Physical-phone/screen-reader/exhaustive accessibility | UNRUN |

All original UTC data times remain preserved. Screenshots include full-page height at stated viewports. No new exchange diagnostics, unblocking probes, synthetic live success, auth/position/order calls, paid infrastructure, deployments, merge/force-push or Phase3. Final remote SHA/main/Draft PR are verified after the documentation push.

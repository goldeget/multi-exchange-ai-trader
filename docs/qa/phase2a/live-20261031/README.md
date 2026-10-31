# PHASE 2-A live checkpoint — 2026-10-31 (Asia/Seoul · KST)

Overall: **PARTIAL / NOT COMPLETE**. This checkpoint supersedes earlier missing-allowlist and proxy-infrastructure conclusions. Existing source baseline is `ed30b94d9c77c8924dbef0cf97df91853ea04269`.

## Policy and execution recovery

Runtime spec/observed revision 1, observations_current=true, running, connectivity=connected, restricted/enforced. Custom allowed hosts are data-api.binance.vision, fapi.binance.com, api.bybit.com and api.github.com. Package-manager preset remains enabled; general Internet remains restricted. Startup policy independently matches. No VPN is configured. See [sanitized policy](policy.json).

Default execution could resolve the proxy but socket access returned EPERM (errno 1). HTTPS_PROXY matched the policy address without exposing its value. EPERM alone did not prove external proxy failure. Official `require_escalated` read-only `git ls-remote origin refs/heads/main refs/heads/codex/initial-platform` succeeded; fetch then restored the existing branch. No proxy, CA, TLS, destination or route changes were made. No restart tool exists. Prior local infrastructure JSON is retained under work/ and annotated as superseded.

Remote main remains `7ddcded673ab9b0168fccf1aac106d02ff5a0b6d`; initial remote working head matched ed30b94. PR #1 was read through the API: open, draft=true, merged=false, base=main. Initial local worktree was clean except this session's work/ evidence; no original backup or uncommitted file was deleted. No duplicate backup was generated.

## Eight-market live collection

Command: `uv run python scripts/verify_phase2a_live.py --output work/phase2a-live.json`, official execution permission, unchanged environment proxy/TLS. Exit 1; matrix_passed=false, live_verified=false. [Original live JSON](phase2a-live.json) retains prices, normalized OHLCV, UTC source/receipt timestamps, HTTP and request latency. Collection window: 2026-10-30 22:54:07–22:54:12 UTC (2026-10-31 07:54 KST).

| Exchange | Market | Symbol | Acceptance | Actual result |
| --- | --- | --- | --- | --- |
| Binance | Spot | BTCUSDT | PASS (collector scope) | ticker 76994.01000000; HTTP 200; ticker 949.571 ms / candles 1012.852 ms |
| Binance | Spot | ETHUSDT | PASS (collector scope) | ticker 2238.35000000; HTTP 200; ticker 847.435 ms / candles 183.769 ms |
| Binance | USDT perpetual | BTCUSDT | BLOCKED (runner: failed/provider) | ticker/candles HTTP 451 |
| Binance | USDT perpetual | ETHUSDT | BLOCKED (runner: failed/provider) | ticker/candles HTTP 451 |
| Bybit | Spot | BTCUSDT | BLOCKED | first ticker upstream HTTP 403; remaining host calls skipped |
| Bybit | Spot | ETHUSDT | BLOCKED | host previously denied; no network request |
| Bybit | USDT perpetual | BTCUSDT | BLOCKED | host previously denied; no network request |
| Bybit | USDT perpetual | ETHUSDT | BLOCKED | host previously denied; no network request |

Both spot candles were current 1m OHLCV batches with two ordered items and latest timestamp 22:54:00 UTC. Binance spot ticker supplies no provider observation time: receipt-time freshness only, not proof of trade-time freshness. This collector is a single snapshot and does not alone establish update integrity.

HTTP 451 and upstream 403 are destination HTTP results, distinct from proxy CONNECT denial or default sandbox EPERM. Exact geographic restriction is **unconfirmed**: the existing collector did not retain response bodies/headers. No denied-host diagnostic requests were added. Limitation: the existing collector only blacklists 403, so it sent four requests to fapi after the initial 451. This does not satisfy strict stop-on-any-host-denial behavior. No further requests to that host were made. No claim that all eight markets passed.

## Browser QA

Results follow in browser-qa.json and screenshots; all normal-data images use the live app without preview=1 or injected market data. Denied Bybit/perpetual local API routes are aborted in the browser before reaching the backend to prevent repeat upstream requests. Their error displays are supplementary local failure evidence, not upstream recovery evidence.

## Historical verification and boundaries

Historical CI run 41135809249 attempt 2 PASS applies to code SHA `6c19097a2f265a37db004bafc3c8f44f1266c192`. Historical 257 Python / 8 frontend tests and lint/type/build passes remain historical, not newly executed. No full CI or tests were repeated for this documentation/evidence-only change; commit uses [skip ci]. Source/workflows/locks are unchanged. Existing backup remains preserved. No secrets, authenticated exchange calls, accounts/positions/orders, paid services, deployment, main mutation/merge, force push or Phase 3 implementation.

### Observed browser results

- **PASS:** desktop viewport1440x1000 and mobile390x844; document scrollWidth equals viewport width (1440/390), Korean labels visible. Screenshots were visually inspected: populated actual-price candle charts and responsive layout, no page-level horizontal overflow. Full-page PNG height exceeds viewport height by design.
- **PASS:** BTC and ETH selection, six intervals (1m/5m/15m/1h/4h/1d) on both viewports, 24 successful candle-response checks. Browser evidence retains 61 actual HTTP200 market responses and UTC receipt times.
- **PASS within limited scope:** desktop32-second automatic polling window produced15 successful local API responses; selected price data and chart were populated. No provider trade-time freshness claim for Binance spot.
- **PASS (supplementary induced fault):** desktop local network abort17seconds produced two ticker stale labels; restoring real successful routes produced two received labels. Screenshots preserve stale/recovered state. This is not recovery of Bybit/fapi access.
- **PASS:** exchange selector changed chart heading to Bybit; spot/perpetual switching displayed error and returned to actual spot data. Denied routes were locally aborted; no successful denied-market data is claimed.
- **PASS:** zero uncaught page JavaScript errors. Expected resource/network failures from intentional local route aborts occurred; not a claim of zero browser console network errors.
- **BLOCKED:** real Bybit/perpetual charts, update integrity and exchange-side recovery.
- **UNRUN:** mobile stale/recovery timing, long-duration candle60-second stale behavior, actual rate-limit behavior, chart interaction/zoom/accessibility exhaustive QA. No overall browser/full Phase2A acceptance claim.

[Desktop live](desktop-live.png) · [Mobile live](mobile-live.png) · [Desktop stale](desktop-ticker-stale.png) · [Desktop recovered](desktop-recovered.png) · [Machine-readable browser evidence](browser-qa.json).

The first scratch QA attempt failed because its exact-label selector did not match the DOM; only the untracked QA harness selector was corrected. No product source was changed.

# Phase 2-A follow-up — 2026-10-31 KST (Asia/Seoul)

**Historical first follow-up checkpoint:** the chart growth FAIL recorded below was subsequently investigated and corrected in[the chart stability checkpoint](../chart-stability-20261031/README.md). Original failure images/JSON remain unchanged. Regional restrictions remain current.

**PARTIAL / EXTERNAL GEOGRAPHIC RESTRICTIONS BLOCKED. Not all eight markets pass.** Source baseline6a28d63b484b089326cd3be6a75add3e45602abc preserved; code checkpoint[dcd571d64ad27fa849bd54f9919406fd0a14db74](https://github.com/goldeget/multi-exchange-ai-trader/commit/dcd571d64ad27fa849bd54f9919406fd0a14db74). Existing untracked work/ files and backups preserved. Main/Draft PR unchanged.

## Root cause, RED/GREEN and regression

The collector blacklisted only403. An HTTP451 was passed to the adapter, mapped to generic upstream, and subsequent same-host requests were transmitted. [Code/test diff](code-fix.diff): extend the existing denial branch to403/451, preserving host-local blocking, no retry/redirect or alternate endpoint. Add bounded first8192-byte phrase recognition returning only fixed category/evidence literals; no arbitrary provider messages, headers, IPs or identifiers are serialized.

[RED](red-summary.txt): offline httpx.MockTransport test reproduced actual fapi transport4 vs required1 before modifying production code;6 failed/16 passed. Existing403 case passed. [GREEN](green.txt):22 passed. Mixed-host regression verifies exactly1 fapi transmission,3 later same-host skips, unaffected4 spot/8 Bybit transmissions, incomplete matrix/live_verified=false. Fixed-phrase tests cover geographical, rate-limit, WAF and unknown categories, including secret/IP-like fixture text never serialized. Existing successful injected transports still cannot be live evidence. These fixtures do not establish live exchange success.

Fresh full regressions: [Python264](pytest.txt), [Ruff](ruff.txt), [mypy12 files](mypy.txt), [frontend8](frontend-tests.txt), [ESLint](frontend-lint.txt), [TypeScript](frontend-types.txt), [build](frontend-build.txt) PASS. [Completion timestamps in KST](execution-times.json). The initial default-sandbox attempts hit uv cache permissions/async execution stalls; those processes were stopped. Official execution permission produced the recorded RED/GREEN and complete regression results. No test failures were omitted from the successful official runs.

## Exactly two newly authorized diagnostics

Current runtime remained revision1/current/running/connected/restricted/enforced; all four approved custom hosts present, package-manager preset on. Proxy/TLS/CA/routes unchanged. Exactly one public ticker GET per previously denied host, no retries/redirects/fallbacks: [sanitized diagnostic JSON](one-shot-diagnostics.json). No complete eight-market collector rerun.

| Host | KST request time | HTTP | Fixed response-body evidence | Classification |
| --- | --- | --- | --- | --- |
| fapi.binance.com | 2026-10-31 08:05:30 |451| restricted location | Geographic restriction; specific country unknown |
| api.bybit.com | 2026-10-31 08:05:30 |403| block access from your country | Country restriction; specific country unknown |

Unlike the previous body-less451 observation, this one-shot response contains geographical evidence. Neither is a proxy CONNECT denial. No positive WAF/rate-limit evidence; those are not asserted as causes. Raw bodies were examined in memory only and not saved, printed or committed. Only fixed sanitized phrases are retained. No IP, request ID, diagnostic signature or credential was copied.

The user-provided official[Bybit HTTP error reference](https://bybit-exchange.github.io/docs/v5/error) lists403 possibilities: rate limit, empty JSON GET body, US IP. The user-provided[guide](https://bybit-exchange.github.io/docs/v5/guide) describes US/mainland China IP restrictions. These documentation possibilities are distinct from our actual response evidence and do not prove the executor's country. Those documentation URLs were not freshly fetched in this run. Adapter code `request_json` uses `client.get(...params=...,follow_redirects=False,auth=None)` without json/data/body; the MockTransport request assertions and actual diagnostic `request_body_bytes=0` confirm no empty JSON object was sent. Both are body-less GETs, not GETs with `{}` JSON bodies. No additional requests to these hosts after the one-shot diagnostics. All six affected markets remain BLOCKED.

## CI

One normal code push triggered[run41142392526](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41142392526), source SHA dcd571d64ad27fa849bd54f9919406fd0a14db74. [Actual API result and step records](ci.json): completed/success, Python checks PASS, Frontend checks PASS, Secret scan PASS, Dependency review PASS. No rerun/dispatch, no disabled gate or permissions change. The existing workflow's recovery artifact packaging ran naturally with this code CI; no manual duplicate backup. Existing backups were not deleted.

Detailed log download redirects to an Actions results storage host that is forbidden by the current policy, so full hosted logs were unavailable; no alternate download route or allowlist bypass attempted. Job/step completion is confirmed through the allowed GitHub API; test counts above refer to fresh local output, not unread hosted log text. Subsequent evidence-only commit uses[skip ci].

## Remaining browser QA

Actual Binance spot only; denied Bybit/perpetual local API paths aborted before backend to prevent extra external calls. No preview, synthetic market data, price injection or virtual clock. A local network abort is an induced fault, not an actual exchange outage/recovery. PC1440x1000 and mobile390x844 normal/interacted/stale/recovered screenshots and response JSON follow; UTC receipt/source times are preserved.


| Check | Desktop1440x1000 | Mobile390x844 |
| --- | --- | --- |
| Actual normal chart and recovery | PASS | PASS |
| Induced fault70 real seconds; successful responses during fault |0|0|
| Ticker stale>=15s |2 stale labels; PASS|2 stale labels; PASS|
| Candle stale>=60s |1 stale label; PASS|1 stale label; PASS|
| Actual network recovery |2 ticker received,1 candle received,0 ticker stale; PASS|same; PASS|
| Wheel zoom/pointer drag/crosshair |exercised; changed view|exercised plus touch tap; changed view|
| Korean / horizontal overflow |PASS;1440=1440|PASS;390=390|
| Uncaught page JS errors |0|0|
| **Chart height/layout stability** |**FAIL**|**FAIL**|

[Desktop responses/checks](desktop-qa.json),[mobile responses/checks](mobile-qa.json),[KST times and image/layout dimensions](qa-times-layout.json). Successful responses28 total preserve actual data and UTC receipt timestamps. Run2026-10-31 08:06:54–08:08:31 KST. Screenshots8: each viewport has normal,interaction,70s-stale,recovered. Normal/interaction/recovery images were visually inspected. Expected local route-abort network failures are distinct from uncaught JS errors. No actual denied-exchange recovery is inferred.

**New unresolved QA defect:** chart/page height grows at fixed viewport. Desktop page1270→5869px and mobile2045→8633px during the long stale window. Chart bounding height also changed434→566px desktop and416→522px mobile during interaction. Recovery rebuilt the chart but pages remained1709/2777px tall. This is not ordinary document scrolling required by mobile layout; dimensions grow without viewport changes. Element-stability screenshots repeatedly timed out before switching the harness to viewport captures. Source inspection identifies `CandleChart` autoSize plus `.chart` min-height without fixed height as a suspected resize-feedback interaction, but root cause/fix is not experimentally confirmed. The collector-only patch does not change chart source; layout stability remains FAIL and requires a separate fix/verification. Wheel/drag/touch were exercised, but do not certify persistence of zoom across polling updates or exhaustive mobile pinch/accessibility behavior.

**Remaining acceptance gates:** fix and verify chart growth; all six geographically blocked markets and their actual data/update/recovery checks; provider rate-limit behavior. Pinch gestures, zoom persistence across data updates and full accessibility QA remain UNRUN. Existing two-market snapshot remains the only successful matrix subset; no new eight-market success claim. PHASE2A incomplete and PHASE3 not started.

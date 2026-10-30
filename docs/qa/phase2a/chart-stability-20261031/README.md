# Chart resize feedback regression — 2026-10-31 KST

This follow-up addresses the chart-height FAIL discovered in the prior70-second QA. It is separate from collector fix dcd571d64ad27fa849bd54f9919406fd0a14db74. All data is real Binance spot; Bybit/perpetual local API routes are aborted before backend/external requests. No preview, market-data injection, virtual clocks, TLS/proxy changes or restricted-host requests.

## RED and root cause

[Desktop RED](red-desktop.json): fixed1440x1000 chart heights436,560,684,796px over four samples. [Mobile RED](red-mobile.json): fixed390x844 heights402,526,648,772px. The executable regression asserted constant380px and failed before product changes. These reports include a bounded-height hypothesis experiment, explicitly not normal product acceptance evidence.

The user's additional global-table hypothesis was investigated using real DOM computedStyle. [Controlled table-style experiment](table-pollution-experiment.json): internal chart `td` padding was already0px due to library inline styles, so the proposed18px padding did **not** explain the growth. However, global `td` border-bottom1px and `table` table-layout:fixed/border-collapse:collapse leaked into the library's internal table. Desktop438→812px and mobile398→764px within three seconds. Scoping only those global table rules to `.markets` **without adding any height constraint** changed the internal border to0px, layout toauto and collapse toseparate; growth immediately stopped (930px desktop and884px mobile across four samples). Existing market-cell padding18px10px desktop/14px7px mobile and border1px remained unchanged. This isolates table-style pollution as the resize-feedback cause; the already enlarged table did not automatically shrink in that controlled existing-page experiment.

`CandleChart` enables autoSize/ResizeObserver: inflated internal table geometry becomes the next container size, feeding the extra border geometry into repeated resize. `.chart` originally had only min-height; that made the feedback observable, but forcing a height alone would leave the global styling corruption. Production fix scopes all market-table selectors, including responsive variants, to `.markets`. The existing chart min-height and autoSize are retained; no fixed-height masking or broad third-party reset was added.

## Repeatable real-browser regression

[check.cjs](check.cjs) is an executable live acceptance harness, not an assertion on CSS text or mocked chart. From repository root with the existing backend/Vite servers, installed Playwright and Chromium, run `node docs/qa/phase2a/chart-stability-20261031/check.cjs green` (CHROMIUM_PATH optionally selects an installed executable). This uses only real permitted Binance spot. No denied-exchange retry, package download or environment configuration is embedded. `red` reproduces old behavior and runs the CSS hypothesis experiment, so it is intended for the old source only.

The green harness checks chart height380 across real wall-clock samples, internal table-layout:auto/padding0/border0, unchanged positive market padding, no horizontal overflow, responsive resize/restore, both symbols and all six candle intervals, wheel zoom/drag/crosshair/mobile touch, and70-second induced network failure plus real-response recovery. It fails the process on a false acceptance assertion. Screenshots include full pages at the stated viewport sizes; UTC data times remain original. Additional ordinary local API error resources are expected for intentional blocked routes; uncaught page errors are tracked separately.

An initial source-edit command briefly corrupted the market table-layout declaration while prefixing selectors; review caught it, it was corrected, and that in-flight QA attempt was intentionally stopped. Only the fresh run against the corrected final CSS is acceptance evidence. Earlier interrupted output stays under work/. Initial element-capture timeouts from the actual growth were retained as a symptom, not treated as proof of a fix.

## Source/static regressions

After the CSS change, frontend8 tests, ESLint, TypeScript and production build PASS; logs and[test completion times in KST](test-times.json) are retained here. The unchanged backend/collector already passed264 Python tests,Ruff,mypy12files during the preceding code unit. No claim that those unchanged local checks were repeated for CSS alone. The additional source-change CI is authorized separately; no rerun of the earlier passing CI.


## Final GREEN on product CSS

Source[345add1979c7cc07f9e50faf27de78da06c13a07](https://github.com/goldeget/multi-exchange-ai-trader/commit/345add1979c7cc07f9e50faf27de78da06c13a07) scopes17 selectors (including mobile variants); `.chart` retains min-height380 and autoSize. No product fixed-height override was added. [Source diff](chart-fix.diff).

[Desktop GREEN](green-desktop.json) and[mobile GREEN](green-mobile.json): process exited0, both passed=true, all20 assertions/check groups each PASS, uncaught page errors0. Every recorded height is380px, including resizing, restoration, both symbols/six intervals, interactions and each10-second checkpoint during the70-second local fault. Internal table layoutauto, td padding0/border0 are asserted, while market-cell padding remainspositive. Actual normal/interaction/recovery screenshots were visually inspected; the chart and responsive market layout display actual data. The screenshot dimensions show no vertical-growth recurrence: desktop normal/stale/recovered1270px, mobile2119px; responsive resized screenshots are separate. All document widths equal the viewport; no horizontal overflow.

Run timings, response counts, measured min/max height and image dimensions are in[GREEN summary](green-summary.json). The stale test used real elapsed70seconds, no virtual clock, zero successful response during fault, two ticker stale labels and one candle stale label on each viewport. Both recover to actual two ticker/one candle success with no stale labels after restoring only local successful routes. Denied exchange paths remain locally aborted throughout. This verifies an induced local fault, not provider-side access recovery. Market data and UTC timestamps are never synthesized or rewritten.

Initial unbounded-height FAIL and the controlled experiments above are preserved. This GREEN resolves that specific layout defect. Pinch gestures, persistence of zoom across polling updates and exhaustive accessibility are not claimed; wheel zoom/drag/crosshair/touch tap and resize were exercised. Geographic restrictions still block all six affected market acceptance gates, so **PHASE2A remains PARTIAL / NOT COMPLETE**.


## Additional source-change CI — PASS

The separately authorized new code CI[41143157865](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41143157865) completedSUCCESS on345add1979c7cc07f9e50faf27de78da06c13a07. [Actual job/step API record](ci.json): Python checks PASS, Frontend checks PASS, Secret scan PASS, Dependency review PASS. This is a new source-change run, not a rerun of collector CI41142392526 (alsoPASS). No security gate, dependency, lock, workflow or permission change. Hosted detailed log retrieval remains unavailable on the current network; no forbidden storage-host repeat or bypass. Counts from local regressions are explicitly local; API job/step conclusions are actual hosted evidence. Remaining documentation/evidence uses[skip ci].

Both original backups and all scratch files remain preserved; no manual backup regeneration. Main unchanged, Draft PR#1 remains draft/unmerged. Final remote SHA is verified in handoff after the evidence push. No authenticated exchange APIs,account/position/order calls,paid services,server/deployment,force push,main merge or Phase3 implementation.

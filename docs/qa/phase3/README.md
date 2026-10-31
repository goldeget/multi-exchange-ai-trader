# PHASE3 local PAPER verification — 2026-10-31 KST

This report concerns **local synthetic PAPER trading only**. It is not real profit, exchange-account trading, market connectivity or strategy backtesting. PHASE2A remains **PARTIAL: two Binance spot markets previously verified, six regional-restriction markets BLOCKED**. No additional request to fapi.binance.com/api.bybit.com was authorized or sent for this work.

Implemented local PAPER scope: **PASS**, with the unsupported/BLOCKED boundaries below. Final source CI41152341568 passed all four jobs at2026-10-31 09:52KST.

## Implemented and qualified contract

[Design](../../superpowers/specs/2026-10-31-phase3-paper-design.md), [sequential plan](../../superpowers/plans/2026-10-31-phase3-paper.md), [exact native qualification](qualification.md), [LGPL notice](../../licenses/NAUTILUS_NOTICE.md). NautilusTrader **1.231.0** native BacktestEngine/SimulatedExchange performs matching, actual simulated market/reduce-only/protection fills, positions and margin-account cash. The app adds educational risk reservation, approximate liquidation, audit and reconciliation; it does not implement a substitute fill engine. No v2 API/live client is used. Original wheel/source are unmodified. Linuxx86_64/Python3.12 is the qualified worker platform; other platforms fail closed.

PAPER-v1: independent BTC-PAPER/ETH-PAPER virtual USDT linear products; initial10,000 virtualUSDT once per new ID; netting with one entry per instrument until flat; isolated app allocation; leverage1..20x chosen by the designer; BTCtick0.1/lot0.001, ETHtick0.01/lot0.01; taker fee0.04%; adverse1tick slippage; MMR0.5%; notionalcap100,000. Decimal strings and explicit unit/tick checks; approximate mark-based maintenance/liquidation; gap losses and negative cash/debt are retained. These are versioned educational assumptions, not real exchange specifications. Tick-by-tick conditional execution avoids any invented optimistic OHLC ordering.

SQLite schema1 transactions/idempotency, hash-linked append-only event/order/fill/settlement audit and snapshots are reconciled by rebuilding the native cache from replay. Actual event/receipt timestamps and a disclosed synthetic native replay clock are distinct. Local events require source/sequence/time/freshness validation; no Binance spot substitution. A no-network seccomp worker is installed before native import, without parent proxy/TLS modification. Runtime SQLite state is ignored and not committed. No reset/delete/redeposit/live-mode endpoint exists.

## Regression and security evidence

| Check | Result | Evidence / scope |
|---|---|---|
| Actual native qualification | PASS | Three real native simulated fills; gross−20.02000000, fees4.79200000, cash9975.18800000; separate native OCO cash10145.12000000, sibling canceled. [Qualification JSON](qualification/native-market.json). |
| Native/store TDD | PASS | Initial native14/store15 RED; implemented native24/store17 GREEN, including actual native integration. Store order-audit and OS denial each had a reproduced RED before repair. |
| Full Python regression | PASS | **319** tests, [log](verification/integrated-python-green.txt). Hand long/short/fees/slippage, partial/rounding/reservation/equity invariants, adjacent liquidation boundary, negative balance, OCO/replace/cancel, same-event no double exit, stale/future/duplicate/reverse/invalid tick, idempotency/concurrency, SQL rollback, append-only/version/corruption/native replay and socket denial.29 upstream Pandas4Warning deprecations recorded; native library not patched. |
| Full frontend regression | PASS | **30** tests, [log](verification/final-frontend.txt). Offline fixtures are not native/real-market success evidence. Durable create/partial-close keys survive re-render; network/committed502/truncated JSON remain uncertain. Scenario restore and failed/mismatched lookup isolation are covered. |
| Static/type/build | PASS | [Ruff](verification/integrated-ruff-green.txt), [strict mypy21files](verification/integrated-mypy-green.txt), [ESLint](verification/final-eslint.txt), [TypeScript](verification/final-types.txt), [build](verification/final-build.txt). |
| Initial source CI41150363112 | FAIL | Python/Frontend/Secret SUCCESS; dependency review actual High GHSA-27vj-qcqg-25rc in fsspec2026.2.0. No allowlist, suppression or severity reduction. |
| Patched dependency/source CI41151437466 | PASS | All four jobs SUCCESS. fsspec2026.6.0 is an explicit uv override of Nautilus's strict upstream2026.2.0 metadata; wheel unmodified. Selected local native paths requalified; not a claim about every upstream adapter. |
| Review-fix CI41151674087 | PASS | Source528fae4695ace43ce87e48355de50949206d0cd8; all four jobs SUCCESS, actual log excerpts saved. A later user extension authorized security checkpoint4. |

API TDD first RED1→GREEN1 includes Host/Origin/JSON/size, native entry/audit/idempotency and exact no-live/reset path inventory. Frontend initial missing UI was RED at module collection, not four assertion failures. Subsequent meaningful REDs: uncertain-intent dismissal1; reload/create2; committed5021; restore-selection3; failed/mismatched account lookup2. The corresponding full30-test suite is GREEN. A FastAPI included-router inventory failure was repaired by checking the actual exact OpenAPI path/method set, without excluding a route or weakening the contract.

## Browser and restart evidence

**PASS**: stable-source Chromium PC1440x1000 (2026-10-31 09:43:13KST) and mobile390x844 (09:44:17KST),17real local API commands each. Initial funds→long→tick→native SL/TP set/replace/cancel→partial close→TP/OCO exit→short SL gap exit→short approximate liquidation→ETH protected position. Final account cash6421.24399960/fees18.69600040 in both identical synthetic scenarios. Layout widths1440/390 equal document widths; Korean labels/status present, keyboard focus visible, pageerrors0 and market requests0. [Desktop JSON](browser-20261031/desktop.json), [mobile JSON](browser-20261031/mobile.json), matching screenshots. These are native simulated results, not real returns.

**PASS**: [durable browser idempotency](browser-20261031/reload-idempotency.json): create was committed before a deliberately induced local response abort; closing/reopening the tab recovered the exact key/payload. Partial close was committed before a deliberately induced local502; reload retried the exact original key/payload. Five POST attempts produced only one funding record and two fills (entry plus one partial exit), position0.060. This is local fault injection, not an observed exchange/proxy failure.

**PASS**: [actual process restart](browser-20261031/process-restart.json), 2026-10-31 09:46:19KST: backend PID18265 stopped and PID23233 started with the same SQLite DB and unchanged host preblocks. Preserved pre-restart browser snapshots exactly equal post-restart native state/audit for both scenarios, including cash/positions/SL/TP. New ETH3150.00 ticks trigger each restored native TP exactly once; funding remains1, no extra native fill on restoration. Both post-restart screens captured. Initial harness readiness checked an invalid trailing-slash local path and reported failure despite successful server startup; corrected health-path verification and preserved pre-restart snapshots established the result. No second restart or lost/reseeded ledger was substituted.

Development hot reload and exact select-name harness failures are failed attempts, not acceptance. The stable results above replace those attempts. All screenshot data is synthetic. Patched security-toolchain rechecks appear below.

## Boundaries

- BLOCKED: six PHASE2A regional-restriction markets; configuration preblocks both hosts. No retry/alternate host/region/proxy/TLS bypass.
- UNRUN/unsupported: strategy backtesting, AI, live/Demo/account/order integration, funding, limit entry, discretionary pending/expiry and liquidity-based partial fills. Native protection replacement/cancel and deterministic full simulated fills are the supported scope.
- Basic browser keyboard/labels/focus checks do not establish complete accessibility compliance. No deployment is authorized: anonymous PAPER mutation must stay on loopback in a trusted local environment. Browser recovery assumes preserved browser storage; do not clear uncertain intent or DB as a shortcut.

## Whole-tree npm audit repair (user-authorized fourth CI)

[Historical audit](verification/existing-frontend-audit.json), collected2026-10-31 09:46:05KST: **14 vulnerable dev-tool package entries**, grouped12Moderate/1High/1Critical (not14unique advisories). Direct: Vite7.2.2 High, Vitest4.0.14 Critical, @eslint/js/eslint/typescript-eslint Moderate. Nine transitive Moderate entries: @eslint-community/eslint-utils, @eslint/config-array, @typescript-eslint/eslint-plugin/parser/type-utils/typescript-estree/utils, @vitest/mocker and minimatch. All affected lockfile paths are dev dependencies; production React/chart dependencies were not identified by this audit. They predated PHASE3; original package manifests/lock were unchanged from ee49820a. That fact did not make them acceptable to leave after the user explicitly authorized repair.

The audit response was delayed but the original JSON completed. A later bounded diagnostic was interrupted and is not used as evidence. Registry advisory counts are time-scoped, not exploit/reachability proof or a whole-ecosystem guarantee. Critical GHSA-5xrq-8626-4rwp concerns a listening Vitest UI server; this task ran `vitest --run`, but the vulnerable version was still replaced. High Vite advisories include filesystem-deny bypass/file-read paths. No exploitation is claimed.

`npm audit fix --dry-run` retained14 findings and suggested incompatible major downgrades. No `--force`, ignore or security-policy reduction was applied. Minimal pins: **Vite7.3.7**, **Vitest4.1.12** within existing majors. The shared Moderate minimatch advisory has fixed version11.0.1; latest ESLint/TS-ESLint still request10.x. A version-scoped **minimatch@^10.2.2→11.0.1 override** explicitly crosses that transitive major contract; retained named CJS/ESM APIs and Node24 compatibility were inspected. Actual ESLint negative-control detection, all30frontend tests, lint/types/build qualify this application's toolchain; all upstream configurations are not claimed compatible.

[Resolved npm audit](verification/npm-audit-after.json): **0 total,0Moderate,0High,0Critical**. [Actual dependency tree](verification/patched-tree.json), [ESLint negative control](verification/eslint-negative-control.json), [patched tests](verification/security-frontend.txt), [lint](verification/security-eslint.txt), [types](verification/security-types.txt), [build](verification/security-build.txt). Native sources remain unchanged; original restart PASS evidence is preserved. Fourth source `edf89138ee2c58446db5d922726f368f6137aa2b`; [CI41152341568](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41152341568) and [patched-toolchain browser evidence](security-toolchain-20261031/) are finalized below.

### Patched-toolchain browser result

**PASS** PC1440x1000 at2026-10-31 09:50:07KST and mobile390x844 at09:50:30KST. New independent virtual scenarios exercised initial funds→long→61000tick→partial/full close→short→60000tick→full close using real local API/native fills under patched Vite7.3.7. Five fills each, no open position. Hand check: each direction gross99.98 minus4.84fees; totalnet190.28, wallet**10190.28000000**, fees**9.68000000**. [Summary](security-toolchain-20261031/summary.json) and viewport-specific JSON/screenshots preserve actual simulated results. Mobile uses real touch taps for command buttons. Korean notices present, document widths equal1440/390, pageerrors0, market requests0. Original17-command workflows, lost-response recovery and actual process restart evidence are preserved, not overwritten by these checks.

Latest implementation source: `edf89138ee2c58446db5d922726f368f6137aa2b`. Documentation/evidence is committed separately with `[skip ci]`; use git history for its exact final SHA. No source change is part of that final evidence commit. Main remains7ddcded673ab9b0168fccf1aac106d02ff5a0b6d and PR1 remains Draft/unmerged.

### Final security-source CI result

**PASS** [CI41152341568](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41152341568), source `edf89138ee2c58446db5d922726f368f6137aa2b`, completed2026-10-31 09:52KST. [Job/step JSON](verification/ci4.json), [actual log excerpts](verification/ci4-observed-log-excerpts.txt).

| Job | Result | Actual evidence |
|---|---|---|
| Python checks | SUCCESS |319tests;29recorded native upstream deprecation warnings; Ruff PASS; strict mypy21files PASS. |
| Frontend checks | SUCCESS |Locked npm install found0vulnerabilities;30tests; ESLint/TypeScript/build PASS. |
| Secret scan | SUCCESS |Gitleaks `no leaks found`; verified recovery artifact produced by existing unchanged workflow. |
| Dependency review | SUCCESS |No changed dependency with severityModerate or higher detected; policy unchanged. Independent whole-tree npm audit also0. |

Four source CIs total: initial dependency failure was repaired, checkpoint2 PASS, real selection-review fix checkpoint3 PASS, explicitly user-authorized security remediation checkpoint4 PASS. Documentation/evidence gets `[skip ci]`; no unchanged successful CI is rerun. No main change, merge, force push or deployment occurred.

### Additional final-document scan distinction

**UNRUN**: additional local Gitleaks scan of the final documentation. Limited searches of preserved `work/` and `/tmp` found no retained Gitleaks binary. The attempted new checksum-pinned binary download was rejected by automatic approval review; no download, alternate route, retry or new permission request followed. A limited common-credential-pattern check found0matches but is not a Gitleaks substitute. **PASS** remains the independently observed final-source CI Secret scan job124391476537 (Gitleaks `no leaks found`). These are separate scopes. Documentation contains only synthetic PAPER state/evidence; original raw sensitive exchange responses/runtime databases are not added.

PR1 title/body update completed through the official GitHub connector after the old CLI's unrelated Projects-classic GraphQL metadata error; Draft/open/main/unmerged boundaries were preserved.

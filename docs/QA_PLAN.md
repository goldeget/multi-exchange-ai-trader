# QA plan

**Current executed scope:** [Phase4 report](qa/phase4/README.md) distinguishes native/local functional PASS from real-futures BLOCKED and shared-portfolio UNRUN. Earlier future-phase wording below is retained as historical planning context.


This document defines planned quality gates. It does not assert that an application, test suite, CI workflow, or any check described below has been implemented or run.

## Safety and evidence policy

- Development and verification must work without exchange credentials. Use public market data and synthetic or publicly redistributable fixtures only. Record fixture provenance and applicable license or redistribution restrictions.
- Never place live orders, transfer assets, or call authenticated production trading or account APIs during QA. PAPER means locally simulated balances, orders, and fills; it is not permission to execute trades on an exchange.
- CI must use local fixtures and stubs. It must not call external production APIs, including public market data endpoints. Any optional manual public connectivity check must be read only, explicitly reported, and excluded from CI.
- Do not store keys, tokens, private account exports, personal data, or secrets in fixtures, logs, screenshots, or artifacts. Redact sensitive values in reports.
- Mark each reported check as **PASS**, **FAIL**, **SKIPPED**, or **UNRUN**. PASS requires observed command output or other recorded evidence. SKIPPED requires a reason; UNRUN means no execution evidence exists. A planned check is UNRUN, not PASS.
- Report the command or procedure, scope, relevant revision, environment, outcome, and evidence or artifact location. Distinguish fixture validation from manual public connectivity validation. Do not infer exchange availability from a successful build or an allowlist entry.

## Phase 2: first public market data dashboard

Scope: display public BTC and ETH spot and perpetual tickers and candles from supported exchanges, with clear exchange, instrument, market type, timestamp, loading, error, and stale data states. This phase does not require order execution, backtesting, or AI behavior.

Planned gates:

- Market adapter contract checks using local public or synthetic fixtures: request construction, instrument mapping, spot versus perpetual classification, response parsing, timestamps and units, decimal precision, candle ordering, duplicate handling, and incomplete candle identification.
- Adapter failure cases: empty or malformed payloads, unsupported instruments, timeouts, disconnections, rate limits, retry timing, and exchange error responses. Ensure stale cached values retain their original timestamps and are visibly identified.
- Polling behavior: bounded request frequency, backoff, cancellation, cache reuse, and prevention of overlapping polling loops or duplicate requests. Use fake clocks and stub transports.
- Dashboard behavior: loading and partial exchange failures, stale or missing data, correct symbol and market labels, candle rendering, responsive layout, keyboard access, and readable error messages.
- Frontend typecheck and production build using the repository's eventual documented commands. Record actual command outcomes after the frontend exists.
- Optional manual public HTTPS checks after approved environment configuration is applied: fetch ticker and candle samples without credentials, confirm schema assumptions and timestamps, and report provider or network failures honestly. Do not make these calls a CI dependency.
- CI gates when configured: deterministic fixture checks, frontend typecheck/build, secret scanning, dependency vulnerability review, and dependency/fixture license checks. Pin dependencies through the project's lockfile and document any accepted findings and their reasons.

Exit evidence must identify which adapters and instruments were covered and which checks remain SKIPPED or UNRUN. A working dashboard alone does not establish correctness of any later trading capability.

## Phase 3: local PAPER execution and accounting

Apply these gates only when local execution features are introduced. All scenarios use synthetic markets and balances; no exchange credentials or live orders are involved.

- Unit checks for order validation and lifecycle: submission, rejection, cancellation, replacement, expiration, partial fills, final fills, and invalid state transitions.
- Fill modeling checks for spread, fees, slippage, available liquidity, repeated events, out of order events, and deterministic behavior. Document assumptions and avoid claiming simulated fills represent actual execution quality.
- Risk checks for position and notional limits, available funds, leverage or margin constraints where supported, stale or missing prices, concurrent submissions, and configured stop conditions. Rejected orders must not change balances or positions.
- Accounting checks for cash, reserved funds, inventory, cost basis, realized and unrealized P&L, fees, and funding where modeled. Reconcile totals across partial fills, cancellations, and multi-asset scenarios.
- Precision checks for tick size, lot size, minimum quantity/notional, rounding direction, and decimal-safe arithmetic. Cover boundary values and ensure invalid orders cannot pass after rounding.
- Integration checks that join order, fill, risk, accounting, event persistence, and UI behavior. Include duplicate delivery and interrupted processing without double counting.
- Restore checks for persisted open orders, balances, positions, settings, and event history after restart. Cover interrupted writes, incompatible schema versions, corrupt or missing state, and reconciliation between snapshots and events. Restore must never trigger real network execution.

## Phase 4: backtesting

- Use versioned public or synthetic historical fixtures with documented provenance, timestamps, gaps, adjustments, and redistribution permissions.
- Verify deterministic replay, event ordering, interval boundaries, warm-up behavior, missing data policy, fee/funding/slippage assumptions, and consistency with the PAPER accounting engine.
- Check for look-ahead bias, future data leakage, and accidental reuse of evaluation data during strategy selection. Validate metrics against small manually calculable examples and report assumptions alongside results.
- Add integration scenarios for multi-instrument replay and restore/resume. Report performance measurements separately from correctness and avoid implying historical returns predict future performance.

## Phase 5: AI assistance

- Use synthetic prompts, local model stubs, and approved public evaluation fixtures. CI must not require model service credentials or contact production model APIs.
- Verify output schema validation, malformed or unavailable responses, timeouts, reproducibility where applicable, and clear separation of observations, assumptions, and generated suggestions.
- Treat model output and external text as untrusted input. Check that embedded instructions cannot bypass risk policy, expose secrets, invoke authenticated exchange operations, or convert a suggestion into a live order.
- Evaluate recommendations with held-out historical scenarios and explicit baselines. Record model/configuration versions, evaluation limits, and failures; do not use fluent explanations as correctness evidence.
- Any simulated strategy action must pass the same order, risk, precision, and accounting gates as other PAPER actions. AI output must never authorize live execution.

## Reporting and CI progression

Introduce checks with the phase that owns their behavior. Keep future-phase items visible as UNRUN until that functionality and its verification exist. Do not block a public dashboard milestone on nonexistent execution, backtest, or AI suites, and do not describe those suites as covered by dashboard checks.

For every milestone, publish a compact results table with: check, phase/scope, command or procedure, revision/environment, status, evidence, and reason for any SKIPPED or UNRUN result. Include outstanding failures and accepted dependency or license findings. CI reports must preserve failed check outcomes and must not convert unavailable tools, missing suites, or disabled network checks into passes.

## Related delivery documents

See the [architecture and phase map](ARCHITECTURE.md#phase-map-and-gates), [research audit](OPEN_SOURCE_AUDIT.md), [exchange capabilities](EXCHANGE_CAPABILITIES.md), [AI design](AI_DESIGN.md), [design specification](superpowers/specs/2026-10-31-multi-exchange-trader-design.md), [implementation plan](superpowers/plans/2026-10-31-public-market-dashboard.md), and [work log](WORK_LOG.md). Current delivery is Phase 1 research/docs and Phase 2 public dashboard. Phase 3 PAPER, Phase 4 backtesting, Phase 5 AI, Phase 6 Demo, Phase 7 approval, Phase 8 AUTO, Phase 9 QA/security expansion and Phase 10 live readiness are future phases; none is enabled by this document. QA and security checks for the public dashboard begin in Phase 2 rather than waiting for Phase 9.

## PHASE3 executed-scope supplement — 2026-10-31 KST

The original plan above is not blanket acceptance. [Phase3 results](qa/phase3/README.md) distinguish actual local native integration, offline UI/HTTP fixtures, real local browser/procedure evidence and unimplemented scope. The current locked configuration runs Nautilus1.231.0 with the documented fsspec security override; import alone is not engine qualification.

Required observed gates: hand-verifiable long/short fees/PnL/slippage; partial-close/reservation invariants; native SL/TP OCO and gap/liquidation/negative-balance behavior; SQLite idempotency/concurrency/rollback/append-only/corruption/replay; OS outbound denial; local API Host/Origin/JSON and no-live/reset inventory; persisted uncertain command recovery after network/5xx/invalid-JSON and scenario-load isolation; PC1440x1000/mobile390x844 PAPER workflow; actual backend process restart with same DB, native cash/positions/protection and seed reconciliation. Browser keyboard/labels/focus checks are basic checks, not complete accessibility certification.

Pending/expiration/liquidity partial-fill plans remain UNRUN/unsupported in this implementation. No PAPER result establishes real-market connectivity, returns or full PHASE2A completion. Six previously region-blocked markets stay BLOCKED, and CI makes no external market calls.

## Current PHASE4 acceptance supplement — 2026-10-31 KST

Earlier phase descriptions are historical plans. [Executed PHASE4 gates](qa/phase4/README.md) cover validated immutable actual SPOT snapshots versus synthetic perpetual fixtures; actual native replay/accounting, no future-prefix signal leakage, next-open execution/cost/path assumptions, chronological OOS/walk-forward, reproducibility, net return/drawdown/Wilson uncertainty/net loss streak; risk-bounded synthetic automation, cursor/idempotency/concurrency/rollback/corruption/eventreserve/restartPAUSED recovery; local API boundaries and Korean desktop/mobile native workflows. Actual full regression368Python and final45frontend plus static/build are recorded with CI evidence.

Original multi-instrument replay gate: BTC/ETH independent account research supported, **shared portfolio native replay UNRUN**. No combined portfolio return. Real futures data/performance BLOCKED;30day720bar actual spot is short functional evidence. Physical-device/full accessibility and Phase4 pinch revalidation UNRUN. Browser response-loss tests are induced localabort/502, not exchange failures. All source/history failures and exact historical false-positive handling are retained; final evidence head runs normal CI including full-history Secret scan.

# Backend implementation evidence

This report covers backend agent files only. Research V2 is owned and reported by the parent implementer. Native execution is not qualified by Windows-only checks.

## Delivered contracts

- `GET /api/v1/capabilities`: separates real-account read registration, synthetic PAPER contract support, runtime prerequisites, offline RULE analysis and optional research V2. `live_order_enabled` is always false. Spot market/limit remain disabled until native qualification. Research V2 is enabled only by the parent's `app.state.research_v2_supported` integration flag.
- `GET /api/v1/overview`: verified existing PAPER states remain independent `ledgers`; research runs remain separate. No aggregate equity or new funding ledger. One failed manual ledger does not remove healthy ledgers/research results. No exchange/account reads are triggered by overview.
- `GET /api/v1/diagnostics`: server liveness, scheduler task/progress state, readable ledgers, unresolved review confirmations and safe error codes. Store failures block new automation in the read model; liveness is never order authority.
- `GET /api/v1/connections/status`: registration and unverified status only; no credentials, provider balances or claim of verified permissions.
- `POST /api/v1/paper/order-previews`: exact body `{scenario_id,product:"SYNTHETIC_PERPETUAL",order_type:"MARKET",config_revision:"PAPER-v1",command:<V1 enter/close/protect>}`. Runs existing native replay without ledger writes. Returns token/hash/revision, Decimal cost strings, exact server fee rate and 60-second expiry.
- `POST /api/v1/paper/order-confirmations`: exact body `{preview_id,idempotency_key}`. Durable token/key binding survives response loss; stable server command ID is `review-<preview_id>`. The original `PaperStore.command` applies native execution inside its existing SQLite transaction. Revision/expiry checks occur after prior-result recovery and before first application. New spot/LIMIT contracts are rejected server-side. Existing V1 `/commands` and its idempotency keys remain compatible.
- `GET /api/v1/recommendations` and `GET /api/v1/recommendations/{id}`: normalized RULE views plus existing Korean card, stable ID, snapshot, reasons, expiry, review state and server time. Reads expire pending cards without fresh market calls. Existing preview/approve/reject replies are retained.
- `GET/PUT /api/v1/settings/ai`: persisted revision-checked internal budget configuration; only `OFFLINE_TEST` is accepted. External modes fail closed because no provider is configured. The request accepts no secret keys.
- `GET /api/v1/ai/budget`: USD Decimal-string used/reserved/uncertain/remaining internal program limit, distinct from null provider balance. Transactional reservation, settlement and evidence lookup are durable, concurrent-safe and idempotent. Unknown usage keeps its reservation. No external AI calls are implemented or made.

All new sensitive routes reuse the existing loopback Host, mutation Origin, JSON and request-size boundaries. AI/review sidecar failures are isolated from original ledger reads and scheduler startup.

## Spot qualification extension (implemented, Linux CI pending)

`paper/spot_native.py` uses Nautilus 1.231.0 `CurrencyPair`, `AccountType.CASH`, a multi-currency account, no borrowing, native market/limit orders, native cancellation, native balances and native commission currency. It does not implement an alternate matching simulator. `spot_worker.py` uses the existing `deny_network()` seccomp boundary before native imports; no Windows fallback was added. The deterministic versioned policy is `SPOT_L1_SPREAD_FULL_LIQUIDITY_V2`: bounded orders fit within synthetic quote liquidity, bid is mark minus one tick, ask is mark plus one tick, and the native fill model adds no further slippage. Native limit orders can rest and later fill on crossed quotes.

`SpotPaperStore` shares immutable journal/reconciliation machinery with `PaperStore`, with a separate metadata identity, explicit SPOT product and separate `PAPER_SPOT_DB_PATH` (default `spot-paper.sqlite3` adjacent to the original PAPER DB). V1 refuses to open that product's database. One seed per separate spot scenario is synthetic independent funding, never summed with perpetual/research funds. Account/holdings equity counts base assets once. Native CASH notional locks are preserved; an additional deterministic fee reserve prevents limit orders from overcommitting quote fees. Sellers cannot exceed native free inventory. Event admission preserves enough final ledger slots to cancel each resting order. Native fills and balances are reconciled before journal commit.

- `GET /api/v1/paper/spot/scenarios`, `/{id}`, `/{id}/audit`: explicit separate product reads.
- `POST /api/v1/paper/spot/scenarios/{id}/commands`: existing envelope, allows only `create`, synthetic `tick` (`source=LOCAL_SPOT_SCENARIO_V2`) or `cancel` with a native order ID. Direct order placement is rejected. While not qualified, funding/ticks/new orders are blocked; cancellation remains available for an existing ledger.
- The shared preview endpoint accepts `product=SPOT`, `config_revision=SPOT-PAPER-v2`, `order_type=MARKET|LIMIT`, and command `{kind:order,instrument:BTC-PAPER|ETH-PAPER,side:buy|sell,quantity:<Decimal string>,order_type,...}` with `limit_price` only for LIMIT. Extra leverage/short/float/precision violations are rejected. Limit preview distinguishes estimated maximum fee from fee actually charged in the replay, and returns native `order_state_after` plus `balances_after`.
- `spot_contract.QUALIFIED` is currently **false**. Parent must only enable it after actual Linux tests pass, then verify the resulting final SHA. Capability false is enforced server-side as well as in the UI. Already committed confirmation receipts remain recoverable after qualification is withdrawn.

Version-specific API/fee/hold behavior was checked against official tagged source: [CurrencyPair](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/model/instruments/currency_pair.pyx), [CashAccount](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/accounting/accounts/cash.pyx), [BacktestEngine](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/backtest/engine.pyx). Source review does not substitute for native execution qualification.

## Latest-main DEMO integration and no-repeat guard

The parent merged latest main `b626501`, which already contains a credential-gated fixed Bybit DEMO connectivity round trip on recommendation approval. This existing sender behavior is preserved; this agent has not invoked it. Analysis source remains RULE. Local recommendation execution remains its separate PAPER ledger; optional DEMO effects are explicitly exposed in `approval_effects`, `approval_state` and `approval_blocked`. Short recommendations explicitly describe the separate fixed Buy-then-reduce-only-Sell test as not following recommendation direction. Capabilities and connections describe DEMO sender registration independently from LIVE order authority and permission verification.

`RecommendationBook.claim_practice` commits an atomic `BEGIN IMMEDIATE` marker before any optional external attempt. An uncertain or close-failed attempt anywhere in that same book blocks new attempts on every other card, including concurrent independent connections and process restart. Historical submission acknowledgement is checked first and is not repeated. Timeout, cancellation, unknown post-claim errors and expired/rejected cards cannot clear an uncertainty marker. There is no automatic reopen/close/retry or uncertainty-reset endpoint. A preflight failure after claiming may conservatively require reconciliation; it is not assumed safe to resend.

Acknowledgement wording now states that request submission was acknowledged and that actual fills/position closure were not verified. `demo_round_trip` is retained as the historical receipt field; it is not presented as verified flattening. Diagnostics count all uncertain/close-failed records, including old cards outside the bounded recent-card list. The guard covers one configured recommendation book; independently configured applications sharing an exchange account remain outside this program's coordination boundary.

## Storage compatibility

Existing PAPER metadata, schema version, event hashes, immutable ledger tables and replay assumptions are unchanged. `PaperStore.command` only gains optional review checks. Review and AI data use separate SQLite sidecars (defaults adjacent to `PAPER_DB_PATH`, override with `PAPER_REVIEW_DB_PATH`/`AI_DB_PATH`). No existing database has been reset or migrated. Provider/AI mode defaults are offline with zero configured internal limit.

`paper.native.instruments(*,fee=FEE)` is the parent-requested backward-compatible hook for research V2 instrument costs; default V1 execution fees and global assumptions are unchanged.

## Checks performed so far

- Dependency-free unittest control checks: **6 passed**. Covers registration/authority, runtime prerequisites, transactional concurrent budget reservations, restart/uncertain settlement, idempotent settlement receipt, unsupported product rejection, and durable review response-loss recovery. The review recovery check uses an explicitly labeled test-only ledger double; it qualifies the sidecar, not native execution.
- Dependency-free overview isolation checks: **2 passed** with test-only read services; no shared-asset aggregation, explicit same-name spot/perpetual identity and no raw error leakage.
- Python `compileall`: passed for source and added test modules.
- Added native `tests/paper/test_order_preview.py`: actual existing ledger/native replay tests for no-effect review, concurrent confirmation, immutable retry after restart/expiry, changed revision, expiry and V1 spot-command bypass rejection. Requires qualified Linux native runtime.
- Latest local executed pure/ASGI/spot-contract group: **26 passed** (3.37s). This includes configuration revision and unsupported-provider guards, worker/store partial-failure isolation, local Host/Origin protection and unqualified spot HTTP rejection.
- Latest recommendation safe API subset: **14 passed**, **2 native approval tests deselected** (10.38s). Includes persisted DEMO claim before a fake timeout, no repeat after expiry/restart, cancellation, prior acknowledgement recovery, concurrent independent claims, global cross-card blocking, explicit short-rule/fixed-Buy metadata and unsupported target refusal. These use controlled test-only practice objects and do not contact Bybit.
- Ruff: all backend-owned source and test files pass.
- Mypy for API/control/read/AI/preview/store modules: **11 files passed** after spot integration; latest DEMO service/book/execution/control/capability changes: **5 files passed**. Local native-module type checking reports missing Nautilus imports because the locked native package is not installed on this Windows test environment; those imports must be checked on Linux.
- Added `test_spot_native.py` (actual CASH market inventory, native quote fee, partial sale, restart, oversell, resting holds/cancel/crossed fill/double-spend, V1 metadata guard) and `test_spot_review_api.py` (actual native preview/confirm/resting-fee semantics, no direct placement bypass, receipt recovery after expiry/dequalification, cancellation). These are pending Linux CI.
- Parent-reported first Linux run `41266615640`: **478 passed, 2 failed**. One failure was route inventory, handled by parent. The spot failure proved that `FillModel(prob_slippage=1)` adds an adverse tick even to a crossed limit fill (59000.1 for a 59000 buy limit). The current explicit bid/ask spread plus zero extra native slippage patch corrects that policy; a sell-limit price-bound/actual-fee assertion was added. This correction still requires a fresh Linux run; spot qualification remains false.

## Remaining integration/qualification

Parent must run the full locked Python checks and Linux native tests before claiming engine qualification. The current Windows host fails the existing Linux x86_64 seccomp prerequisite; no bypass/fallback was added. Capabilities expose this runtime constraint separately from engine contract support.

Spot PAPER matching/reservation/cancellation code is implemented and remains disabled pending full native qualification. External provider credentials, actual provider permissions/balance/prices and paid calls are unconfigured and unverified. UI must preserve the same preview token and idempotency key across uncertain confirmation/reload and must match recommendation responses by card ID/selection generation.

No exchange orders, account API calls, paid model calls, deployment, commits or dependency-lock changes were performed by this backend agent.
# Coordinator qualification update

The pending flags described below were superseded after CI49:489 tests passed, including all native spot and extended research tests. The remaining source-boundary failure (network-library import in recommendation domain) was removed without weakening the test; timeout/cancellation and source-boundary checks pass. SPOT and research V2 contract flags are now enabled, with native runtime availability separately false on Windows. Final full CI is still required.

Coordinator also added an atomic status/expiry recheck inside the durable DEMO claim to prevent another process rejecting or expiring a card while market revalidation awaits. Two regressions failed before the fix and pass afterward; prior acknowledged/uncertain receipt recovery remains first. Tests use explicit clocks for durable-claim races.


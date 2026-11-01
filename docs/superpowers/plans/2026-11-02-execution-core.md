# Automatic execution core implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Build a tested, durable, network-free execution core for the real automatic trading lifecycle, without falsely enabling an unqualified venue adapter.

**Architecture:** Both automatic modes share one account execution coordinator. Durable commands precede all effects, verified owned fills drive protection, and deterministic risk gates consume explicit user policy and current venue evidence. The existing PAPER engine and public API remain unchanged until a real native adapter passes qualification.

**Tech Stack:** Python 3.12, Decimal, SQLite WAL/FULL, pytest, Ruff, mypy. No new dependencies.

**Spec:** `docs/ROADMAP_FINAL_PRODUCT.md` sections 4.1/4.1.1 and user request of 2026-11-02: both automatic modes must manage real entries, fills, TP/SL and exits. `docs/implementation/AUTOMATIC_EXECUTION_CORE_KO.md` records this patch's implemented scope and outstanding qualification. Private research evidence is a controller artifact, not a dependency of this repository.

## Global constraints

- Single operational order path: NautilusTrader 1.231.0. No HTTPX/CCXT/pybit order fallback. No authenticated requests, orders, paid AI calls, or new servers during core development.
- LIVE remains unsupported in existing capability/API; a fake gateway is a test tool, never production fallback. Explicit DEMO/LIVE scope has no implicit MAINNET default.
- Existing manual/external positions remain external/unmanaged. Never auto-adopt, change their leverage, cancel their orders, or close/hedge them.
- User sets leverage min/max, loss limits, sizing rule, margin/position mode and liquidation buffer; no hidden numeric defaults, arbitrary leverage cap, or AI authority over these values.
- ACK is not fill, cancel ACK is not no-fill, and saved RUNNING is not operational health. Timeouts/ambiguous effects remain UNKNOWN; no new-ID retry.
- All exits/protection are reduce-only and bounded by confirmed owned quantity. Missing/contradictory evidence blocks new entries.
- Work only in `execution-core`, branch `gpt/execution-core-20261102`. Each agent owns named files. No agent commits/stages shared work; controller commits after review.
- Money/quantity use finite Decimal; UTC instants must be timezone-aware. Existing test/native sandbox boundaries remain intact.
- Decimal technical representation bounds are at most 256 coefficient digits and exponent magnitude 1000; these prevent unbounded precision allocation, not a new leverage or trading limit.
- Mode distinction is awaiting user clarification; implementation here is common to both and grants no expanded AI authority.

## Review focus

1. Crash after send but before ACK: never redispatch automatically; persisted intent is reconciled by the same client ID.
2. A late or duplicate execution after cancel: apply once, protect actual exposure, never discard because order looked terminal.
3. Wrong account/environment/order mapping or external net-position conflict: no ownership inference from ID prefix.
4. NaN/infinity, bool-as-number, stale/future data, missing policy, lot rounding and competing reservations: reject rather than silently repairing unsafe input.
5. Protection report covers the wrong side, quantity, trigger or generation: do not display protected or cancel the prior generation.

### Task 1: Durable scoped command journal

**Own files:** `src/multi_exchange_trader/execution/__init__.py`, `journal.py`, `tests/execution/test_journal.py`.

**Produces:** `ExecutionScope(account_id: str, environment: str)` accepts only explicit DEMO/LIVE; `CommandJournal(path: Path, scope: ExecutionScope)` with durable command registration, claim-before-send, acknowledge, mark-unknown, reject, pending recovery and read methods. Expose clear frozen command records and exceptions. Network-free.

- [ ] Write failing tests for identical command replay, conflicting payload, scope separation, deterministic client ID <=36 chars, concurrent claims from two SQLite connections, SENDING crash recovery to UNKNOWN, no redispatch of UNKNOWN, failed commit means no claim, append-only audit, atomic risk reservation of two competing entries.
- [ ] Implement canonical JSON hashing (reject non-finite numbers), explicit command kinds, command states PENDING/SENDING/ACKNOWLEDGED/UNKNOWN/REJECTED. Duplicate command registration returns the original record only for identical immutable payload. Claim transitions and commits before returning. SQLite BEGIN IMMEDIATE and unique constraints enforce race safety. Constructor/recover marks prior SENDING UNKNOWN; never converts it back to PENDING. Unknown never releases reservation.
- [ ] Reservations accept explicit nonnegative Decimal `risk_amount`, `notional_amount`, `margin_amount` and explicit finite limits/counters from caller; enforce totals in one transaction. Only confirmed terminal commands may release through an explicit method; no automatic release on timeout or ACK. Document that this is journal accounting, not full account risk authority.
- [ ] Add event inbox/projection CAS API only if cleanly testable: same event ID plus same payload idempotent, changed payload conflict; projection and event commit atomically with expected revision. Do not perform network callbacks inside transactions. Keep implementation focused; document unfinished integration honestly.
- [ ] Run focused pytest, Ruff, strict mypy. Report exact API, tests and limits to controller; no commit.

### Task 2: Owned fill and protection projection

**Own files:** `src/multi_exchange_trader/execution/protection.py`, `tests/execution/test_protection.py`.

**Produces:** Frozen projection/event/evidence types plus pure reducer/check functions (choose clear names and report API to controller). No dependence on journal/risk files; no network.

- [ ] Write failing tests: 3 partial entry fills, duplicate fill, conflicting duplicate execution ID, late fill after cancel, exits exceeding owned exposure, SL/TP racing, wrong scope/order/side, external position conflict, partial TP then stop, stale/unknown/rejected protection, exact generation checks, and flat-but-orders-unverified distinct from stopped-flat.
- [ ] Implement immutable program-order mapping rather than prefix ownership. Track execution IDs and signed entry/exit quantities; entries may only follow registered own entry IDs and cannot exceed intended quantity. Reduce-only exits may not flip or exceed owned quantity. A conflicting report marks attention/blocks entries and preserves evidence, never silently clamps/forgets fill. If representing rejection as exception, preserve the original state for the caller to journal conflict.
- [ ] Verify protection from explicit venue evidence: scope, owned position identity, side, reduce-only, stop/take-profit role, trigger value/source, active state, exact quantity, policy revision and generation. No timestamps guessed; missing/stale/future evidence cannot mark OPEN_PROTECTED. Partial coverage and pending replacement never cancel the old protection. Return proposed owned-order actions only, never broad cancel/flatten.
- [ ] Protection absence/failure blocks new entries; expose unprotected quantity and truthful state. Stop-flat requires independent same-cycle flat plus absence of all own working/conditional orders, not local fill math alone.
- [ ] Run focused pytest, Ruff, strict mypy. Report exact API/tests/limits; no commit.

### Task 3: Deterministic entry risk assessment

**Own files:** `src/multi_exchange_trader/execution/risk.py`, `tests/execution/test_risk.py`.

**Produces:** Frozen explicit policy/market/instrument/account/reservation data and pure `assess_entry(...)` returning decision/evidence or typed rejection. No dependence on journal/protection. No network or leverage mutation.

- [ ] Write failing tests: every missing mandatory policy/input; non-finite Decimal; stale/future market/instrument/account/risk-tier; wrong mode; external same-symbol order/position; leverage user/venue intersection; rounding down; below min qty/notional; fees/funding/slippage costs; loss/exposure/margin/position-count and reserved amounts; SL versus liquidation; long/short symmetry; leverage applied readback missing/mismatch; no arbitrary built-in cap.
- [ ] Implement loss-fixed sizing from explicit user per-trade loss and stop distance plus explicit per-unit fees/funding/slippage allowance. Use runtime quantity step/tick/minimums and size-dependent verified risk-tier ranges. Quantity rounds DOWN only. Required leverage derives from explicit allocation/available margin, intersected with user and venue ranges and leverage step; reject unsatisfied constraints. Never round above loss budget to meet minimums.
- [ ] Require fresh, full, scoped account/instrument/tier evidence and explicit user max quote age; modes must match. All account aggregate exposures include pending reservations. Existing external same-symbol activity blocks assessment. Missing actual applied leverage/readback fails final submission assessment (a planning-only result may explicitly say not submission-ready).
- [ ] Liquidation safety must consume qualified explicit liquidation evidence tied to proposed quantity/leverage/mode, or reject as UNVERIFIED_LIQUIDATION. Do not invent an approximate Bybit liquidation formula and call it verified. Clearly separate planning from final submission-ready decision and list real adapter obligations.
- [ ] Daily loss/drawdown/consecutive loss limits are explicit inputs with defined UTC loss accounting supplied by a qualified ledger; no hidden defaults or fabricated account state.
- [ ] Run focused pytest, Ruff, strict mypy. Report exact API/tests/limits; no commit.

### Task 4: Shared coordinator integration and review

**Own files:** controller delegates `execution/coordinator.py`, `execution/codec.py`, their tests and `docs/implementation/AUTOMATIC_EXECUTION_CORE_KO.md` after T1-3 interfaces are stable. Controller implements `execution/ownership.py` and `tests/execution/test_ownership.py` to provide the OS account-worker lock.

- [ ] Define a gateway Protocol with explicit capability/environment qualification and ID-scoped submit/query/cancel. No broad cancel. Production adapter absent means entry refused.
- [ ] Real process lock, exact account/environment, stable local private lock path, acquisition epoch, process crash release and fail-closed release/reacquire tests. This is same-host arbitration, not cross-host fencing. Offline coordinator has TEST-only states; no production activation method exists.
- [ ] Use journal claim-before-send and preserve UNKNOWN on every ambiguous effect/crash. No broker calls under an open transaction. Reconcile unknown commands by the same IDs; absence once does not authorize resend. Boot enters recovery; explicit activation after successful full reconciliation required. Verify policy and current risk before dispatch, not merely at queue time.
- [ ] Integrate projection storage atomically if T1 CAS API is present; otherwise do not claim durable full fill recovery and keep coordinator disabled for production.
- [ ] Run injected fake gateway tests including timeout after accepted submit, process restart, duplicate events, stale policy/market/private state, no entry without verified protection capability, external conflict and protection continuation during entry pause.
- [ ] Independent task/whole-branch review, fix actual findings, full applicable checks. Merge only reviewed exact-head CI passing patch using already-given user merge authorization. Do not label native integration, real DEMO qualification or entire final program complete.

### Subsequent required native qualification (separate deliverable)

Implement and source/execute-test exact Nautilus Bybit linear gateway, ownership filtering, retries/reconciliation, attached protection confirmation and explicit leverage-setting/readback. Actual DEMO credentials are on another PC; real credential transfer and bounded venue tests remain prerequisites. Server remains absent and no new spending is authorized. Native qualification and actual user risk settings precede a real LIVE activation; core tests cannot substitute for them.

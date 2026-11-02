# OpenRouter HTTP Status Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the actual nullable numeric response status from headers through generation, durable dispatch, restart, local charge reconciliation and the existing safe result.

**Architecture:** Append one optional scalar to both existing frozen result dataclasses and one nullable SQLite column. Capture status before reading a response body; persist it in the same transactional result updates as failure/cost. Migrate known local v1 storage under a constructor writer lock without changing accounting or provider behavior.

**Tech Stack:** Python `>=3.12,<3.13`, existing locked httpx `>=0.28.1,<0.29`, stdlib dataclasses/sqlite3, pytest, Ruff and mypy. Keep all existing dependency pins/lockfile, including native `nautilus-trader==1.231.0`.

**Spec:** `docs/superpowers/specs/2026-11-03-openrouter-http-status-design.md`. Root seeded this plan from merged main `5c07a69d237bf7c724b8d0ec7fd66d094468f71c`; parent main CI116 is in progress, while parent exact reviewed CI115 passed all seven jobs. Actual operator profile use remains blocked until main CI116 passes.

## Global Constraints

- Parent profile PR25 is merged; root verified main `5c07a69d237bf7c724b8d0ec7fd66d094468f71c` and started a new branch in the existing linked isolated worktree. No applicable `AGENTS.md` was found. Do not create another worktree. Parent main CI116 and actual profile acceptance remain separate root gates.
- User delegated subagent implementation and independent review. Execute Task 1 and Task 2 sequentially with the chosen existing workflow; preserve their review gates and final whole-change review. Do not add approval stalls already opted out of by the user.
- Root serializes ALL Git mutations, commits and package operations. Implementation/review workers never Git-mutate; task reviews use exact committed base/head supplied by root, including after fixes.
- `http_status` is exact `int` 100–599 or `None`; reject bool and all coercions. Append it after existing dataclass fields with default `None` to preserve positional calls.
- No new failure vocabulary, accounting behavior, retry, selection authority, automatic activation, runtime provider I/O on construction/status/reconciliation, LIVE action, server, or paid request.
- Keep existing fixed host/model/routing, transport/body/privacy limits, disabled default, unknown-cost hold, exact charge and overrun behavior unchanged.
- No reason classifier, diagnostics JSON, body prose, headers, key labels, billing, management key, credits/endpoints query, or new provider adapter. No private/default vault/trial stores or actual credentials in tests.
- Use Python `>=3.12,<3.13` and existing locked dependencies. Constructor changes remain within existing local directory/DB semantics; all test stores live under `tmp_path`.
- RED requires behavioral assertion failures after importable minimal interfaces/stubs; collection, dependencies and fixture/setup failures are not accepted RED.
- Local Windows verification is focused synthetic tests/static checks only. Do not repeat the full Windows native/PAPER suite or a redundant full local suite before identical hosted coverage. Full suites run hosted/approved Linux; local Linux reruns are only for unresolved platform changes. Native 36/restart 6/restored 12 acceptance remains mandatory.
- Invalid/malformed status evidence projects to safe None without bypassing bounded body processing, dropping independently known cost, changing existing failures or releasing uncertainty. A valid observed status survives later body/context exceptions.

## Review Focus

- A failure during response context exit or body iteration must retain an already received status; Task 1 pins both with fake streams/transports.
- A body's numeric `error.code` and a boolean/string status must never replace or masquerade as transport evidence or erase independent cost; Task 1 pins exact scalar validation, invalid-header known/null-cost cases and body/status disagreement.
- Concurrent first opens must decide/migrate under one lock and must not issue duplicate ALTER or regress a completed v2; Task 2 tests simultaneous v0/v1 constructors and lock timeout.
- A malformed existing schema/status or interrupted migration must fail before settlement/provider effects, with transactional rollback; Task 2 tests metadata/value corruption and injected migration failure.
- Known status must survive CHARGED → local settlement/restart/overrun and unknown-cost holds without another generation or selection replay; Task 2 extends existing crash/charge/accounting tests.

---

## File map and bounded ownership

| File | Responsibility |
|---|---|
| `src/multi_exchange_trader/ai/generation.py` | Shared scalar validator, appended GenerationResult field, immediate capture and all generation return paths. |
| `src/multi_exchange_trader/ai/dispatch.py` | Appended DispatchResult field, v2 initialization/migration, persistence/read/settlement propagation. |
| `tests/ai/test_generation_dispatch.py` | Existing fake fixtures plus generation evidence, positional compatibility, durable result and accounting assertions. |
| `tests/ai/test_dispatch_http_status_storage.py` (new) | Pure-local handcrafted legacy schema, migration, concurrent opening, rollback and malformed-store cases. |

No worker/factory/operator/UI/workflow changes are expected: the worker consumes failure/selection and remains compatible. If rechecking merged main identifies a material interface change, correct this map before editing product code. Keep the two source modules in place; no new transport, generic storage framework or mutable diagnostics record.

### Task 1: Capture bounded generation status

**Files:** Modify `src/multi_exchange_trader/ai/generation.py` and `tests/ai/test_generation_dispatch.py`.

**Interfaces:**

- Consumes existing `OpenRouterGenerationClient.generate(self, request: SelectionRequest, *, quote: GenerationPriceQuote, now: datetime) -> GenerationResult` and `_parse(self, raw: bytes, status_code: int) -> GenerationResult` unchanged.
- Produces `validate_http_status(value: object) -> int | None`, raising fixed `ValueError("Invalid HTTP status")` on invalid input; `GenerationResult.__post_init__(self) -> None`; and appended `GenerationResult.http_status: int | None = None` after `output`.
- Existing `GenerationResult(failure, cost, output)` remains valid and means status unknown. New internal calls use keyword `http_status=`.

- [ ] **Step 1: Add failing generation/DTO tests using the existing fake request/quote/client fixtures.** Include `test_generation_http_status_scalar_contract` and `test_generation_result_preserves_positional_arguments`:

  ```python
  assert GenerationResult(None, "0.0012", "output").http_status is None
  assert GenerationResult(None, "0.0012", "output", http_status=200).output == "output"
  assert validate_http_status(None) is None
  assert validate_http_status(100) == 100
  assert validate_http_status(599) == 599
  # Parameterized invalid: True, False, 99, 600, "402", 402.0, Decimal("402"), int subclass.
  # Each invalid value raises the fixed ValueError from validator and GenerationResult.
  ```

  Add direct fake `client.generate(...)` tests named `test_generation_status_survives_post_header_failure`, `test_generation_status_is_none_before_headers`, and `test_transport_status_does_not_come_from_error_code`. Assert valid 200 success, 200 error envelope, 302 redirect, 401/402/403/404/429/503 errors, malformed/duplicate/non-UTF8 JSON, reflected fake key, unsupported encoding, invalid/oversized content length, streamed overflow, body deadline, body `ReadTimeout`, body transport/arbitrary exception and context-exit exception retain the exact received number. A 404 response with body `error.code=402` remains 404. Pre-header timeout/transport/arbitrary exceptions have None. Preserve each path's existing failure string, absence of raw text, one fake call and independent exact/null cost behavior.

  Add `test_invalid_header_status_does_not_erase_cost` with test-only malformed response status values (bool/string/float/out-of-range). For a bounded otherwise valid error envelope, assert `http_status is None`, existing `HTTP_ERROR`, independently valid exact usage cost retained, and no raw diagnostic value exposed. Repeat with absent/invalid cost and assert cost stays None; the dispatch accounting regression must retain the full uncertain hold. Do not let status validation raise out of the evidence projection and skip charge extraction.

- [ ] **Step 2: Make new interfaces importable, then run the tests and record behavioral RED.**

  If the new imports or constructor field are missing, first add only minimal importable signatures/dataclass field and nonfunctional stubs needed to collect the tests. Do not implement capture, validation or persistence yet. Confirm collection and fixtures succeed; collection/setup failures do not satisfy this step.

  Run `uv run --locked pytest -q tests/ai/test_generation_dispatch.py -k "generation_http_status or generation_result_preserves or generation_status or transport_status or invalid_header_status"`.
  Expected before implementation: collected tests fail behaviorally because a received 402/404/429 is not retained and/or invalid scalar inputs are accepted. Record an actual assertion failure and its expected value. Resolve collection/setup errors separately; do not call any provider.

- [ ] **Step 3: Implement only the Task 1 interfaces and status propagation.**

  The validator checks `value is None` or `type(value) is int and 100 <= value <= 599`, without coercion or input interpolation. The dataclass validates at runtime. Initialize observed status before transport entry; assign it immediately after headers, then include it on every result and existing exception path. Preserve it if iteration/context exit fails. Keep existing bounded body/cost extraction, failure strings and output checks unchanged. At the transport evidence boundary, invalid/malformed status projects to None without aborting body/cost processing or replacing the existing failure path; preserve independently known exact cost and retain full uncertainty for null cost. Do not broaden public interfaces to implement this separation.

- [ ] **Step 4: Run focused GREEN and static checks.**

  Run the Step 2 command, then `uv run --locked pytest -q tests/ai/test_generation_dispatch.py`, `uv run --locked ruff check src/multi_exchange_trader/ai/generation.py tests/ai/test_generation_dispatch.py`, and `uv run --locked mypy src/multi_exchange_trader/ai/generation.py`. Expected: all selected tests pass with no Ruff/mypy findings; existing accounting tests still pass. These are fake/local checks, not provider access evidence.

- [ ] **Step 5: Hand the bounded unit to root for a committed review snapshot.**

  The worker reports changed files, focused RED/GREEN/static evidence and remaining concerns without Git mutation. Root alone stages/commits the Task 1 snapshot (`feat(ai): preserve bounded generation HTTP status`) and supplies exact committed base/head to a fresh reviewer. Review all generation return/exception paths, exact-type validation, malformed-status known/null-cost independence, privacy, positional compatibility and unchanged accounting/transport controls. Workers fix actionable findings without Git mutation and rerun affected checks; root commits fixes and supplies the new exact head for re-review. Do not merge the partial slice independently; Task 2 completes durable propagation on the same new branch.

### Task 2: Persist dispatch status with safe v2 migration

**Files:** Modify `src/multi_exchange_trader/ai/dispatch.py` and `tests/ai/test_generation_dispatch.py`; create `tests/ai/test_dispatch_http_status_storage.py`.

**Interfaces:**

- Consumes Task 1 `GenerationResult.http_status` and `validate_http_status(value: object) -> int | None`.
- Produces appended `DispatchResult.http_status: int | None = None` after `selection`, with `__post_init__(self) -> None` validation. Existing six positional values retain their meaning.
- Preserves public `AIDispatcher.__init__`, `dispatch`, `status(request_id: str) -> DispatchResult | None`, and `reconcile_charge(request_id: str) -> DispatchResult` signatures.
- Extends private `_record(self, request_id: str, status: str, failure: str | None, cost: str | None = None, *, http_status: int | None = None) -> None` and `_record_locked(self, db: sqlite3.Connection, request_id: str, status: str, failure: str | None, cost: str | None = None, *, http_status: int | None = None) -> None`. Both validate; None preserves a known stored value.
- Produces local schema v2: exact legacy eight columns followed by nullable `http_status INTEGER`; no backfill. The design's accepted v0/v1/v2 matrix and constructor lock/rollback rules are mandatory.

- [ ] **Step 1: Add failing durable/result tests to the existing fake dispatch suite.**

  Add `test_dispatch_result_preserves_positional_arguments_and_validates_status`; assert old six-position construction keeps `selection`, defaults status to None, and rejects the Task 1 invalid scalar set. Extend success/error/null-cost/secret-response assertions to inspect returned result, `status()`, reopened dispatcher and persisted `http_status`. Assert 402/404/429 remain distinct with `UNCERTAIN`, exact cost None and USD 0.01 held. Existing blocked/pre-response results remain None. Assert repr/SQLite contain no fake key, error prose, billing text, flagged input, candidate reason or provider identifiers newly copied from the body.

  Extend `test_charge_persists_before_settlement_failure_then_local_reconcile` to a fake 429 response with exact usage cost: CHARGED status, stored cost and 429 survive the forced budget-settlement failure, restart and local reconciliation; result selection stays None and fake-call count stays one. Extend the existing pending-charge/overrun/restart tests to retain 200. Duplicate dispatch remains `ALREADY_DISPATCHED`; use `status()` to read original evidence rather than changing that existing duplicate-call result contract.

- [ ] **Step 2: Add failing local storage migration tests in the new test file.**

  Use a literal legacy CREATE TABLE with the exact observed eight-column metadata, fake client, precreated temporary BudgetBook, and synthetic rows only. Do not obtain legacy fixtures from a default/private database. Required named tests and assertions:

  | Test | Required observation |
  |---|---|
  | `test_v1_rows_migrate_without_inferred_http_status` | version 1 → 2, original eight values unchanged, new value SQL NULL, old uncertain/charged rows remain consumed, status/reconcile never generate. |
  | `test_v0_empty_and_interrupted_legacy_create_are_supported` | empty v0 creates v2; exact v0 legacy table migrates; repeated v2 open is idempotent. |
  | `test_concurrent_dispatcher_open_migrates_once` | parameterized empty v0 / interrupted v0 / legacy v1; synchronize threads before construction, all open successfully, exactly one appended column, v2 and legacy rows intact, zero client/budget mutations. |
  | `test_migration_lock_timeout_fails_closed` | held writer lock plus test-only short SQLite timeout raises local storage error; no generation, reservation or schema/version change. |
  | `test_migration_failure_rolls_back_schema_and_version` | inject failure after ALTER but before commit through a test-only connection hook; still v1/eight columns/unchanged rows afterward, then normal reopen succeeds. |
  | `test_incompatible_schema_fails_closed` | unknown version; missing table at v1/v2; renamed/missing/extra/hidden/mis-typed column; wrong PK/nullability; view; nonempty unknown v0; corrupt database all reject without repair, provider call or settlement. |
  | `test_invalid_stored_http_status_fails_before_accounting` | forged v2 rows with text/blob/real/out-of-range status reject on constructor and, when inserted after a valid open, on status/local reconciliation before budget settlement. None/100/599 succeed. |

  Pin ordinary schema metadata with `PRAGMA table_xinfo`; legacy request_id has `notnull=0`, `pk=1`. Update the old happy-path assertion from user_version 1 to 2. Do not add SQL CHECK merely to make corruption tests impossible; runtime/store-read validation is the contract.

- [ ] **Step 3: Run focused tests and record meaningful RED.**

  Run `uv run --locked pytest -q tests/ai/test_generation_dispatch.py tests/ai/test_dispatch_http_status_storage.py`.
  Make any newly imported signatures/field importable with minimal nonfunctional stubs first. Expected RED: collected tests fail behaviorally on absent v2 migration/concurrency behavior and durable status assertions. Collection, dependencies and fixture/setup errors do not count. Keep failures tied to required behavior rather than changing expected failure strings or making accounting assertions weaker.

- [ ] **Step 4: Implement v2 initialization and durable propagation in dispatch.py.**

  Begin IMMEDIATE before reading version; validate the exact known table metadata; create/migrate according to the design using individual execute calls, not executescript; verify v2 metadata, stored scalar type/range and quick_check; set version and commit atomically. Let constructor storage exceptions fail closed and roll back. Keep connection timeout/sync/local-only behavior. Use explicit INSERT column names with NULL status for claims. Validate stored status before `_apply_charge_locked` settles a row. Persist generated status with UNCERTAIN or CHARGED before returning/settling, retain it with COALESCE on later transitions, and return it by keyword from final results and `status()`.

- [ ] **Step 5: Run focused GREEN, related AI tests and static checks.**

  Run the Step 3 command, then `uv run --locked pytest -q tests/ai`, `uv run --locked ruff check src/multi_exchange_trader/ai tests/ai`, and `uv run --locked mypy src/multi_exchange_trader/ai`. Expected: all selected tests pass, including existing accounting/idempotency/concurrent repair tests, with no Ruff/mypy findings. No provider/network/actual credential activity occurs.

- [ ] **Step 6: Hand the durable unit to root for a committed review snapshot.**

  The worker reports changed files and focused verification without Git mutation. Root alone commits the Task 2 snapshot (`feat(ai): persist dispatch HTTP status across restart`) and provides exact committed base/head to a fresh reviewer. Review constructor transaction ordering/rollback and two-opener behavior; schema metadata/type validation; all writes and settlement reads; no status erasure; positional compatibility; and conservative accounting under failure. Workers fix findings and rerun affected checks without Git mutation; root commits fixes and provides the new exact head for re-review.

## Final release verification and handoff gate

- [ ] Complete a fresh whole-change review against the committed design and exact committed final base/head supplied by root. Check no dependency/workflow/provider/model/privacy/spending or unrelated product change slipped in. Workers make no Git mutations; root commits any fixes before the corresponding exact-head re-review.
- [ ] Keep Windows checks to the focused synthetic/static commands above; do not run the full Windows native/PAPER suite or duplicate the full hosted suite locally. Full verification uses existing hosted CI, which runs `uv sync --locked`, `uv run pytest -q`, `uv run ruff check src tests scripts`, and `uv run mypy src scripts` in Python 3.12 with the locked environment. Use an approved local Linux run only to resolve an actual platform uncertainty, then run only the relevant coverage. Record the exact head/results; skipped local native cases do not pass native acceptance.
- [ ] Root alone pushes the reviewed new branch through the existing PR process. Require exact hosted CI for the final reviewed head, including unchanged Python checks, Linux operations checks, and the existing network-isolated native contract job: 36 actual native cases, 6 actual restart cases, and all 12 named restored-monitoring cases, with no skipped/failing/error cases and separate artifacts. Do not edit workflow assertions or counts to obtain green.
- [ ] Root alone merges that reviewed passing head under existing user authority and reads back merged main SHA, source/diff and hosted main CI status. An older passing head or still-running main CI is not completion; wait or report the exact incomplete gate honestly.
- [ ] Report code/CI acceptance separately from provider execution. The consumed original trial remains uncertain with unknown charge and intact reservation; status cannot be retrospectively recovered, and this slice authorizes no new paid smoke or retry. Root can later consume the new safe field without changing this implementation's scope.

## Self-review result

The two tasks cover every design requirement and all five Review Focus conditions. Task 2 consumes the exact Task 1 field/helper; no public calling signature is reordered. Constructor migration, initial positional INSERT, settled/overrun writes, status readback and local pending-charge repair are explicitly covered. No product implementation, test execution, Git operation or provider operation was performed by this draft.


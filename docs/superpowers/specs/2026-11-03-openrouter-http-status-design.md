# OpenRouter HTTP status preservation

Date: 2026-11-03. Controller design under delegated implementation authority, seeded from merged main `5c07a69d237bf7c724b8d0ec7fd66d094468f71c` after PR25 and exact reviewed CI115 passed all seven jobs. Parent main CI116 is in progress; actual profile operator use remains gated on it and uses a separate frozen source copy. This feature starts in the existing linked worktree on `gpt/openrouter-http-status-20261103`; it grants no new provider or spending action.

## Intent and evidence boundary

Preserve the actual nullable numeric transport status through `GenerationResult → DispatchResult → ai_dispatches → status()/reconcile_charge()`. A future result must distinguish an observed 402 from 404 or 429 without guessing a cause or retaining response prose.

Root reports that the consumed one-shot paid trial returned `UNCERTAIN / HTTP_ERROR`, unknown exact cost, and a USD 0.01 uncertain reservation preserved after restart. This is supplied context, not a new inspection of its private evidence. The old adapter discarded the status; this change cannot recover the old response, determine its charge, authorize retry, or backfill a historical status.

Inputs inspected read-only: `src/multi_exchange_trader/ai/generation.py`, `src/multi_exchange_trader/ai/dispatch.py`, `tests/ai/test_generation_dispatch.py`, the `execution/worker.py` dispatch consumer, `pyproject.toml`, `.github/workflows/ci.yml`, and the audit's `openrouter-failure-diagnostics-contract.md`. The broader diagnostics contract is an input, not this slice's specification. No Git verification or private operational evidence was used by this drafting task.

## Goals, constraints, and exclusions

- Add only `http_status: int | None`, with `None` meaning that no valid numeric response status was observed. Exact `int` values 100–599 are valid; bool, float, string, Decimal, subclasses, and out-of-range integers are invalid. Never coerce.
- Keep all current failure strings and their selection/accounting behavior. Malformed JSON remains `INVALID_RESPONSE`; a status does not turn invalid output into an accepted selection.
- Capture status immediately after headers, before redirect, encoding, length, body iteration, or JSON checks. Retain it on every later result, including body-read timeout/transport exception. Exceptions before headers have `None`.
- Keep the fixed POST host, no redirects, no retry, fixed model and routing controls, body/depth/node limits, exact cost handling, privacy checks, disabled default, quote requirements, liability holds, and overrun rules unchanged.
- No reason taxonomy, raw body/prose/header storage, provider error classifier, key labels, billing information, management key, credits/endpoints query, provider adapter, free/model rotation, paid request, LIVE activity, server, or activation.
- Use Python `>=3.12,<3.13` and the existing locked dependencies. No dependency or workflow changes are needed.

## Approaches and delegated choice

1. **One typed nullable field and one SQLite column — selected by the builder under delegated design authority.** Smallest change that survives restart and supports operator readback. Requires a local schema migration and constructor concurrency tests.
2. A diagnostics JSON envelope/reason taxonomy — deferred. It adds parsing, precedence, privacy and schema complexity without being required to preserve the actual status.
3. An in-memory field only — insufficient because restart/local reconciliation would discard the evidence.

The selected approach adds no provider cost. Its costs are one nullable column, a local transactional migration when opening the existing dispatch store, two focused implementation/review units, and the already required exact CI qualification. It grants no spending permission.

## Existing interfaces and proposed additions

Current `GenerationResult` positional order is `(failure, exact_cost_usd=None, output=None)`; current `DispatchResult` is `(request_id, status, failure=None, reservation_status=None, exact_cost_usd=None, selection=None)`. Preserve these positions by appending `http_status: int | None = None` after the existing fields. Both remain frozen/slotted and keep output/selection hidden from repr. Use keyword `http_status=` at new call sites.

In `ai/generation.py`, add the shared scalar validator `validate_http_status(value: object) -> int | None`. Return the unchanged valid value/None; otherwise raise `ValueError("Invalid HTTP status")` without interpolating input. Each result's `__post_init__(self) -> None` invokes it. `object` is used only for this untrusted scalar boundary; do not broaden existing result, selection, or mutable container types.

Keep `OpenRouterGenerationClient.generate(...)` and `_parse(self, raw: bytes, status_code: int) -> GenerationResult` calling signatures unchanged. Initialize a local observed status to `None` before transport entry; validate and assign `response.status_code` immediately after the response context yields. Pass it to every subsequent result and exception result. An invalid/malformed diagnostic status becomes safe `None`; diagnostic validation must not abort existing bounded body/cost processing, invent a new failure string, erase a known exact cost, or release uncertainty. Preserve an already captured valid status even if response iteration or context exit raises. Keep raw transport handling separate from the validated evidence projection so malformed evidence cannot weaken the existing failure/accounting path; no broader public interface is required.

`DispatchResult.__post_init__` uses the same validator. In `ai/dispatch.py`, preserve the existing positional parameters of `_record` and `_record_locked`; add only keyword-only `http_status: int | None = None`. Validate before writing. Update status using `http_status=COALESCE(?, http_status)` so a later local bookkeeping transition cannot erase a known response status. No operation needs to clear or replace an observed status with an inferred value.

The unknown-cost path commits `UNCERTAIN` and observed status together before returning. The exact-cost path commits `CHARGED`, exact cost, and status together before budget settlement. `SETTLED`, invalid-selection and overrun transitions preserve that status. `status()` validates the stored scalar and returns it by keyword. `_apply_charge_locked` validates the stored scalar before any budget settlement and preserves it; `reconcile_charge()` returns the same value through `status()` and performs no provider I/O. Storage failure retains the existing conservative liability/no-retry behavior; physical storage failure cannot promise successful durable evidence writes.

## Local schema migration

The observed v1 table is exactly eight columns, in order:

`request_id TEXT PRIMARY KEY`, `binding_hash TEXT NOT NULL`, `maximum TEXT NOT NULL`, `status TEXT NOT NULL`, `failure TEXT`, `exact_cost TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`.

Version 2 appends only nullable `http_status INTEGER`. Use one column, no diagnostics JSON or table rebuild. Runtime validation at construction, write, stored-row read and settlement boundaries is authoritative; validate SQLite storage class as well as range. An INTEGER-affinity column alone is not proof that an application input had exact Python type `int`.

Constructor sequence, entirely local:

1. Keep the existing parent-directory creation, SQLite timeout of 10 seconds, row factory and `synchronous=FULL` semantics. Open `BEGIN IMMEDIATE` **before** reading `user_version` or deciding the schema action. Do not use `executescript`, which can undermine the transaction boundary.
2. Under that writer lock, read version and validate that `ai_dispatches` is a real table with the exact expected column names/order, declared types, nullability, and primary key metadata. Account for the legacy `TEXT PRIMARY KEY` reporting `notnull=0`; do not silently rewrite it.
3. Accept version 0 only for an empty new database, or the exact legacy eight-column table left by an interrupted old create. Create v2 in the former; migrate the latter. Accept version 1 only with the exact eight-column table and append the column with `ALTER TABLE`. Accept version 2 only with the exact nine-column table. Missing/mis-typed/extra/hidden columns, incompatible versions/layouts, views, and malformed storage fail closed; never guess a repair or downgrade a version.
4. Existing rows receive SQL NULL naturally, including any old uncertain/charged rows. Never infer a status from failure strings, body codes, current metadata, or cost.
5. Revalidate the v2 layout, reject non-NULL status values whose SQLite `typeof` is not `integer` or whose value is outside 100–599, and require `PRAGMA quick_check` to return `ok`. Set `user_version=2` and commit only after these checks. Any exception rolls back schema/version changes and closes the connection.

Concurrent constructors serialize the complete decision and migration under the same writer lock, then re-read current schema/version. The second opener must see completed v2 instead of issuing a second `ALTER TABLE`. A lock timeout raises the existing local storage exception; it performs no budget/provider action. Constructors still perform only the existing category of local directory/DB I/O, without generation, metadata, budget configuration, or program activation.

Replace the current positional eight-value `INSERT INTO ai_dispatches VALUES (...)` with an explicit column list and a NULL status for new pre-response claims. This is required for the appended column and makes the initial unknown state explicit.

## Safe operator boundary and unchanged accounting

The product deliverable is the field on the existing safe result/repr and local status/reconciliation APIs. Root owns any later operator receipt usage. This slice adds no operator script, web endpoint, UI, or generic receipt framework. Only an actual integer/None is newly exposed; body `error.code`, messages, labels, amounts and provider identifiers never populate it.

`HTTP_ERROR` with absent cost remains uncertain with the full maximum held, regardless of whether the observed status is 402, 404 or 429. Exact valid charge evidence still settles even when output is rejected, and an overrun still blocks selection. An old request ID remains consumed; duplicate dispatch, restart and reconciliation never send a second request or reconstruct a selection. Backups still require both dispatch and budget stores from one quiescent snapshot. An old binary must not reopen/downgrade a migrated v2 store.

## Latest user-directed Haiku 5.5 verification extension

The user clarified that the registered OpenRouter key is restricted to `anthropic/claude-haiku-5.5`, then explicitly requested a minimal real test of that model. Root corrected the managed model in revision3 while preserving the same AI key and both Bybit pairs; no source note/env reread or generation occurred in that configuration change. The earlier Qwen request was an incorrect out-of-target smoke, not a Haiku test; its unknown cost/hold remains unchanged. Model lookup/key authentication is not generation access proof. No alternate/free/latest model is permitted for this key.

Task3 adds two small protocol-compatible controls before the new test. OpenRouter's current [OpenAPI schema](https://openrouter.ai/openapi.json), `components.schemas.ChatAssistantMessage.properties.refusal`, permits string or null. Accept absent or JSON-null refusal only. Keep every non-null refusal rejected and retain rejection of any tool_calls/function_call field, strict model/role/finish/content/output validation, duplicate-key/privacy limits and independent cost handling. This is a statically demonstrated compatibility gap; no actual Haiku response has yet been observed.

Add explicit keyword-only `reasoning_enabled: bool | None = None` to the existing generation client and private factory. Exact bool or None only; no coercion/raw diagnostics. None keeps the prior payload unchanged. An explicit bool adds only `reasoning: {enabled: <bool>}` and does not activate, quote, reserve, change model/host/routing, or perform I/O. The factory forwards it into the same-key/same-model client. The [official reasoning guide](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) documents the option and that reasoning consumes the output bound. Root's Haiku probe will explicitly disable reasoning and use one short fixed selection request/max128 output; the full-context honest quote remains unchanged. Do not fabricate a smaller context/token count/price/access result.

Root alone may conduct one newly authorized Haiku probe after reviews/exact CI/main gates. Use a distinct isolated test book/dispatch and durable one-attempt marker, preserving the earlier Qwen book/hold. The new internal reservation budget is at most USD0.11 to admit the real full-1M-context conservative quote (current authenticated metadata/max128 quote USD0.10506720). This is reservation exposure, not an asserted actual charge or provider-level guarantee. Actual billing is the provider-reported exact cost of the short request. No credit purchase, business-budget change, automatic retry or model fallback. Missing cost remains uncertain; historical response/status cannot be recovered by this change.

## Acceptance and execution boundary

Implement as three sequential independently testable units: bounded generation evidence, durable dispatch/schema propagation, then the explicit Haiku compatibility extension. Use only fake transports, fake keys, and `tmp_path` stores. RED means a behavioral assertion failure after any required minimal interfaces/stubs are importable; collection, dependency and fixture/setup errors do not count. Required tests cover exact scalar types/range; invalid diagnostic status with independently known/null cost; all post-header rejection/exception paths; pre-header `None`; independent cost accounting; positional compatibility; v1/v0 migration, rollback, corruption and concurrent opening; restart and local reconciliation; no retry; and safe repr/storage projection.

Root serializes every Git mutation, commit and package operation; implementation/review workers never Git-mutate. Root commits each bounded unit before a fresh reviewer examines its exact committed base/head, and commits any fixes before re-review. The final whole-change review also uses exact committed revisions.

After focused synthetic RED/GREEN and static checks, require fresh whole-change review and exact hosted locked CI at the final reviewed head, including full Python/static checks and unchanged network-isolated native 36/restart 6/restored-monitoring 12 receipts. Focused Windows synthetic checks are sufficient locally; do not repeat the full Windows native/PAPER suite or run a redundant full local suite before identical hosted coverage. Full platform suites run hosted or in an approved Linux environment; local Linux repetition is justified only by an unresolved platform change. Root merges only the reviewed passing head and reads back merged main and its CI. Windows native skips are not hosted acceptance. Workers perform no paid smoke. Root performs the separately authorized Haiku-only minimal probe after reviewed source/CI/main gates.

No unresolved product choice requires a user question. The builder must recheck the actual main interfaces after the current profile PR merges; a conflicting interface/schema change requires a narrow plan correction, not a silent assumption or edits to the frozen branch.


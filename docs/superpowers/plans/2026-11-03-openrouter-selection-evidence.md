# OpenRouter Selection Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Advertise the exact existing selection output bounds and preserve a safe fixed rejection code without weakening validation or guessing the consumed trial's cause.

**Architecture:** Add per-request numeric bounds only to generation system instructions. Append a nullable dispatch diagnostic through DTO, atomic schema3 migration, writes and readback; keep all accounting and selection authority unchanged.

**Tech Stack:** Existing locked Python/httpx/SQLite/pytest, no dependency or workflow change.

**Spec:** docs/superpowers/specs/2026-11-03-openrouter-selection-evidence-design.md

## Global Constraints

- Worktree F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102, baseline main aab53709abf52b90887c7c973a5fc078a0d5e621, new branch gpt/openrouter-selection-evidence-20261103. Root owns Git, no other worktree.
- Actual previous Haiku HTTP200/cost0.0000846/SETTLED/INVALID_SELECTION is a failed response-contract test. Cause unknown. No historical output/code reconstruction, paid retry, actual/default store/key/env/note/trialDB/network/orders/server access by workers. Synthetic/fake only.
- Preserve failure=INVALID_SELECTION, old positional/default DTO behavior, HTTP status, cost/unknown holds/idempotency/no replay; diagnostic is not authority. No model/token/TTL/parser/worker/budget-policy/UI/dependency change.
- selection_rejection_code is None or exact allowed str. Unknown captured exception codes map to fixed UNCLASSIFIED, never arbitrary text. Explicit invalid DTO/write/storage fails with fixed safe error before accounting effects. Full fixed set is in the spec and must be copied exactly.
- Known schema0/1/2 ->3 and exact3 only; BEGIN IMMEDIATE before decisions; full exact layout and diagnostic validation/quick_check/commit in one transaction. No executescript/repair/backfill. Actual migrations require prior quiescent paired backup, not performed by these workers.
- Each task behavioral RED then focused GREEN/covering checks, separate Ruff/mypy receipts. No full Windows/native/PAPER suite. Final exact hosted CI and main CI own full qualification. Preserve evidence and originals.

## Review Focus

1. Different validated numeric bounds, Unicode reason versus UTF-8 output size, private candidate details: Task1 must advertise the right limits without private interpolation or wire authority changes.
2. Unknown/secret-like/non-str exception code after a known paid charge: Task2 must return UNCLASSIFIED and settle normally, never leak or erase liability.
3. Restart/repair/overrun and later record calls: Task2 must preserve code/status/cost without reconstruction or second generation.
4. Legacy v0/v1/v2, exact v3, concurrent openers and interrupted migration: Task2 pins transactional all-or-nothing known migration with NULL history and unchanged original fields.
5. Corrupt stored code, wrong SQLite type/layout and forged DTO/write values: Task2 rejects before budget repair, with no secret in exception/repr/DB beyond explicitly synthetic malicious storage fixture.

### Task 1: Advertise existing per-request output limits

**Files:** Modify src/multi_exchange_trader/ai/generation.py and tests/ai/test_generation_dispatch.py only.

- [ ] Read the spec and existing prepare/public_candidate_request/validator. Keep public JSON and all generation routing/quote/schema/privacy checks unchanged.
- [ ] Add behavioral tests against current prepare: dynamic bounds from multiple valid requests, reason character versus response UTF-8 byte wording, no reason control characters, same seven fields/copy bindings/SELECT-or-ABSTAIN instructions, no private request values/system candidate interpolation, no construction/prepare network/reserve effects. RED must be an assertion failure on missing/wrong instructions.
- [ ] After request validation, append concise instructions derived only from request.limits.max_reason_chars and max_response_bytes to system content. No duplicated configurable defaults, examples, parser change or relaxed bound.
- [ ] Run the targeted new tests and full tests/ai/test_generation_dispatch.py. Ruff changed files, strict mypy generation.py; save separate stdout/stderr receipts and commands/exits. Root handles commit.
- [ ] Write task-1-report.md with exact final hashes, RED/GREEN/static evidence, source freeze and limits. No actual model compliance claim.

### Task 2: Persist safe selection rejection diagnostics

**Files:** Modify src/multi_exchange_trader/ai/dispatch.py, tests/ai/test_generation_dispatch.py and tests/ai/test_dispatch_http_status_storage.py; create tests/ai/test_selection_rejection_evidence.py if coherent focused separation helps. No other source file.

- [ ] Read the spec and current schema2/record/status/reconcile/dispatch composition. Task1 is already reviewed; preserve its instructions.
- [ ] Add minimal importable interfaces if necessary, then behavioral RED: fixed allowlisted code/None validation and old DTO positions; real select_all_in_one fixed rejections with exact code, valid SELECT/ABSTAIN code None; injected secret-like/unknown/non-str exception code maps UNCLASSIFIED while known charge settles; privacy and unknown-cost/overrun remain intact.
- [ ] Append selection_rejection_code to DTO and implement exact safe validator plus capture projection. Keep generic failure unchanged; never store/format detail or raw unknown code. Add nullable TEXT column ordinal9.
- [ ] Implement migration under existing BEGIN IMMEDIATE: empty0, exact8col0/1, exact9col2 -> exact10col3; exact3 idempotent. Check pre-migration source layout and post-migration target layout, all stored diagnostics and quick_check before version3 commit. Preserve v0 object restrictions; reject unknown/incompatible forms. Update existing version/layout expectations as needed, retaining old migration/HTTP tests rather than deleting coverage.
- [ ] Record new code with final failure, preserve via COALESCE, include in status/reconcile/result and validate SQLite typeof/value BEFORE accounting. Explicit column INSERT remains required. A later overrun may override failure without erasing underlying code.
- [ ] Pin v0/v1/v2 oldrowNULL and original8cols/http status unchanged, v3 reuse, fail injection rollback/version/row preservation, two concurrent initial/migrating openers, malformed schema/version/code, constructor and status/reconcile rejection before settlement, durable readback/repair/no selection replay/no second POST, positional defaults and repr privacy. Do not claim recovery of historical actual code.
- [ ] Run focused changed tests then full tests/ai (existing Windows symlink skips distinguished). Ruff changed files and strict mypy src/multi_exchange_trader/ai; retain separate receipts. No native/PAPER/full-project duplicate local run.
- [ ] Write task-2-report.md with commands/exits, hashes, source freeze, RED/GREEN/static and limitations. Root commits, fresh task review, then fresh whole review/full hosted/main gates. No actual migration or provider call.

## Completion

Root generates whole-branch package from baselineaab53709, fresh final review, exact hosted CI including unskipped native36/6/12 and passing main merge/readback/mainCI. No raw historical selection or code can be recreated by this feature. Any future actual probe is a separate root-only acceptance decision; this feature performs zero paid calls.

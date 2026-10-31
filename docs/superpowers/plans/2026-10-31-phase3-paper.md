# Phase 3 Local PAPER Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans directly; the user explicitly requires no subagents or further design-approval questions. TDD and verification-before-completion apply.

**Goal:** durable local-only educational futures PAPER powered by actual Nautilus1.231.0 simulated exchange.
**Architecture:** deterministic native replay + validating application risk + atomic SQLite audit, exposed through the existing FastAPI/React app.
**Tech stack:** Python3.12, nautilus_trader==1.231.0, stdlibSQLite/Decimal, FastAPI, React/TS.
**Spec:** docs/superpowers/specs/2026-10-31-phase3-paper-design.md and the explicit user request.

## Global constraints

No new outside hosts/credentials/accounts/orders/transfers/Demo/paid service/deployment/main merge/force-push/regional bypass. fapi/Bybit external calls0; existing preblock setting preserved. PHASE2A six markets remain BLOCKED. Synthetic outcomes never count as market success. Preserve work/backups. Commit checkpoints normally on codex/initial-platform. At most3 source CI runs; evidence-only[skip ci].

## Review focus

- Replay after a failed SQL commit must not add a deposit/fill; task2 rollback/restart tests.
- Partial close then stop/liquidation must never reverse; task1 native reduce-only/OCO tests.
- Gap loss can exceed isolated allocation and make cash negative; task1 native account/reconciliation test.
- Same idempotency key with changed payload must conflict after restart; task2 test.
- UI state must not imply a live quote or lose failure status during restart; task3 browser QA.

### Task1: qualify and wrap the native core

Files: src/multi_exchange_trader/paper/{contract,native}.py; tests/paper/test_native.py; pyproject.toml/uv.lock; NOTICE component license.
Interface: replay(events:list[dict])->snapshot+native fills/orders, only local inputs; validate decimal contract separately.
- [ ] Save exact wheel/tag/release/license and executed native fill/account/OCO evidence; checkpoint docs before implementation.
- [ ] Write tests for version/native fills, hand long/short/partial/fees/slippage, native SLTP set/replace/cancel/OCO, isolated-risk liquidation/gap debt and invalid quantities. Watch RED.
- [ ] Implement native strategy/SimulatedExchange adapter; no external adapter factories and no substitute matching logic. Watch GREEN and native-derived reconciliation/invariants.
- [ ] Pin dependency/lock, check full Python/static compatibility and source checkpoint1/CI if integrated readiness permits.

### Task2: durable service and local API

Files: paper/{store,api}.py; existing api.py lifecycle; tests/paper/test_store.py,test_api.py; .gitignore.
Interfaces: PaperStore(path).command(scenario,key,payload), state(scenario); native replay fromtask1.
- [ ] Write RED seed-once/idempotency/conflict/concurrent/rollback/restart/schema/corruption/audit/invariant/network-denial and Host/Origin/JSON tests.
- [ ] Implement atomic SQLite append-only chain, replay/reconciliation and bounded scenarios; fail closed. Add local-only fixed PAPER routes, no reset/live controls.
- [ ] GREEN full Python/static; preserve old API inventory assertions by explicitly accounting for new PAPER routes. Commit source checkpoint at an independently complete boundary.

### Task3: PAPER UI and complete acceptance

Files: apps/web/src/paper/*, App/tab integration, styles scoped; tests; docs/qa/phase3.
- [ ] Write UI RED for synthetic labels/decimal inputs/pending/idempotency/error/protection/ledger; implement responsive Korean PAPER workflow.
- [ ] Run full Python/frontend/static/build. Commit integrated source (total sourceCI<=3), push; verify every hosted job, fix failures without gate weakening.
- [ ] Run real local PC/mobile workflow and actual server-process restart against a gitignored testDB, prove persistence/replay/seed once and protection continuation. Record screenshot/JSON/KST synthetic provenance.
- [ ] Review complete diff directly; Work performs independent review. Update ARCHITECTURE/QA_PLAN/WORK_LOG/report, document actual supported/unsupported scope and any BLOCKED/UNRUN.
- [ ] Evidence-only[skip ci] commit/push, verify remote SHA/main/Draft PR and original backups/work protected.

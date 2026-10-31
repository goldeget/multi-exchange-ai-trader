# Phase4 historical research and automatic synthetic PAPER implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, task-by-task. User explicitly forbids subagents and further design approval questions. Preserve existing work/; no worktree switch/reset.

**Goal:** Qualified immutable data/native research and risk-controlled synthetic PAPER automation with Korean UI and reviewable remote evidence.
**Architecture:** Validated local dataset→socket-denied native v1 replay→reproducible chronological report; separate SQLite synthetic-only runner/scheduler→existing native PAPER execution, no live path.
**Tech Stack:** Existing Python3.12/FastAPI/SQLite/HTTPX/Nautilus1.231.0 and React/TS/Vite, unchanged patched dependencies.
**Spec:** docs/superpowers/specs/2026-10-31-phase4-research-design.md

## Global constraints

Baseline docs56a212c5/sourceedf89138, branch codex/initial-platform, main7ddcded/DraftPR1. Preserve all work/backups/DB. Only approved data-api.binance.vision public spot collection; fapi/Bybit remain preblocked0requests. Actual spot and synthetic perpetual data/accounts/performance never mix. No alternate matcher, AI/live/Demo/key/account/paid service/deployment/main merge/force/reset/network change. No new dependency or denied download retry. Native OS network isolation remains fail-closed. CI never fetches exchange markets.

## Review focus

- Tampered ID/provenance/symbol or missing/duplicate/unclosed/future candles must be rejected; task1 tests.
- Prefix signals, next-open fills and untouched final OOS must remain invariant to future-data perturbation; task2 tests.
- Native spot inventory equity must not be presented as futures margin cash or funded shorting; task2 hand accounting and product-mismatch tests.
- Concurrent steps, response loss, SQL rollback and process restart must not repeat virtual funds/orders or silently resume a paused run; task3+4 tests/real restart.
- Selection failure/unknown 5xx must preserve correct resource identity/key and prevent stale-account actions; task4 offline/UI+browser tests.

### Task1 — validated immutable datasets and fixed-host collector (source checkpoint1)
**Files:** research/data.py, research/collection.py; scripts/collect_phase4_spot.py; tests/research/test_data.py, test_collection.py; built-in synthetic fixture under tests/fixtures/research or packaged research/fixtures.
**Interfaces:** validate_dataset(envelope)->validateddict; save_dataset(root,envelope)->SHAID; load_dataset(root,id)->dict; synthetic_dataset(symbol='BTC-PAPER')->dict; collect_spot(client,symbol,start,end)->dict.
- [ ] Write behavioral tests first: decimal/time/OHLC/count/gap/checksum/source/product, no arbitrary path/host; collector public GET/body0/no redirect/one transmission and stop after403/451; synthetic provenance explicit.
- [ ] Run RED, implement minimum, run focused GREEN and full regression/static. Record actual commands/results.
- [ ] Commit/push GREEN source immediately, report SHA; CI1 observed once. Choose fixed30day1h closed period; run approved guarded collector, save raw/normalized provenance/checksum. No denied-host retries. Evidence docs skipCI.

### Task2 — single-engine native replay and temporal evaluation
**Files:** research/native.py, strategy.py, evaluation.py, worker.py, sandbox.py; tests/research/test_native.py, test_evaluation.py.
**Interfaces:** signal(closedbars,fast,slow,market)->target; run_segment(dataset,config,start,end)->native result/curve/fills; evaluate(dataset)->report; sandbox_evaluate(dataset)->report.
- [ ] Inspect/qualify exact native CurrencyPair/CASH API with throwaway actual fills before assuming balances. Save proof; no product code before tests.
- [ ] RED hand long/spot cash+inventory/fees/adverse fills, synthetic perpetual consistency, next-open/warmup/prefix invariance, future mutation, OOS train-only selection/non-overlap, deterministic report hashes, metrics zero-trade/bounds.
- [ ] Implement native quote replay with explicit O-L-H-C; no own fill engine. Isolate worker before native import; no sockets/credentials. Run focused native GREEN and required regressions.

### Task3 — durable risk-controlled synthetic automation (integrated source checkpoint2)
**Files:** research/automation.py, storage.py (or focused store modules), scheduler.py; tests/research/test_automation.py, test_storage.py.
**Interfaces:** create_run(id,key,config), command_run(id,key,action), step_run(id,cursor), state_run(id), store report/dataset catalog. New PHASE4_DB_PATH single atomic native-replay/session journal; no writes to original PaperStore, whose16/500limits remain unchanged. Phase4 independently enforces16/500 plus10exit reserve/96bar source. No second DB effect, no unnecessary saga.
- [ ] RED native step/paused/terminal stop, total loss/drawdown/exposure/streak, idempotency/payload conflict, concurrent duplicate cursor, transaction fault rollback/corrupt schema/hash, restart/replay/fundingonce, actual-spot refusal.
- [ ] Implement bounded event stream over existing sandbox_replay; committed cursor decides next step. Native flatten after risk with10events reserved and96bar bounded source; keep losses/deficit. Recovery defaults PAUSED_RECOVERY, explicitresume only. Background loop reads durable running states/due times, expectedcursor and lock avoid duplicate loops; zero transport code. Add native-replay-beforeSQLcommit and response-aftercommit fault tests and pause/stop/inflight races.
- [ ] Run integrated GREEN, wholePython/static; commit/push core before UI, report SHA. CI2 observed once. No claim of UI/real restart until executed.

### Task4 — API, Korean UI and real process/browser acceptance (source checkpoint3)
**Files:** research/api.py; existingapi.py lifespan; shared local-boundary helper if needed; apps/web/src/research/*; App.tsx; tests/research/test_api.py; frontendtests; docs/qa/phase4.
**Interfaces:** fixed local dataset/report/run REST routes; explicit research vs synthetic-PAPER labels; durable unknown intent handling.
- [ ] RED route/Host/Origin/JSON/limits, stale selection/response ID, duplicate/5xx/reload persistence, loading/error recovery, risk statuses and labels.
- [ ] Implement API/scheduler lifespan and separate Korean screen; maintain old dashboard/PAPER. Tests/lint/type/build/full regression GREEN; commit/push, observe CI3 alljob actual logs/API.
- [ ] Guarded data research if available (actualspot only), deterministic repeat/hash/OOS evidence; syntheticfuture research clearly separate/BLOCKEDrealperformance.
- [ ] Chromium PC1440x1000/mobile390x844 research/data/results/auto start→step→pause→resume→stop/risk, no overflow/pageerror, keyboard/labels, screenshots. Actual backend restart same DB/cursor/account/protection/paused state, no duplicate order/funds; compare ledger.
- [ ] Self-review final diff (user forbids agents), reproduce/fix any actual defects RED/GREEN; extra CI only justified. Update ARCHITECTURE/QA_PLAN/WORK_LOG/CLOUD_RECOVERY/Phase4 report with PASS/FAIL/BLOCKED/UNRUN and KST. Raw gh api/job evidence saved; final docs skipCI, PR title/body update, verify local=remote/main/Draft, report exact source/docsSHA.

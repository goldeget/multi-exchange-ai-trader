# Restored Monitoring Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task requires failing tests first, minimal implementation, focused verification and independent review. This plan does not request another human approval for already authorized implementation/review/passing main merge.

**Goal:** Real monitor-only resume of supported restored native state, with fresh opaque PRE/POST evidence, genuine owned private events and all financial paths fenced.

**Base:** `6f8a60cc92379bcdeec46fedaa921b4b2ace48da`; branch `gpt/restored-monitoring-resume-20261102`; checkout `F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102`.

**Spec:** `docs/superpowers/specs/2026-11-02-restored-monitoring-resume-design.md`.

**Stack:** Python3.12, existing SQLite/httpx, pinned nautilus-trader1.231.0 and installed generic pyo3 WS. No dependency/SDK wheel changes.

## Global Constraints

- Preserve original restore/history/v4 backup semantics, original native event constraints, STOPPED/execution blockers and old36+6 strict gates. No actual/DEMO provider/order calls, operator env/secrets/DB/log reads, paid application AI/server spending/deployment. Synthetic stores and credentials only.
- Work only in verified branch/worktree; inspect current status before editing. Do not consume/reset another agent's changes. One writer per task; independent review reads exact diff and evidence.
- No invented readiness/authority Boolean, native lifecycle mock, REST fill creation, silent cap truncation, hidden retry, catch-and-ready, accounting promotion or default economics. The spec permits only explicitly source-backed pinned SDK instrument representation constants (margins0.1, multiplier1, absent max_notional/tick/info), under strict restored equality; these are not actual margin/leverage evidence.
- For each task report exact files and APIs, RED command/failure, GREEN command/result, regression checks, implementation SHA/diff scope, assumptions/unsupported states and unresolved blocker. Counts reflect collected/passed results, never estimated. Redacted fixed error codes only; no private payloads.
- Commit only task-owned files after checks/self-review under root coordination; no blanket git add. The active implementer alone owns product/index during its task; root edits only the ignored ledger then. Root owns remote/main integration. Subagents do not push/merge independently.
- All paths below are relative to the verified checkout. Focused helper modules may be split only within stated responsibility; retain four task headings and report actual file list.

## Review Focus

- Exact prior attestation can expire while genuine callbacks remain valid; fresh authority and event provenance must not be confused (Tasks1/3).
- Economic instrument metadata drift and hidden transport retries must fail before stream admission without overwriting native cache (Tasks1/2).
- Pending duplicate executions and terminal-before-fill ordering must not make inferred or duplicate native events (Task2).
- Native-before-app delivery and concurrent stop/revocation must prevent late ready publication (Tasks2/3).
- Actual SDK shutdown must preserve shared-loop users and retained ownership on incomplete quiescence (Tasks3/4).

### Task 1: Frozen targets, opaque session receipts, bounded reads and pure comparison

**Create**

- `src/multi_exchange_trader/execution/monitoring_models.py`
- `src/multi_exchange_trader/execution/monitoring_receipts.py`
- `src/multi_exchange_trader/execution/monitoring_readback.py`
- `src/multi_exchange_trader/execution/monitoring_reconciliation.py`
- `tests/execution/test_monitoring_receipts.py`
- `tests/execution/test_monitoring_readback.py`
- `tests/execution/test_monitoring_reconciliation.py`

**Modify narrowly**

- `src/multi_exchange_trader/execution/account_binding.py`: issuer registry/generation, exact prior-handle refresh, observation installer, monitoring read/require/consume/discard/revocation.
- `src/multi_exchange_trader/execution/runtime.py`: typed read-only `monitoring_state()` and consistent mutation-sensitive digest/revision.
- `src/multi_exchange_trader/account/bybit_readonly.py`: optional internal physical-attempt/deadline/quota observation hooks; preserve ordinary defaults/return protocol.
- `tests/execution/test_account_binding.py` and existing read-client tests only for affected defaults/regressions.

- [ ] Write RED tests proving fake/copied/pickled/wrong-session/wrong-binding/phase/target/lease/channel/restore-generation handles fail, one exact issuer works once, capacity16 includes pending, and cancellation/reauth/close/key swap/rollback/expiry revoke authority. Public injected transport remains unable to issue production-capable proof. Use boundary-only HTTP interception for real-session positive protocol, not patched require methods.
- [ ] Add frozen/redacted models from spec. Implement worker-issued `MonitoringWitness` and `StreamStamp` interfaces without accepting caller-controlled authenticated flags. `capture_monitoring_target(runtime, assembly, *, restore_generation_id, policy_revision, witness, stream_stamp)` uses actual detached native validation plus full durable ownership second pass; no install/format changes.
- [ ] Implement `runtime.monitoring_state()` with typed app credit/candidate/position/reservation/command/conflict summary and current logical revision/digest. Capture native and app summaries on owner loop; don't read private tuple offsets from matcher.
- [ ] Implement session APIs exactly as spec. `refresh_monitoring_binding(prior_binding, scope=...)` permits an expired exact prior handle through retained attestation, never new self-attested expected UID. Revoke before any auth attempt. `configure_native_monitoring` requires observation binding, permits read-only/no-Capital key, leaves execution installer unchanged.
- [ ] Implement shared `MonitoringReadBudget`: total200 physical attempts/60s; perreceipt32 logical GET/20s/3 attempts; registry16; TTL min(binding maxage,30s). Debit inside read's physical retry loop, including refresh/auth/time. Enforce the independent shorter binding TTL. Hooks attach internally to active operation and release in finally; default ordinary reads remain unchanged. Preserve reviewed quota headers only and fail on exhausted/rate/banned responses, not a new scheduler.
- [ ] RED/GREEN page tests: complete multipage match; page5 with cursor, repeated/empty continuation, failed later page, malformed/wrong-symbol/category/duplicate conflict/time rollback and retry-cap exhaustion all block with no receipt. Each page/session is revalidated before/after await. No partial success.
- [ ] Implement explicit symbol/current-open/position/history reads, positive fresh order createdTime then full no-ID symbol/time execution sweep, max4 symbols/32 orders,5pages/50rows. Optional ID probes remain bounded and cannot replace broad sweep. Cover missing positive terminal history, pre-retention creation, shorter zero-fill availability, native ns exact ms alignment, out-of-window/unknown economic/unowned returned execution rows.
- [ ] Implement pure exact matcher with supported status table, ordinary native MarketGTC→venue MarketIOC exception only, LimitGTC strict economics/TIF, exact original executions and one-way position including explicit zero. App0/native filled positive returns `MATCHED_NATIVE_WITH_UNCREDITED_FILLS` with no state/candidate/credit mutation. Unknown fills/manual/conditional/hedge/reversal/reopen/ambiguous credits fail.
- [ ] Run focused new tests and affected account binding/history receipt/readonly regressions, Ruff/mypy for touched modules. Provide RED/GREEN evidence and exact phase/counter/TTL/error codes to Task2/Task3. Do not claim native Linux startup from unit tests.

### Task 2: Genuine passive Bybit subclass, bounded HTTP adapter and owned raw stream

**Create**

- `src/multi_exchange_trader/execution/native_monitor_client.py`
- `src/multi_exchange_trader/execution/native_monitor_http.py`
- `src/multi_exchange_trader/execution/private_monitor_stream.py`
- `src/multi_exchange_trader/execution/private_monitor_reports.py`
- `tests/execution/test_native_monitor_client.py`
- `tests/execution/test_native_monitor_http.py`
- `tests/execution/test_private_monitor_stream.py`
- `tests/execution/test_private_monitor_reports.py`

**Modify narrowly**

- `src/multi_exchange_trader/execution/native_node.py`: `monitor_only=False` config/factory selection and assembly arm/readiness/barrier/retire APIs, defaults unchanged.
- `src/multi_exchange_trader/account/bybit_readonly.py`: add GET `/v5/account/fee-rate` only; reuse Task1 hooks.
- `tests/execution/test_bybit_native.py`, `test_native_node_linux.py` only if required to prove direct defaults unchanged; do not rewrite counts or weaken assertions.

- [ ] Write RED tests for passive config, constructor inert repayment, public+async financial/query/report denies and final transport allowlists. Assert denial precedes local OrderSubmitted/Rejected, records sticky fault and cannot be swallowed into readiness. Enumerate actual pinned methods including conversion repayment, arbitrary/unknown methods and specialized private/trade connect.
- [ ] Build real `BybitExecutionClient` subclass and dedicated factory, validating config before scheduled tasks run. Implement explicit passive configuration in spec (not merely reconciliation=False). Keep real provider/client/native lifecycles and parent connect/disconnect flag management. Direct builders remain monitor_only=False; no broadening NativeLeverageControl's exact-type checks.
- [ ] Implement monitor-only bounded HTTP adapter. No call to raw Rust `request_instruments` (unbounded pagination + hidden fee fallback). Exact allowed reads route to session GET budget, returning genuine pyo3 CryptoPerpetual/AccountState through public constructors/from_dict; retain real provider.initialize/load_ids_async/conversion and account registration. Deny all unknown/write/report-generation paths. Golden schema tests cover actual pyo3→Cython conversion, exact full semantic fields, original fees/precision/limits/margins/info and real account registration inputs. Missing response fields/currency/fees block; no placeholder balances/0.001 fee fallback. Derivative fee category may be absent; requested symbol is validated. Read source mappings and linked official schemas, not live account data.
- [ ] Ensure original native instrument objects/timestamps/events/indexes remain identical across fresh provider timestamps. Normalize only demonstrated empty info None/{}; changed meaningful info/fees/margins/limits/currencies/increments block before subscription. Provider and transport caches stay distinct from native Cache; no fake data/quote event.
- [ ] Implement owned generic pyo3 transport with max_attempts0, fixed endpoint and epoch/conn_id. One auth + one batch subscribe(order.linear/execution.linear/position.linear), sole-pending ACK handling when req_id absent, exact req_id/topics when present, immediate ping/pong then20s/30s liveness and full mode conjunction. Bound frame1MiB/rows200/queue1024 and handshake10s. Outgoing auth/subscribe/ping/controlpong only, no raw send escape or credential logging.
- [ ] Implement raw prevalidation before SDK cached-field substitution, exact original fields/provenance/durable owner/lifetime, decimal/integer precision and standard execution fees. Fill dedupe covers pending+applied entries. Real report handlers/to_pyo3 or public genuine event-generation seam only after validation; external/mass/status/query reconciliation hard denied. No unsupported pending/conditional/economic updates.
- [ ] Implement serial application with native+app delivery acknowledgment, expected outcome checks and `arm_monitoring(target)`, `wait_monitoring_ready()`, `monitoring_barrier()`, `retire_monitoring(reason)`. Establish ingress at the first Python raw handler before application scheduling, add an owner-loop FIFO fence, and track exact drained watermark; the generic SDK already schedules Rust-to-Python, so never claim Rust-held/not-yet-invoked-frame completeness or an atomic venue cutoff; empty queue/sleep alone cannot pass. Buffer terminal-before-execution5s; never aggregate-to-fill. Position reports observe only. Sticky errors retire epoch before cleanup.
- [ ] Run focused parsing/allowlist/conversion tests; report real SDK signatures/typed fixture conversions and exact remaining Linux integration requirements. No unit fake proves actual lifecycle. Supply Task3 with immutable stamp/fault/readiness/barrier contracts and cleanup ownership. Reuse the exact Task1 operation budget for native startup HTTP; do not create an independent counter.

### Task 3: Worker PRE/start/POST admission, live-change status and cleanup fences

**Create focused helpers if needed**

- `src/multi_exchange_trader/execution/monitoring_resume.py`: worker-owned admission/status controller, keeping worker.py focused
- `src/multi_exchange_trader/execution/native_loop_ownership.py`: versioned shared-loop default-executor ownership preservation
- `tests/execution/test_native_loop_ownership.py`: actual thread/executor ownership behavior

**Modify**

- `src/multi_exchange_trader/execution/worker.py`
- `src/multi_exchange_trader/execution/native_node.py` only for Task2 assembly integration
- `src/multi_exchange_trader/execution/monitoring_models.py` only for immutable real monitoring status/checkpoint fields
- `src/multi_exchange_trader/execution/private_monitor_stream.py` only for deferred M1 owned-task retirement accounting below
- `tests/execution/test_restored_monitoring_worker.py` (create)
- `tests/execution/test_worker_shutdown_admission.py`, `test_worker_lifecycle.py`, `test_worker_restart.py`, `worker_receipt_support.py` only where legitimate affected behavior needs adaptation
- `tests/execution/test_native_restart_linux.py`: only genuine exact-subclass+inheritance assertion compatibility if worker now selects monitor factory; keep6 distinct contracts/coverage intact

- [ ] RED tests: existing restored connect blocker is replaced only by full real resume protocol; flags/data-only receipt cannot unblock. Second resume/stage/accounting/history mutation is fenced across awaits. Existing stopped execution/UNKNOWN/reservation/credit invariants remain.
- [ ] Actual worker calls builder with monitor_only=True before restore. Keep direct defaults unchanged. Add `resume_restored_monitoring() -> WorkerMonitoringStatus`; `connect_monitoring` delegates restored path without nested duplicate admission accounting. Make runtime/assembly/session/binding/channel/lock/policy snapshots exact; no user receipt/boolean bypass parameter.
- [ ] Follow spec: synchronous fence/sharedbudget→refresh→captureA/PRE→guard/purecompare/consume→arm+register actual node.run_async task→real readiness+drain→captureB/POST→drainC→bounded retarget legitimate original deltas. No original event rollback. Callback failures become sticky monitoring fault, not just pause-and-ready.
- [ ] Final synchronous guard/one-shot POST consume/status publication has no await. Check owner/current target/phase/binding TTL/issuer generation/policy/epoch/conn/mode/pong/ingress=drained/no pending and stop/cancel fence. Return immutable redacted readonly status with explicit scope/time/fingerprint/freshness and execution flags false.
- [ ] Genuine postadmission fill continues native/app quarantine processing, stales historical proof and preserves STOPPED. Fault/revocation/disconnect retires current epoch; explicit later admission requires fresh exact refresh/PRE/POST/new socket, never automatic reconnect. Keep binding expiry's fresh-authority limits distinct from original received-event provenance.
- [ ] Stop/cancel/reauth/close/key/client replacement/lease reacquire/owner replacement during every awaited phase must win. Track run/stop/reads before awaits; settle cancellation then propagate. Keep accepted callbacks draining until producer quiescence; preserve valid native effects on failed POST. Cleanup timeout retains stores/channel/lease and never seals/disposes. Safe cleanup seals actual evolved native state, using existing codec restrictions.
- [ ] Set monitor-only TradingNodeConfig.timeout_post_stop=0.05 from pinned kernel stop source; preserve direct default10.0, actual residual check and full delivery/quiescence guards. This latency setting does not prove quiescence.
- [ ] Keep actual shared owner loop alive: await real stop_async/run completion, check disconnected queues/tasks, seal before quiesced dispose_local. Never call TradingNode.dispose on running shared loop. No implicit session close; caller owns session.
- [ ] Preserve shared default-executor ownership around genuine monitor-only node construction before any yield, using the source-backed spec correction. Kernel construction installs its own pool; disposing it must not break unrelated asyncio.to_thread users or cancel their queued work. A focused CPython3.12 ownership helper may restore the previous pool/absent setting, fail closed for unsupported loops, and leave the kernel's explicit executor intact for actual SDK work. Add real thread-work preservation tests and carry actual-node verification into Task4's existing shutdown case, without changing old36/6/new12 counts.
- [ ] Resolve deferred Task2 minor M1 while integrating long-lived task ownership: remove completed control-pong/deferred-completion tasks from the owned active set with a done callback, preserve deterministic close of running tasks and retrieve exceptions into fixed sticky faults. Add focused completion/exception tests; do not silently drop or log raw failures. This is lifecycle resource accounting, not a trigger to repeat Task2's broad review.
- [ ] Run focused worker/shutdown/history/restart regressions and new worker tests; report exact state machine/cleanup statuses and pending Linux proof. No count weakening or passing claim based on test assembly.

### Task 4: Twelve actual isolated Linux contracts, exact CI gate and operator documentation

**Create**

- `tests/execution/test_restored_monitoring_linux.py`
- `tests/execution/restored_monitoring_io_support.py` (synthetic fixtures and I/O-boundary interception only)

**Modify**

- `.github/workflows/ci.yml`: separate new12 execution/XML gate; old36 and restart6 unchanged
- `docs/implementation/NATIVE_WORKER_20261102.md`: actual scope, API, readonly status, lifecycle/failure behavior and qualification limits

- [ ] Write exactly12 separate nonparametrized tests below. Each executes real production builder/node/kernel/engines/provider/session/protocol/monitor client/portfolio/strategy/cache/lifecycle as applicable. No assembly_factory/decoder replacement, manual connected/initialized/running flags, fake `_connect`/`_disconnect`/start/run/stop, patched receipt require, or fixture-generated readiness. HTTP and generic WS construction/send/receive are the only faked I/O boundaries; no raw Bybit socket is used. Fake sends record allowlist/max_attempts0; supply raw JSON ACK/pong/reports, not ready flags.
- [ ] Reuse strict OS isolation before constructing any builder: Linux network namespace, cleared env, REQUIRE_NATIVE_CONTRACTS=1/NATIVE_NETWORK_ISOLATED=1, lo-only and blocked IPv4/IPv6 external probes. Synthetic temporary restore/history/policy stores and dummy credentials only. Intercept at HTTP network boundary so real session signing/UID/receipt code executes; separately injected transport must fail. No product authentication bypass.

| # / exact test function | Required proof |
| --- | --- |
|1 `test_actual_restored_lifecycle_and_one_shot_admission`|Real sealed generation→real restore→PRE/start/ACK/pong/portfolio/native+app barrier/POST/MONITORING. Original native filled state with app0 matches without credit; exact cache/index preservation and genuine real task readiness.|
|2 `test_readonly_binding_without_capital_or_trading_permission`|Real session response readOnly=1/noCapital; observation factory/receipts work, execution binding still fails. Fresh instrument conversion preserves semantic fee metadata/native identity; metadata drift blocks before subscribe.|
|3 `test_forged_stale_phase_and_budget_receipts_cannot_admit`|Forged/copy/foreign phase/session/lease and stale GET-start/binding proof fail. Capacity/retry/read/deadline total200 counts actual attempts including startup. No partial receipt or fake readiness.|
|4 `test_all_financial_and_reconciliation_fences`|Actual monitor client public+async APIs/final HTTP/WS/query/report routes deny; no local submitted/fake terminal/repay/mass reconciliation, no active periodic synth timers; sticky fault.|
|5 `test_ack_pong_modes_and_transport_epoch_are_required`|Missing/negative/duplicate/wrongconn ACK, absent req_id solepending rule, failed topic lists, pong timeout/all modes, unexpected reconnect/late retired callbacks. Only auth/subscribe/ping output; actual run task alone cannot pass.|
|6 `test_original_owned_execution_reaches_native_and_app_quarantine`|Raw private execution on real restored ACCEPTED order→real SDK OrderFilled/Position/indexes→exactly1 app candidate, credit0/UNKNOWN+reservations preserved; no REST/native fabrication.|
|7 `test_pending_applied_duplicates_and_original_field_guards`|Exact duplicate both queued/applied no-op; conflicting execId fault. Wrong original instrument/side/client/venue/account provenance rejected before SDK substitution; precision/fee/foreign/malformed/overflow fail.|
|8 `test_partial_terminal_ordering_never_infers_execution`|Actual partial/full original events and terminal-before-execution within5s apply correct real sequence; missing execution times out without fill inference. Economic update/unsupported pending/conditional/external statuses block.|
|9 `test_pre_post_mutation_and_final_delivery_barrier`|Controlled I/O scheduling at PRE/connect/readiness/POST/final barrier causes exact retarget or failure; native-before-app callback window cannot admit. Three POST cap; failed POST retains genuine native/app effects.|
|10 `test_cancel_stop_reauthentication_and_lease_fences`|Cancel/stop/session revoke/close/owner/lease change at awaited phases; no late admission, receipts revoked, run/read cleanup tracked. Failed quiescence retains resources and callbacks.|
|11 `test_real_shutdown_quiesces_without_closing_shared_loop`|Real stop_async/run-task/queue/repayment/owned socket cleanup; shared loop remains usable, no financial calls, no dangling producers. No TradingNode.dispose on running loop; timeout cannot clean-seal.|
|12 `test_live_fill_stales_proof_fault_requires_new_admission_and_seals`|After ready genuine fill continues monitoring+stales proof/credit0; fault retires epoch, new explicit bounded admission uses new socket/proofs. Quiesced seal/restart preserves exact genuine original events; no checkpoint rollback.|

- [ ] Capture background SDK exceptions and fail tests; spies may fail prohibited reconciliation calls while leaving lifecycle real. Compare actual event histories/index integrity, not only counters/flags. Normal unit parametrization may supplement negative cases but new Linux collection remains exactly12.
- [ ] Run existing strict36 and restart6 commands exactly as CI, then new module separately using the same cleared-network namespace pattern:

```bash
sudo unshare --net -- env -i PATH="$PATH" LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" LANG=C.UTF-8 PYTHONPATH="$PWD/src" REQUIRE_NATIVE_CONTRACTS=1 NATIVE_NETWORK_ISOLATED=1 OPENBLAS_NUM_THREADS=1 "$PWD/.venv/bin/python" -m pytest -q tests/execution/test_restored_monitoring_linux.py --junitxml=restored-monitoring-contracts.xml
```

- [ ] New XML gate checks total tests=12, testcase count=12, failure+error+skip=0, every classname ends exactly test_restored_monitoring_linux, all12 exact unique function names above, no duplicate/parametrized substitutes. Existing36/6 remain distinct unchanged gates. Archive/report XML under root's evidence convention; do not claim old count proves new feature.
- [ ] Run `uv run pytest -q`, `uv run ruff check src tests scripts`, `uv run mypy src scripts` and required existing CI checks on final reviewed SHA. A Windows native skip is not a native pass. Record local/isolated/hosted/fullCI separately; independent review addresses actual diff and evidence before authorized main merge.
- [ ] Update real operator doc path with supported stable scope, read-only key handling, response limitations/blocked states, exact API/status, no auto financial activity/reconnect/credit, stale proof after genuine live fill, cleanup retry semantics, twelve-test evidence and explicit no real Bybit acceptance/whole-account/event-complete/valuation/production/trading claim.

## Completion report

Root receives exact final SHA/files, tests and XML counts36+6+12 separately, independent review fixes, required CI status and any unresolved blocker. Merge only the reviewed passing authorized change; preserve backup/restore/history work and STOPPED/execution flags. No observed no-network result is promoted to live account or financial qualification.


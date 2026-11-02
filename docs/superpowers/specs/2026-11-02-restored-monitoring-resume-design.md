# Restored monitoring resume — design

Date: 2026-11-02. Base `6f8a60cc92379bcdeec46fedaa921b4b2ace48da`, tree `17db720d1be199498962be24d71802e2753eaa8f`; branch `gpt/restored-monitoring-resume-20261102`; checkout `F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102`.

Status: finalized for authorized implementation, review, verification and passing main merge. This is design, not a claimed implementation/Linux lifecycle/exchange acceptance pass. Preserve completed original native restoration, history accounting, v4 backup and old strict36+restart6 contracts.

## Outcome and limits

`await worker.resume_restored_monitoring()` must actually run safe native monitoring after fresh session-issued PRE reconciliation, real passive lifecycle startup, owned private ACK/liveness readiness, delivery barrier, fresh POST/current-target reconciliation and one-shot synchronous admission. Choose **B: genuine owned live event processing**. Original private executions update real native Order/Position and reach the existing app quarantine path. REST never generates native events, credits fills, acknowledges UNKNOWN, releases reservations, infers terminal/flat states, or overwrites cache. Genuine non-fill callbacks retain existing guarded ACK rules. All execution qualification flags remain false, runtime inactive and desired policy STOPPED. Read-only keys without Capital/trading permission use observation authority.

Use synthetic temporary stores/credentials and OS-network-isolated Linux contracts only. No actual/DEMO account/provider/order calls, operator env/secret/DB/log reads, paid application AI, new dependencies/server spending/deployment. No new approval stall is required for the authorized implementation/review/merge. Preserve originals and worktree ownership.

Evidence: six files under `F:/자동매매프로그램/handoff-drive-20261102/`: `RESTORED_MONITORING_ARCHITECTURE_AUDIT.md`, `architecture-choice.md`, `RESTORED_MONITORING_SDK_AUDIT.md`, `RESTORED_MONITORING_SDK_LIFECYCLE_ADDENDUM.md`, `OWNED_PRIVATE_MONITOR_TRANSPORT_AUDIT.md`, `BYBIT_MONITORING_READ_CONTRACT_AUDIT.md`. Current application and pinned1.231.0 source remain authority. The owned transport audit supersedes the specialized-binding hidden-reconnect blocker; architecture-choice supersedes observer-only recommendations.

## Concrete bounds

These local policy caps do not promise provider completeness. The smallest applicable cap wins; exceeding one blocks, never truncates to success.

| Boundary | Default |
| --- | --- |
| Input | 1–4 configured DEMO linear symbols, at most32 owned orders |
| Registry | 16 pending+issued receipts; expire old slots, never evict live authority |
| Receipt freshness | GET-start wall and monotonic TTL=min(binding max_age,30s); current binding TTL independently wins |
| Receipt reader | 20s deadline;32 logical GETs; at most3 physical attempts per GET |
| Whole resume | 60s admission deadline;200 actual HTTP attempts across refresh/PRE/native startup/POST; at most3 complete POST cycles, initial plus2 retargets |
| Pagination | 5 pages per endpoint/symbol/window; limit50; nonempty cursor at cap fails |
| History | DEMO creation age and execution window <=7days; shorter zero-fill terminal availability applies |
| Stream | 1MiB frame,200 topic rows/frame,1024 total queued/pending entries |
| Handshake | dial/auth/subscription/first pong <=10s and remaining resume deadline |
| Liveness | immediate first ping, then20s; last valid pong <=30s; watchdog <=1s |
| Ordering | terminal aggregate waits at most5s for actual original executions |
| Cleanup | bounded10s attempt; failure retains resources and requires retry-stop, never clean-seal success |

A shared internal `MonitoringReadBudget` debits before every physical HTTP send, including retries, and enforces remaining deadlines. Add optional internal attempt/deadline/response-quota hooks to `account/bybit_readonly.py`, ordinary defaults unchanged. The existing retry loop is inside `read`; counting calls to read is insufficient. Preserve only reviewed quota headers; stop on rate-limit/ban/exhaustion, without a new general rate scheduler. Cleanup can outlast the admission deadline solely to establish quiescence or report retained resources.

## Models, target and authority

Create frozen/redacted `MonitoringTarget`, `MonitoringObservation`, `MonitoringAssessment`, `MonitoringStatus`, `NativeMonitoringSummary`, `ApplicationMonitoringSummary`, `MonitoringWitness`, `StreamStamp` in `monitoring_models.py`. Public data/fingerprints are not capabilities; no live authority is serialized or backed up.

`capture_monitoring_target(runtime, assembly, *, restore_generation_id, policy_revision, witness, stream_stamp)` in `monitoring_reconciliation.py` runs synchronously on the owner loop. Worker-issued internal witness binds exact worker/runtime/assembly/session/channel/lock objects, acquisition ID, loop, restore generation, policy/config and symbol set. Receipt slots bind identity as well as canonical fingerprint. Capture current detached native payload via existing `capture_native_cache`/`prepare_native_cache`, including durable ownership's validated venue-ID second pass; never reinstall cache/reconsume a seal. Require genuine native types/events/index integrity and fully represented owned orders; missing/never-sent/PENDING orders block.

Add guarded read-only `runtime.monitoring_state()`: canonical owned requests, command states/reservations, app position/credited quantities, credit/candidate identities/conflicts and logical revision/digest spanning journal/runtime/history. Matcher must not inspect private projection tuple offsets. Existing logical digest machinery is acceptable; no broad schema migration. Relevant native/accounting mutations invalidate targets.

Create opaque `AuthenticatedMonitoringReceipt` in `monitoring_receipts.py` with constructor/copy/deepcopy/pickle rejection and frozen redacted view. Session-owned registry/issuer generation adds:

```python
await session.refresh_monitoring_binding(prior_binding, *, scope)
session.configure_native_monitoring(binding, *, scope, install)
await session.read_monitoring_receipt(binding, *, target, phase)
session.require_monitoring_receipt(receipt, *, binding, target, phase)
session.consume_monitoring_receipt(receipt, *, binding, target, phase)
session.discard_monitoring_receipt(receipt)
```

`phase` is exactly PRE or POST. Budget attaches internally to the exact active operation. Refresh accepts only previously attested exact handle/current credential owner/DEMO scope, using saved independently supplied UID/parent/master expectations. An expired exact handle may refresh; copied/replaced/foreign handles cannot. Authentication attempt revokes old receipts before I/O. Close, failed auth, key/client replacement, identity mismatch, wall/mono rollback or invalid clocks revoke pending and issued capabilities. Cancellation releases slots. `configure_native_monitoring` uses `require_observation_binding`; never weaken execution binding or exact-type financial controls.

Require current exact binding/session generation, trusted non-injected transport, scope/phase/owner/target/stream stamp and GET-start plus every response timestamp on issue/require/consume. No constructed snapshot/authenticated Boolean/proof view is authority. PRE cannot authorize POST; PRE uses the pre-start stamp, while POST binds the actual current connected epoch/conn_id/watermarks after lifecycle startup. Never require the PRE stamp to remain equal after intentional real connection. Consume removes exact slot once synchronously; discard always works in finally. Strict existing nonfuture timestamp policy remains unless measured bounded offset handling is explicitly implemented/tested; no silent skew or TTL relaxation. Event creation time differs from fresh response time.

## Read contract and pure match

`monitoring_readback.py` uses only exact session GET client, fixed `https://api-demo.bybit.com`, explicit configured symbol/category=linear. Guard identity/lease/cancel/deadline before and after every await. Validate all pages/rows/cursors, preserving integer source/local clocks and exact Decimal economics. Missing continuation, repeated cursor, conflicting duplicates, wrong scope, unknown economic type or incomplete shape blocks.

1. Read time, positions and broad current orders: `/v5/position/list`, `/v5/order/realtime` with openOnly=0 and no orderFilter. Include unsupported/manual/conditional rows in evaluation; do not silently filter them away.
2. Broad retained `/v5/order/history` with explicit bounded times supplies positive terminal rows. Every native order needs fresh positive row joined locally by both client+venue IDs, symbol and immutable shape. Optional bounded ID probes never replace broad open sweep or assume provider AND semantics. Retain venue createdTime/updatedTime.
3. Full execution lower bound is earliest positively matched fresh **venue createdTime**, not local native ts_init or last fill. Require actual creation age and window <=7days; fixed end; optional1ms overlap must remain inside retention. `/v5/execution/list` uses symbol/startTime/endTime, **no order-ID filters**. Validate every returned row before local dual-ID joins; exact full original fill set/cumulative totals required.
4. Require an explicit valid one-way positionIdx=0 row, including zero. Empty/omitted row never means flat. Match actual native netting lifetime, not arbitrary allocation across app owners.

`compare_restored_state(target, observation)` is pure: no I/O, event/credit/history reduction or cache mutation. Require exact account/environment/symbol/IDs/side/type/quantity/reduce-only/ordinary classification/limit price/TIF/status/cumulative/leaves. Native original fill nanoseconds must be exactly ms-aligned and equal integer execTime_ms*1_000_000. No float rounding. Preserve exact execution ID/fee/currency/liquidity/Trade type and relevant seq; seq is not globally unique.

| Native / venue | Stable admission |
| --- | --- |
| ACCEPTED / New | Positive current row; zero cumulative, full leaves |
| PARTIALLY_FILLED / PartiallyFilled | Positive current row and exact original fill chain/totals |
| FILLED / Filled | Positive terminal row and full original execution set |
| CANCELED / Cancelled | Positive terminal row, exact fills including partial-before-cancel |
| REJECTED / Rejected | Positive retained row and original native rejection |
| INITIALIZED/SUBMITTED-only/pending update/cancel/DENIED; unsupported EXPIRED mapping; Untriggered/Triggered/Deactivated/spot PartiallyFilledCanceled | Explicit unsupported; never approximate absence or lifecycle |

Only ordinary native Market/GTC from current factory→Bybit Market/IOC normalization is allowed (supported original Market/IOC also exact). Native Market has no limit price. Limit/GTC requires exact price and TIF. No generic GTC==IOC, conditional/post-only/economic update exception. This directional normalization is supported by pinned factory/HTTP mapping and [Bybit order semantics](https://bybit-exchange.github.io/docs/v5/order/create-order).

`MATCHED_NATIVE_WITH_UNCREDITED_FILLS` permits native filled Order+Position while app quantity remains0 only when every difference has exact original uncredited fill provenance and all existing credits/candidates are compatible. No REST candidate/ACK/credit/reservation/flat mutation. Unknown execution, conflicting fee/ID, unowned open order, extra/hedged position, ambiguous ownership, split/reversal/reopen/adjustment fails even if totals match.

Retention and scope limitations: [DEMO7days](https://bybit-exchange.github.io/docs/v5/demo), [history and zero-fill availability](https://bybit-exchange.github.io/docs/v5/order/order-list), [execution filters](https://bybit-exchange.github.io/docs/v5/order/execution), [explicit position rows](https://bybit-exchange.github.io/docs/v5/position). No cross-endpoint atomicity, complete historical-event replay, whole-account cleanliness, global flatness or valuation claim.

## Actual lifecycle, bounded HTTP adapter and financial fences

`native_monitor_client.py` defines genuine `BybitExecutionClient` subclass/factory. Add `monitor_only=False` to `build_node_config`/`build_native_node`, preserving direct defaults; actual worker selects True before hydration. Do not swap classes after restore. Update old restart helper's exact base-class assertion only to exact monitor-subclass plus genuine Bybit inheritance; preserve all other assertions and36+6 counts.

Explicit passive config: cache database=None/flush_on_start=False; load_cache=False/reconciliation=False; inflight_check_interval_ms=0; open_check_interval_secs/position_check_interval_secs/own_books_audit_interval_secs=None; filter_unclaimed_external_orders/filter_position_reports=True; generate_missing_orders/manage_own_order_books=False; snapshot_orders/snapshot_positions=False; snapshot_positions_interval_secs=None; all purge intervals=None/purge_from_database=False; external_clients=None/save_state=False. No data clients/external bus streams/actors/algorithms. Strategy manage_stop/manage_gtd_expiry/manage_contingent_orders=False/external_order_claims=None. Reject unsupported emulated/contingent/algorithm/pending cache before run.

Before constructor tasks execute, forbid account configuration and auto_repay_spot_borrows=True; override constructor-scheduled repayment coroutine as inert and settle it on disconnect. Deny synchronous public AND async submit/list/modify/cancel/batch/all, QueryAccount/QueryOrder, leverage/mode, borrow/repay/conversion, account writes, report generators, `_send_*_report` and mass/direct reconciliation. Denial records sticky fault before raising, before any local submitted/fake terminal event. Guard all final transport methods default-deny, including unknown future methods.

**Bounded HTTP ruling:** pinned Rust request_instruments internally fetches fees then has an unbounded cursor loop and possible fee fallback. Neither method count nor zero retries proves total200. Add `native_monitor_http.py`: monitor-only adapter routing actual allowed HTTP through the bounded session reader, returning real pyo3 types via public constructors/from_dict. Keep actual `BybitInstrumentProvider.initialize/load_ids_async`, pyo3-to-Cython conversion, `_update_account_state`, `_await_account_registered` and portfolio lifecycle. No raw Rust high-level HTTP escape, mutable transport, SDK patch or lifecycle mock.

Adapter exposes only request_instruments(exact configured linear symbol), request_account_state(exact UNIFIED/native account) and reviewed local cache/settings/masked-key/close/cancel-requests operations. Reads: instruments-info, fee-rate, wallet-balance, existing time/identity. Add only `/v5/account/fee-rate` to current GET allowlist. Convert fresh full linear-perpetual instrument semantics into actual pyo3 CryptoPerpetual and wallet state into actual AccountState, matching pinned SDK field mapping; test golden real-type conversion. Missing/unsupported fields/economics fail, never defaults. A general derivative fee response may omit category, so validate request+symbol instead of inventing a required response category. Missing wallet currency is not fabricated zero. [Fee schema](https://bybit-exchange.github.io/docs/v5/account/fee-rate), [instrument schema](https://bybit-exchange.github.io/docs/v5/market/instrument), [wallet schema](https://bybit-exchange.github.io/docs/v5/account/wallet-balance), [pinned internal HTTP behavior](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/crates/adapters/bybit/src/http/client.rs#L3208).

Fresh transport/provider metadata must match restored native instrument semantics: type/ID/raw symbol/currencies/precision/increments/limits/lot/multiplier/inverse/margins/fees/tick/info. Exclude only versioned acquisition timestamps; only documented empty info None/{} normalization. Preserve native object/timestamps; do not publish instrument events or overwrite Cache.add_instrument. Meaningful fee metadata must remain comparable; no fixture-specific/default economics. Narrow pinned-1.231.0 representation exception: linear instrument margin_init and margin_maint are explicitly Decimal(0.1), multiplier=1, max_notional/tick scheme/info absent, matching the tagged SDK mapping; lot_size equals the exact size increment. These constants are representation compatibility, never actual venue/account risk-margin or leverage evidence. Fresh API-supported fees/balances/limits remain mandatory; no fallback fees or fabricated balances. Compare full currency registry metadata, normalize only the demonstrated tick_scheme/tick_scheme_name structural alias and empty info, and preserve every original native object/timestamp.

Monitor `_connect` executes real provider initialize/semantic cache validation/account read+registration then owned stream. Never call specialized Bybit `_connect`; private/trade constructor-held sockets remain guarded/unconnected. `_disconnect` retires owned epoch, cancels/awaits owned tasks/socket, then actual superclass local cleanup. Keep inherited public connect/disconnect flags real. Assembly APIs: `arm_monitoring(target)`, `wait_monitoring_ready()`, `monitoring_barrier()`, `retire_monitoring(reason)`.

## Owned raw stream and genuine event path

`private_monitor_stream.py` owns installed generic pyo3 WebSocketClient with reconnect_max_attempts=0, heartbeat=None, fixed `wss://stream-demo.bybit.com/v5/private`. New object/epoch for each explicit connect; no hidden redial. Readiness requires full mode conjunction (active AND not reconnecting/disconnecting/closed), auth/sub ACK, matching first private pong (`op=pong`, matching req_id/conn_id and one timestamp-string arg; no invented success flag and no public `op=ping` pong substitute), actual engines/client/trader/strategy/portfolio readiness, no sticky faults and delivery barrier. No active flag/subscription count/task-exists substitute. Missing quotes leave valuation unknown.

Exactly one auth and one sole batch subscription to order.linear/execution.linear/position.linear. Require success=true/nonempty consistent conn_id, exact req_id when present; missing req_id only consumes sole pending command. If success/failure topic lists exist require exact topics/no failures. Reject duplicate/negative/unexpected ACK; buffer early topics without treating them as ACK. Outgoing schema allowlist immediately before send_text: exact generated auth in AUTH_PENDING, fixed subscribe in SUB_PENDING, expected JSON ping; bounded control pong only. Never expose arbitrary text/binary/order methods or log key/signature/auth packets; TRACE disabled. Unexpected post_reconnection triggers thread-safe sticky fault. Check modes on every dispatch/readiness/final guard; watchdog handles terminal mode. Retire before cleanup; old callbacks never restore readiness.

`private_monitor_reports.py` rejects duplicate JSON keys/nonfinite/oversize/malformed data, retains original fields and provenance, and validates **before** SDK handlers substitute cached instrument/side. Require exact live epoch/session/account scope, category/symbol, both IDs, native instrument/side/trader/strategy/owned lifetime/economics. Execution payload lacks positionIdx: rely on proven one-way binding/native mapping, never invent raw field. Official ordinary execution markers may be empty or `UNKNOWN` stopOrderType, qualified by explicit CreateByUser and no additional fees/conditional/block-trade economics. A Market execution row may contain a nonnegative protective IOC `orderPrice`; that field is not a native Market limit price and must never create one. Actual execPrice, raw immutable IDs/type/quantity/side and exact execution economics remain mandatory. This execution-stream distinction does not relax the separate REST matcher or generic TIF rules. Construct actual FillReport/OrderStatusReport/to_pyo3 only after validation; private callback is the sole origin, not a public report injection API.

Exact Trade execId/qty/price/fee/currency/liquidity/time, no overfill, supported position continuation. Dedupe against applied native fills AND accepted-but-pending IDs. Identical duplicate=no-op; same ID/different economics=sticky fault. Supported new executions start from ACCEPTED/PARTIALLY_FILLED. Same-state Accepted/terminal duplicates observe only. Genuine Cancelled may advance accepted/partial only with complete applied original cumulative fills. Other Accepted/Rejected/Expired transitions require explicitly supported original predecessors; no synthetic predecessor. Filled/Partial aggregate never generates missing execution. Terminal ahead of executions waits5s then faults if unresolved; genuine executions already applied survive failure. Positions observe only; economic OrderUpdated, conditional/pending/external/blank IDs, unsupported adjustment, malformed/overflow/application error fault before unsafe mutation.

One bounded serial owner queue applies real SDK native events and verifies expected actual event identity/count/economics and app callback outcome, including swallowed SDK errors. Ingress sequence is assigned at the first Python raw-handler action, before any application scheduling. The generic SDK has already scheduled that handler from Rust; the barrier includes an explicit owner-loop FIFO fence for observed callbacks and cannot attest Rust-held/not-yet-invoked frames or an atomic venue cutoff; native posted/app-consumed acknowledgements track delivery. Drained watermark advances only after native AND app consumption. Barrier drains captured watermark and pending statuses, then recaptures current ingress. Queue-empty or sleep(0) alone is not proof. Final synchronous check requires ingress=drained/no pending, current epoch/conn/ACK/mode/pong and exact app/native target.

## PRE/start/POST admission and shutdown

1. One resume fence and `_admitted` cover all reads/start/cleanup before first await. Refuse concurrent stage/accounting/history mutation/second resume. Capture exact owners/lease/policy/shared budgets; pause entries; RECONCILING; refresh exact prior binding.
2. Capture A, read PRE, recheck current target/owners and compare. Consume PRE synchronously only to start this lifecycle. Mismatch never connects.
3. Arm queue/generation and register actual node.run_async task as stop-owned before startup await; CONNECTING. Await real readiness and native/app barrier. Genuine original events may evolve state independently of receipts.
4. Capture B, read POST, barrier, capture C. Legitimate provenance-backed deltas discard POST and retarget within3 cycles/remaining caps. Unexplained mutation/sticky fault fails immediately; no rollback to A.
5. Without await, final guard checks stop/cancel/owners/lease/session generation/binding TTL/phase/receipt clocks/policy/epoch/modes/ACK/pong/watermarks/current target; pure-match current POST, consume once and publish MONITORING together. Return immutable status with trading_enabled=False/production_supported=False/venue_event_complete=False.
6. Normal later genuine fill continues monitor, marks last reconciliation fingerprint stale and exposes current revision; it is not a permanent always-blocked observer. Revocation/key/owner mismatch, disconnect/reconnect/liveness/parser/queue fault retires admission and requires fresh explicit resume/new epoch. Binding expiry cannot authorize new receipts/admission; existing genuine callback provenance is distinct from fresh REST permission, never financial authority. No automatic reconnect/reauth.
7. Failure/cancel becomes nonready before cleanup; discard receipts, settle read/start/run/stop tasks and drain accepted callbacks. Stop sticky fence wins every await. transport_lost preserves UNKNOWN liabilities and original effects. Retain lease/stores/channel on unproven quiescence; no clean seal/dispose. After real stop, existing seal captures actual updated native/app state. No lost or rollbacked genuine fills.

Use existing quiesced `NativeNodeAssembly.dispose_local` kernel/executor cleanup, never TradingNode.dispose on the running shared loop. Real tasks/queues must settle; cleanup failure is explicit ATTENTION with retained resources.

## Acceptance

Exactly four implementation tasks; separate new `tests/execution/test_restored_monitoring_linux.py` with12 nonparametrized tests, exact collection/pass12, zero skip/error/failure and exact module identity. Keep prior36 and restart6 gates separate. Real builder/node/kernel/engines/provider/session/client/portfolio/strategy/cache/run/stop; fake ONLY HTTP/genericWS I/O, not lifecycle/authority methods or flags. Positive tests intercept network boundary without setting transport_injected=False or monkeypatching require; separately prove public injected transport rejected. No assembly_factory/decoder bypass.

Focused checks + strict36 + restart6 + new12 + required CI on exact reviewed SHA, evidence commands/XML/module counts and independent review. Windows skip/local partial/hosted one-job/fullCI/provider acceptance are distinct. No whole-account, event-complete, valuation or production/trading readiness claim.



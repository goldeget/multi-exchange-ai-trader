# Native worker and monitor-only process

This implementation wires Nautilus Trader **1.231.0** into the existing journal,
runtime projection store, account-session binding, risk callback, history reader,
protection readback and native leverage controls. It does not qualify automatic
execution or provision/run a server. No real keys, accounts, databases, orders or
paid AI generation were used during implementation.

## Concrete lifecycle

`execution/native_node.py` lazily imports the pinned Linux wheel. The exact
credential-owning `BybitDemoAccountSession.configure_native_monitoring` callback
delivers the DEMO/LINEAR config to an actual `TradingNode`; its registered
factory constructs the genuine `MonitoringBybitExecutionClient` subclass, with
financial entrypoints and final transports denied. The registered actual `Strategy` supplies its real order factory/cache
to `NativeBybitBridge`. Construction/build does not connect the node.

The provider has explicit instruments, no load-all default. The strategy uses
NETTING with no external-order claims and no contingent/GTD/stop management.
Worker construction disables native reconciliation and cache reloading. The
standalone non-restart config retains unclaimed-order/position-report filters
and disables missing-order generation; that setting alone cannot prevent inferred
fills. Connect configuration has no leverage, margin-mode
or position-mode mutation and disables spot borrow repayment. ONE_WAY is still
independently required by risk/readback; NETTING alone does not prove venue mode.

`ExecutionWorker.start_passive` acquires the existing account/environment lease,
creates the existing journal and runtime-state handles on the running worker loop,
and validates/restores the sealed local native generation before recovering
ownership/unknown commands without sending. `resume_restored_monitoring()` is a
separate explicit, bounded PRE/start/POST admission; `connect_monitoring()` delegates
to that path for restored generations. Fresh-cache connection is refused with
`MONITORING_RESTORED_CACHE_REQUIRED`. Neither call activates runtime trading.
An independent subscription to the exact `events.order.<strategy-id>` message-bus
topic is the sole worker callback route; the Strategy retains its default no-op
order callback. The subscription stays alive when the native Strategy enters
STOPPING or STOPPED, while the pinned kernel disconnects clients and drains its
engine queues. It checks exact event types and trader/strategy identities. Native
callbacks use `call_soon_threadsafe` before touching SQLite, fenced by the
exact immutable venue/UID/environment/native-label/channel-generation object.
Expired account metadata blocks new staging/read requests but does not invalidate
callbacks from that already-bound channel. Old/copy channels and unknown orders
are rejected; native event decoding still verifies exact persisted ownership.

The worker stages FULL_AUTO only from explicit user symbol/strategy selections and
an immutable trusted plan registry. ALL_IN_ONE uses the existing durable
`AIDispatcher`, which can select candidate IDs only. Provider dispatch runs on a
separate thread to preserve fill processing; policy revision, complete risk policy,
scope, registry revision and evidence expiry are rechecked after the await. A
selection never activates trading. The first shutdown request closes admission to
new worker selection, control, connection and read operations before any await.
If a previously admitted async operation is still running, shutdown refuses with
`AWAIT_INFLIGHT_SELECTION_BEFORE_STOP` or `AWAIT_INFLIGHT_OPERATION_BEFORE_STOP`:
entries remain paused, new work stays blocked, and the callback sink, SQLite stores
and account lease remain owned. The caller awaits that operation and explicitly
retries stop. Repeated cancellation of paid dispatch or native leverage callers
cannot abandon their protected effect and liability-accounting task; cancellation
propagates only after it settles. Ambiguous native outcomes retain UNKNOWN records
and reservations under the original owner. Synchronous runtime cancellation has no
await boundary; it does not require a separate asynchronous lifetime counter.

After admitted work has completed, shutdown stops the producer, awaits the
native run task, and verifies native disconnection and empty execution queues.
Only then does it unsubscribe the channel, drain posted callbacks, mark in-flight
commands unknown, publish the matching clean seal, dispose the native assembly,
and close SQLite/relinquish the lease on its owner loop. A native
stop failure or unproven drain keeps the sink, stores and lease alive for an explicit
retry, and marks unacknowledged handoffs unknown. Abrupt termination leaves an
active generation blocked; arbitrary-crash native recovery and independent venue
reconciliation remain unimplemented gates.
Concurrent stop attempts are rejected while one native shutdown is running;
successful repeated stop on the owner loop is idempotent. Failed or cancelled
shutdown never reopens admission automatically. Native callbacks retain their
separate admission path until transport quiescence and drain are established.

## Dormant headless entrypoint

The default command performs only a dry check and prints the fixed qualification
blockers; it reads no credentials or files and makes no network calls:

```text
python -m multi_exchange_trader.execution.worker_cli
```

An explicitly invoked `--run-monitor` requires `--uid`, `--parent-uid`,
`--is-master true|false`, repeated `--symbol`, `--directory` and `--policy-db`.
Only that path reads `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET` from the process
environment and performs UID authentication GETs. It creates passive runtime
state. Passive mode periodically refreshes same-session identity and polls
owned-position readback. Adding `--connect-monitoring` requests a native read/stream connection;
a supported sealed restored generation must pass the same fresh PRE/start/POST
admission described below before monitoring becomes ready.
Connected mode supervises the admitted stream's status and node task without
periodic reauthentication or REST position polling. Fresh REST authority naturally
expires while exact original-event provenance remains valid. A retired status or
ended native task fails closed through the orderly worker stop path; there is no
automatic reconnect, reauthentication or new admission. Fresh REST polling needs
a separate explicit workflow. The new Linux case12 exercises this public
supervisor with an actual new worker and the same two isolated I/O boundaries.
The process has an empty plan registry and a risk producer which
always raises a monitor-only blocker. It has no automatic entry generation,
strategy import path, paid AI dispatch, web port, or policy activation path.
SIGTERM/SIGINT handlers are installed before startup and reinstalled immediately
after native construction, which otherwise replaces them. Both passive and connected
monitor modes set the same owner-loop stop event. Provider bodies,
native configs and credential values never appear in CLI errors.

`deploy/linux/trader-worker.service` is an **uninstalled, inactive template**.
It refers to a separate root-owned mode-0600 `/etc/trader/monitor-worker.env`
containing explicit identity/symbol values and credentials, and existing private
state directories. No template was installed, enabled or started. It provides no
public API; the dashboard remains separately restricted to loopback. Running a
monitor requires a deliberately prepared Linux host and explicit operator action.

## State layout and operator recovery

The worker directory is the same private state root used by the application:
`/var/lib/trader/data` in the inactive service template. It contains the canonical
`execution.sqlite3` command journal and `runtime-state.sqlite3` owned-state store,
alongside `runtime-policy.sqlite3`, the six application ledgers and `datasets/`.
When paid dispatch is configured, its `ai-dispatch.sqlite3` also belongs in this
root. A monitor does not manufacture a dispatch database or the six app ledgers.
The backup layout also requires an explicit separate `--lock-directory`, set to
`/var/lib/trader/locks` by the service template; its `StateDirectory` declaration
creates that private directory when an operator installs/starts the service.
The default API/CLI behavior still keeps leases in the worker directory for existing
callers, which is not a backup-compatible layout. Existing lease files are never
unlinked automatically. Every worker for the same account/environment on the host
must use the same designated lock directory, including after a restore; choosing
another lock directory while an old writer is alive defeats the local lease.
Lease files are local synchronization metadata, not a recovery proof or snapshot
payload. No backup inventory allowlist was widened for them.

For offline backup, the operator stops **all** writers, including the worker,
application, collectors and scheduled jobs, and keeps them stopped. The source
passed to `scripts/operations/state_snapshot.py snapshot` is this whole common
root, not a separate execution subdirectory. Preserve the execution journal and
runtime-state store as one generation with the policy and other ledgers. See the
[offline backup and restore procedure](../operations/LINUX_DEPLOYMENT_KO.md) for
manifest verification, new-target restoration and the complete-runtime requirement.

History generations add `history.sqlite3` to this same root. The portable recovery
tool consists of `state_snapshot.py` and its siblings `history_snapshot_validation.py`
and `native_restart_format.py`; all run with Python 3.12's standard library,
without installing the application. Both siblings resolve relative to the script,
including invocation from another
working directory. Source databases are never opened with SQLite: only disposable
copies are checked, including committed WAL, before publication.

| Inventory | Manifest | Explicit requirement |
| --- | --- | --- |
| Six base ledgers and `datasets/` | v1 | Existing base requirement |
| Base plus present original runtime stores | v2 | `--require-runtime` requires all original four |
| Base plus history and paired execution/runtime-state stores | v3 | `--require-history` requires this triple |
| Base plus all original runtime stores and history | v3 | Both flags together |
| Base plus all four runtime stores, history and sealed native file | v4 | `--require-native`; native presence always enforces this completeness |

The original four remain `execution.sqlite3`, `runtime-state.sqlite3`,
`runtime-policy.sqlite3`, and `ai-dispatch.sqlite3`. A legacy history backup without
native data does not require paid-AI dispatch or policy files. If present, they
are preserved. A native-bearing backup always requires all four, even when paid
dispatch is unused; the backup tool refuses a missing store and never creates a
placeholder to hide incompleteness. Preparing a complete application state root
is a separate operator/application setup gate.
History always requires both paired stores even without a flag. Version 3 has
exact base manifest fields plus `runtime_files` and
`history_files: ["history.sqlite3"]`; v1/v2 cannot contain or claim history.
Genuine v1/v2 generations and partial policy-only backups remain readable.
All three subcommands support the runtime, history and native requirement flags.
Missing required history is refused;
restore never creates a replacement empty database or restores history alone.

History validation checks exact schema v1, column types/order, full BINARY keys,
foreign keys, scope CHECK, canonical append-only triggers, bounded canonical
payloads and hashes, original membership/provenance, and every deterministic
conflict pair. All account/environment rows in both paired stores must match the
single persisted history scope; empty paired stores supply no contrary scope.
Unknown files, lock files, views and triggers remain rejected. Hashes and this
scope check establish capture integrity, not historical authenticity against an
actor independently mixing same-scope files and recomputing the manifest.

## Bounded durable history

`HistoryStore.ingest` commits complete typed snapshots, immutable variants and
conflict pairs atomically. `coverage(symbol, start_ms=..., end_ms=...)` and
`load_window(...)` preserve original provenance through restart and offline restore.
The worker integration contract is explicit
`await worker.collect_history(symbol, start_ms=..., end_ms=...)`, returning an
ingestion receipt; `read_history` stays read-only. The worker opens the scoped
history store on its owner loop. Collection rechecks the exact runtime, lease
acquisition, session/binding and requested scope/window after GET, before any
history write. A requested stop rejects the result while retaining ownership
until the admitted operation ends and stop is retried. Portable tests exercise
populated v3 recovery a day later without refreshing observations or activating
orders; actual venue and deployment qualification remain separate.
No automatic first-history date, polling interval, startup collection flag or
midnight balance is chosen.

Query windows are inclusive and bounded to seven days. Execution/order coverage
is symbol-specific; transaction coverage is account-wide, including transfers
with empty symbols and activity in other symbols. Overlapping/adjacent successful
queries compose into covered ranges and explicit gaps. A completed empty query
counts as query coverage. A failed/cancelled collection advances no coverage.
Account transactions appear once per distinct variant. Later ordinary order
updates remain observations; identity changes, equal-update collisions and true
venue-time fill regressions remain explicit conflicts, including older updates
received later. Conflicts discovered later also taint earlier relevant coverage.

`query_complete` and `last_query_end_ms` describe successful queries, not arrival
of every venue event. Contradictions and gaps are independent. Orders are filtered
by creation time and do not prove absence of older open orders. Views retain all
conflicting variants rather than choosing a winner. Neither load nor restore
refreshes observation timestamps, turns a union into `HistorySnapshot`, or grants
runtime/risk authority. `venue_event_complete`, `flat_verified` and
`DailyRealizedLoss.ledger_qualified` remain false; runtime remains BLOCKED and
policy remains STOPPED.

Restoration is an explicit operator action: verify the chosen backup, restore to
a new private directory without overwriting the original, and review account/
environment identity and ownership. With writers still stopped, set application
state paths, the worker `--directory`, and `--policy-db <same-root>/runtime-policy.sqlite3`
to that same restored generation, with the correct service owner and permissions.
Keep `--lock-directory /var/lib/trader/locks` outside the restored state root and
shared by all designated workers on that host; do not restore copied lease files.
Only then explicitly start the monitor. Restoring files never activates orders;
unresolved commands and reconciliation requirements persist, and qualification
remains blocked. This code does not migrate, rename, delete, switch directories or
restart a service on the operator's behalf.

If the requested worker directory contains legacy `journal.sqlite3` or one of its
`-wal`, `-shm`, or `-journal` sidecars, startup refuses with
`LEGACY_WORKER_JOURNAL_REQUIRES_OPERATOR_RESTORE`, even if `execution.sqlite3` also
exists. The legacy bytes are left untouched and no new journal/state file is
created. Preserve that legacy generation and its matching runtime-state files for
operator-led recovery into a separately validated canonical generation; do not
delete the old files or merely point the worker at an empty directory to clear the
blocker. Automatic migration from the legacy filename is not implemented.

## Explicit receipt corroboration

`await worker.corroborate_native_fills(symbol, start_ms=..., end_ms=...)` is an
explicit owner-loop operation. No timer, endpoint, CLI flag or automatic caller
invokes it. The runtime always pins the worker's exact session and history store
at construction. The worker captures those identities, its binding, scope,
requested window, runtime and lease acquisition generation before obtaining an
opaque session-owned receipt through the existing GET reader. After GET, sticky
stop admission wins before any history SQL access. All identities, the allowed
symbol, live receipt and lease are checked again before ingestion and each
candidate's synchronous runtime call. Receipt cleanup always uses the captured
issuing session, including failure and cancellation continuations.

The existing history store commits the receipt's entire exact snapshot first.
Persisted native candidates for that symbol are selected when either their native
event's floor millisecond lies in the inclusive requested window, or their
execution ID occurs in that exact receipt. Credited candidates are included to
catch later contradictions. Results are globally sorted by canonical candidate
ID and contain only CREDITED, DUPLICATE, CONFLICT or UNRESOLVED data. Actual
comparison still requires exact `history.time_ms * 1_000_000` nanoseconds; selecting
an end-millisecond candidate with a one-nanosecond mismatch never tolerates it.
Unrelated history rows cannot create or adopt a native candidate.

The session allows at most four pending-plus-issued receipts, valid for no more
than 30 seconds from GET start and bounded by session freshness. Publicly
constructible snapshots, comparisons, durable references and credits cannot
replace an exact live handle. Injected HTTP transport is rejected by the public
receipt route. Read-only authentication does not grant execution permission;
the existing permission and qualification gates remain in force.

The runtime validates exact stored whole-snapshot membership and immutable owned
commands, exact execution economics and all relevant history conflicts, then
atomically writes one stable account/environment/instrument/execution guard and
projection. New receipt metadata alone never changes the first credit. Later
contradictions preserve the original quantity and record sourced durable
decisions, including contradictions against an already credited execution.
History and runtime writes are separate transactions. Each candidate commits
independently; a later operational exception propagates while prior committed
history and credits remain queryable and safe to retry with a fresh receipt.
Empty, failed, partial or successful batches do not establish complete exposure,
flatness or account-ledger qualification. No ACK, terminal confirmation,
reservation release, exit authority or protection repair is added.

The portable regression in `test_worker_corroboration.py` uses real temporary
SQLite and the existing standard-library v3 snapshot/verify/restore tool with
both `--require-history` and `--require-runtime`. It compares stopped source and
restored bytes before opening a genuinely fresh next-day worker, session, bridge,
strategy and channel. Replay preserves one accounting credit, original times,
source identifiers, conflicts and V3 digests, with BLOCKED/STOPPED and
reconciliation still required. The old handle and restored data cannot authorize
another credit. No restore-time GET is made and no policy is refreshed to hide
expired original observations.

Positive tests privately seed synthetic session state and replace the exact
owned client's read with configured fixture responses; unconfigured paths fail
closed. They never exercise a public fake-transport success or a production test
issuer. Existing fake monitoring/control fixtures explicitly grant only their
synthetic identities the permissions their tests require. Actual authenticated
transport observation remains unverified. This implementation uses no actual
keys, orders, paid AI or server effects and chooses no operator investment,
model, budget or hosting settings.

## Sealed local native restart and offline v4 backup

The production `ExecutionWorker.start_passive()` construction path now uses a
fresh actual node, Strategy, factory, adapter and native Cache. It does not call
`run_async`, connect clients or start the trader. Its restart configuration sets
`load_cache=False` and `reconciliation=False`: a later native `load_cache()` would
erase the restored in-memory cache, and native reconciliation can infer events
which are not original execution receipts. Monitoring connection after a restored
generation refuses with `NATIVE_RESTART_RECONCILIATION_REQUIRED`. Execution
qualification remains a fixed rejection and saved policy remains STOPPED.

The private `native-restart.json` envelope binds exact account/environment,
BYBIT account label, trader, strategy, instrument allowlist and pinned native
version to the complete logical contents of `execution.sqlite3`,
`runtime-state.sqlite3` and `history.sqlite3`. The canonical digest includes
schema, SQLite versions, all rows and typed cells, including audit/inbox and
liability data; committed WAL content is included and checkpoint layout is not.
Generation IDs are distinct from the new worker's channel and lease identity.
Hashes detect accidental corruption; a writer controlling the private directory
can replace the envelope and its hash. They are not signatures or authentication.

Before application recovery mutates SENDING state or projections, the worker
validates the matching sealed generation and complete immutable application
ownership. It decodes original native events and instruments into detached
objects, reconstructs actual Position objects from their effective original fill
streams, and validates exact object state, routing links, open/closed indexes and
cache integrity. Only a completely validated candidate can consume the seal into
active state and populate an empty fresh cache. A startup failure disposes the
new native assembly and blocks startup; no failed candidate may connect. Original
events are not passed to runtime callbacks, the message bus or fill accounting.
Native ACCEPTED/FILLED/CANCELED data does not ACK a command, credit a fill, confirm
terminal state or release reservations. Existing SENDING-to-UNKNOWN recovery and
reconciliation flags still apply. An explicitly unresolved command with no known
venue identity may remain missing from cache, unresolved and ineligible for resend.

Clean stop requires the admission fence, settled admitted work, disconnected
actual engines and empty command/event queues. It closes the native event
subscription, drains posted callbacks, preserves transport-loss uncertainty, then
captures genuine cache objects and the application digest under the scoped lease.
Atomic restrictive publication creates a new sealed generation before disposal
and ownership release. Failed quiescence or capture does not publish a seal. An
active generation left by a crash, missing seal on an existing application, stale
digest, partial/mixed generation or unsupported native stream blocks restart;
there is no fallback to an earlier seal. This supports a clean-stop local
generation, not arbitrary-crash recovery.

Supported objects are pinned original MarketOrder/LimitOrder event streams and
CryptoPerpetual instruments in the exact allowlist, with one unambiguous NETTING
application lifetime per instrument. Original INITIALIZED, accepted, partially
filled, pending-cancel, terminal orders and closed Position states are preserved.
Successful OrderUpdated histories, transformations, emulation/contingent orders,
position adjustments, split flip fills, flat/reopen multiple lifetimes, foreign
or manual objects, missing source fills/instruments and ambiguous ownership are
blocked rather than normalized or merged. Cached native exposure is local data,
not fresh venue proof or whole-account exposure authority.

The offline standard-library tool automatically includes any native file and
validates it even when `--require-native` is omitted. A native-bearing manifest is
version 4 with exactly `native_files=["native-restart.json"]`, all four runtime
stores, history and existing complete base ledgers/datasets. `--require-native`
applies to snapshot, verify and restore. Missing native files cannot satisfy it;
malformed, active, stale, mixed, oversized or partial native generations refuse
before destination publication. Without native data, the existing v1/v2/v3
formats and completeness rules remain unchanged.

The packaged stdlib `execution/native_restart_format.py` is canonical. Its
portable sibling `scripts/operations/native_restart_format.py` is a mechanically
copied byte-identical implementation guarded by an equality test; tests never
refresh either file. The script loads this sibling anchored to its own path and
needs no source tree, installed application/native package or site-packages. The
portable bundle is `state_snapshot.py`, `history_snapshot_validation.py` and
`native_restart_format.py`; copied-bundle tests use an arbitrary working directory
and Python `-I -S`. SQLite logical digest checks run only on disposable scratch
copies, preserving operator source bytes including WAL/SHM. Portable validation
establishes sealed wire/schema/digest and paired scope consistency. Opaque MsgPack
blobs still require the pinned native semantic codec; declared trader/account
labels require separate live authenticated matching and cannot authenticate
themselves through this unsigned artifact.

Operators must still stop all writers, explicitly declare `--writers-stopped`,
keep private state/backup/parent directories, and restore to a new destination.
Keep leases outside the state root. Nothing here stops a service, migrates or
cleans state, reads operator credentials, connects to a provider or starts trading.

The separate `test_native_restart_linux.py` module defines exactly six actual
native contracts with no parametrization changing that count. Worker cases use
the actual worker builder without `assembly_factory` or builder replacement,
synthetic credentials and temporary SQLite only. CI runs it under `unshare --net`
and a clean environment, requires pinned 1.231.0, a distinct network namespace,
loopback-only interfaces and unreachable IPv4/IPv6 probes, and checks
`native-restart-contracts.xml` for exactly six passing cases and zero skips,
errors or failures. The old strict36 command and XML gate are unchanged. Windows
collection/skips and portable cache tests do not establish this Linux result.
Exact-head hosted CI and independent review remain necessary before accepting
that qualification; this implementation document is not a CI result.

## Remaining implementation and evidence

- No production strategy signal/approved-plan producer or qualified risk evidence
  composer exists in this daemon. No automatic entry cycle is scheduled.
- History and account ledger are callable interfaces, not a complete continuously
  reconciled account ledger. No unobserved midnight balance is guessed or persisted;
  separately proven cash balance and effective UTC boundary are still required.
- Missing event completeness, drawdown/loss-sequence evidence, liquidation evidence,
  attached child ownership, venue readback/protection qualification and replay-safe
  protection repair remain blockers. Factory/config tests cannot prove these.
- Sealed local cache reconstruction does not establish authenticated venue
  reconciliation or continuity/completeness of exchange event delivery. D4
  filtering still refuses unowned venue objects. A separately reviewed passive
  reconciliation lifecycle, arbitrary-crash persistence model and actual venue
  evidence remain open gates; restored cache data cannot enable monitoring or
  cancel/exit/protection authority by itself.
- The lease fences this designated host only, not another computer. No actual
  deployment, PC-off continuous operation, disconnect/reconnect venue behavior or
  real DEMO canary has been demonstrated.
- `NativeExecutionRuntime._require_qualification` remains a fixed rejection.
  RuntimePolicyBook remains STOPPED-only. No boolean, startup flag, saved setting
  or registry entry clears those gates.

Portable tests exercise real isolated SQLite with fake native/provider boundaries:
foreign-thread fills after metadata expiry, exact channel fencing, restart with
unknown commands/no replay, stale policies, both selection modes, callback progress
during generation, signal replacement/reinstallation, failed-stop ownership retention,
subscription scoping, drain guards and default no-effects behavior. Eight
`test_native_node_linux.py` cases exercise actual pinned config and node/factory/
strategy wiring, actual constructor signal replacement for SIGTERM/SIGINT in both
monitor modes, and actual message-bus routing through Strategy.handle_event while
RUNNING, STOPPING and STOPPED during kernel shutdown. Three distinct fills plus a
duplicate must survive in the real temporary SQLite store exactly once. Transport
and connection boundaries are simulated without connecting the adapter. These cases
run only in the mandatory isolated Linux network namespace; Windows skips are not
native proof. Integration CI must report those results separately. This shutdown
contract does not prove restart cache reconstruction, actual venue stream completeness
or production deployment qualification.

Official pinned sources: [TradingNode](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/live/node.py),
[live config](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/live/config.py),
[Bybit factories](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/adapters/bybit/factories.py),
[strategy config](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/trading/config.py).

## Restored monitoring API and operating limits

After `await worker.start_passive()`, explicitly call
`await worker.resume_restored_monitoring()`. The result is a frozen
`WorkerMonitoringStatus`; `worker.monitoring_status` refreshes its current status.
The phases are PASSIVE, RECONCILING, CONNECTING, MONITORING, ATTENTION and STOPPED.
`scope`, `symbols`, `reconciled_at`, `reconciliation_fingerprint`,
`current_fingerprint`, `current_revision`, `reconciliation_current`,
`fresh_read_authority`, `stream_stamp`, `retained_stamp`, `fault` and
`resources_retained` distinguish the last reconciliation from current observation.
`trading_enabled`, `credit_authorized`, `production_supported` and
`venue_event_complete` are always false. A running task alone is not readiness.

Authentication verifies the explicit numeric UID, parent UID and master status
using the exact credential-owning session. A read-only key with no Wallet Capital
or order permission can qualify observation; it cannot qualify execution. Fresh
read/admission authority expires. The exact already-attested live event provenance
can remain valid after that expiry: genuine original fills continue through the
native engine and application quarantine, while `fresh_read_authority` becomes
false and the previous reconciliation becomes stale. Reauthentication, session
close/replacement, transport loss/reconnect, invalid original data or owner/lease
change retires the attempt. There is no automatic reconnect or trading activation.

Supported scope is a bounded, explicit DEMO LINEAR symbol set with an intact
sealed local native cache and unambiguous application ownership. Stable ACCEPTED,
PARTIALLY_FILLED, FILLED, CANCELED and REJECTED originals need fresh positive venue
rows, both order IDs and exact original execution sets. One-way positionIdx=0 is
explicit even when zero. Native filled state may coexist with application quantity
zero only with exact original uncredited provenance. UNKNOWN commands,
reservations, original cache events, existing credits and history remain intact.
Missing originals, unsupported pending/conditional/economic updates, hedge/split/
reversal/reopen ambiguity, missing retention, incomplete pagination and metadata
drift block admission. A missing row never means flat or terminal.

One operation shares at most 200 physical HTTP attempts and 60 seconds across
binding refresh, PRE, real provider/account startup and POST. Each receipt has
at most 32 logical reads, 20 seconds and three physical attempts per GET. There
are at most 16 outstanding receipts and three POST capture cycles. Terminal
quota, ban, malformed page/row/body or ownership failures cannot be converted into
a successful retarget. A genuine observed delta requires a fresh exact POST proof.
The owned generic private stream sends only auth, the exact three-topic batch
subscription and ping/control pong. Admission requires the correct ACKs, private
pong, live modes, actual native/portfolio readiness and native plus application
acknowledgement of the observed delivery watermark.

A new original execution can update real native Order/Position state and persist
an uncredited application candidate. REST reads never manufacture fills, credit,
terminal acknowledgements, repayment or reservation release. This is not
whole-account reconciliation, a complete venue event replay, a valuation proof,
live Bybit acceptance or permission to trade in DEMO or production.

The expected corroboration-required quarantine hold remains a trading/accounting
block, but does not itself retire monitoring when backed by the exact owned
candidate. Other conflicts, mixed or malformed holds and credit decisions still
block monitoring. PRE/POST and live acknowledgement share this distinction.

Execution rows are applied serially: each row waits for native completion and
application acknowledgement before the next row is dispatched. Whole batches and
duplicate input may queue during that wait; duplicate evaluation then observes the
already-applied original. This limits throughput to per-row completion. Linux
qualification observes queued input and each native-before-app window; defensive
pending-ledger duplicate evaluation is separately unit-tested, not claimed as a
reachable concurrent production dispatch path.

Call `await worker.stop()` and wait for completion. Shutdown first retires
admission, then waits for actual node/client/socket tasks and accepted callbacks.
The monitor-only native post-stop wait is 0.05 seconds; actual residual/queue/drain
checks still apply and the complete cleanup has a 10-second bound. The direct
nonmonitoring builder retains its 10-second default. The worker preserves the
caller's running event loop and default thread executor, including queued work;
it disposes only its own proven-quiescent SDK resources.

The pinned SDK recreates an internal actor worker during stop and leaves its
disconnect coroutine untracked. Cleanup verifies their exact owner, kernel pool
and loop, requires idle actor queues and awaits only those owned tasks. Unsupported
ownership fails closed. A startup connection wait may be interrupted only after
genuine stop and disconnection are proven. A bounded delivery failure remains
sticky even after the SDK finishes closing the transport: later logical shutdown
proof rethrows it, retaining the gap, stores, callback channel and lease.

An unproven stop, observed unprocessed gap or changed final cleanup owner retains
the callback channel, stores and lease (`resources_retained=True`). It must not be
reported as a clean STOPPED seal. Release any blocking operation, await the same
owned stop task and explicitly retry stop; do not clear faults or replace a live
owner's handles. A retired attempt cannot rearm. After a genuine clean seal, a
later explicit attempt uses a new worker/client and fresh PRE/POST proofs; genuine
native/application effects survive that seal and restore. An unresolved delivery
gap requires investigation rather than a fabricated checkpoint.

## Qualification evidence boundaries

CI keeps three distinct isolated runs: the original 36 native contracts, the six
restart contracts, and exactly 12 named contracts in
`tests/execution/test_restored_monitoring_linux.py`. Each new case executes the
production worker/builder and genuine lifecycle; only HTTP send and generic
WebSocket dial are synthetic. The venue ledger is independent of the cache.
The Linux namespace fixture checks cleared-environment flags, lo-only networking,
blocked external IPv4 and IPv6 probes and the pinned 1.231.0 wheel before native
construction. The new XML gate requires exactly 12 unique specified names, the
exact module, zero skips, errors and failures. CI archives all three XML receipts
under the tested commit SHA.

Collection, static checks and Windows skips do not establish native acceptance.
Actual Linux execution of all these contracts and all required project CI must
pass on the reviewed commit before any passing-main integration claim.
No fixture result establishes real provider/account acceptance or operational
financial qualification. Existing v4 backup, sealed generation, offline restore
and history restrictions remain in force.

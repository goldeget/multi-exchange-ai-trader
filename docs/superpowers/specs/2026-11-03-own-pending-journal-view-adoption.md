# Task13 U5 technical adoption and Task1 journal-view implementation scope

Root adopts the concrete U5 design, subject to the exact completion-call amendment below. This is a local-data/model contract, not financial activation, whole Task13 completion or permission to execute orders/providers/AI/servers.

Frozen proposal: TASK13_OWN_PENDING_MODEL_DESIGN_20261103.md23775B/SHA6f8a636b2db6ae26856f8449c9945978f32768ad6f4567421e491c054b8e19ba, source pins20219B/066eda1014230635aaa017d98b3ae40faefca4b6b7a0188c37541a10673a25d5, receipt2718B/77ba0c4318d41b45c1c5e91e5d418023444e8bac9cc80b50504de9ccbea411b8. Root fully read the proposal in two bounded outputs and verified all44 public inputs and three frozen artifacts.

Different independent design reviewer: TASK13_OWN_PENDING_INDEPENDENT_DESIGN_REVIEW_20261103.md13546B/SHAa4e5d789c7f1762ae3de8ae7b46c1ec1d7c4cfdcb130e788b08ab940842d8357, SOURCE_PINS21253B/a2cc3dffd700b499c2006cd58cc4b843cecee0f6a8d04a17256dd9a54f1f7e90, RECEIPT3265B/9e82347ca11a76c5445febabb0f1bb298ba527d372e3c89dc947148a8b290fa3. Root read its entire report. Critical0/Important0; Minor1 is the explicit API shorthand corrected here. The old frozen proposal/review are preserved unchanged.

## Exact M1 amendment — binding for later adapter implementation

Use the ACTUAL existing helper signature, not the proposal's informal `_completion(evaluation, ...)` shorthand:

```python
m._completion(
    reference,
    inputs=evaluation,
    expected_input=expected_generic_hash,
    model_revision=required_new_revision,
    public_code=assessment_owner_code,
    now=now,
    # For liquidation only: after=margin.reference.computed_at
)
```

For margin the expected generic hash is the EVALUATION binding hash, required revision BYBIT_LINEAR_OWN_PENDING_MARGIN_V1 and owning code RISK_V2_MARGIN_UNVERIFIED. For liquidation it is the NEW projected margin hash, BYBIT_LINEAR_OWN_PENDING_LIQUIDATION_V1 and RISK_V2_LIQUIDATION_UNVERIFIED, with after=margin.reference.computed_at. Preserve the original reference; never synthesize a reference to call the old builder. Actual code review must verify this exact call. Root resolves M1 by adopting the reviewer's exact source-backed clarification; no intrinsic accounting/design blocker remains.

## Adopted overall direction, not all immediate code authority

True-J one-current original handle/full+evaluation snapshots in one transaction, exact own PENDING identity and all-row witness, no caller-exclusion authority. New outer OWN_PENDING adapter preserves every old V1 DTO/hash/binder/golden; it replays all FULL/evaluation data and original-held risk/margin/caps and derives the separate point capacity min(eval capacity, original held margin), then reuses the old actual selector with new outer hashes. Fresh loss/reserve above original holds refuses; smaller values never release; Q/envelope/price/stop/protection remain original. Genuine source/model/control/lease/protection authority remains a separate intersection.

PENDING view replay ends at actual claim. Claim consumes/retire the view; no SENDING replay or new claim authority. U2/U4 actual producer separately retains the real claim result/full CAS/original owner/expiry and verifies actual final handoff. No pending-only require after claim, no resend/reprice/reservation release/reset. U1/U2/U3/U4/U6/M3/protection/DEMO/server/PC-off/full63 remain incomplete. Generic opaque receipt framework L is NOT adopted.

## Immediate implementation authorization: Task1 ONLY

Root creates a NEW isolated branch/worktree from latest verified main1ea1682c7299bb9f6d1958e06b88b09d0b7c4592. The new journal/reservation/codec source was compared against reviewed integration b7fcbe74cc324ce9a7f6c895ac2408ff1dff6a99: no difference. A later integration onto the then-current main is still mandatory. Root owns Git/CI/main.

Owned paths ONLY:

- src/multi_exchange_trader/execution/journal.py
- NEW tests/execution/test_own_pending_view_v2.py

No SQL schema/layout/writer/archive/payload/hash changes, public signature changes, runtime/worker/source/model/native/qualification or model-adapter implementation in this task. Old tests are read-only. Reuse original `_v2_checked_records`, `_v2_meta`, `_snapshot`, scope/codec/reservation and existing command identity helpers; no second journal/hash/serializer.

Implement the proposal's private identity-only `_OwnPendingViewV2`, private retained `_OwnPendingDataV2` exact fields and one-slot J lifetime. Exact methods:

```python
_own_pending_view_v2(scope: RiskScopeV2, *, command_key: str,
    owner_epoch: str, expected_command_identity_sha256: str,
    now: datetime) -> _OwnPendingViewV2
_require_own_pending_view_v2(view: _OwnPendingViewV2, *, scope: RiskScopeV2,
    command_key: str, expected_command_identity_sha256: str,
    now: datetime) -> _OwnPendingDataV2
```

Capture validates closed types and retires the prior slot before I/O. Use ONE existing J transaction and COMPLETE validated records/meta. Original must be true nonterminal PENDING ENTRYv2 with deterministic client, exact position/scope/epoch/command identity, canonical original intent/reservation and archived assessed_at<=now<original envelope expiry. Generate full exclude=None and evaluation exclude=exact own from SAME records/epoch/revision/time. The FULL held hash/epoch/revision remains in BOTH. No captured/require DB writes, schema migrations, claims or defaults.

Retain original handle/data plus independent detached primitive/type/value witness of all rows/meta, including phase/terminal and full immutable payload and exact V1/V2 reservation preimages. Mutable retained DTO aliases cannot be the sole baseline. Require exact original J/slot/handle identity and open lifetime; revalidate ALL current rows/meta/witness and rebuild both snapshots at ORIGINAL observed_at in ONE transaction. Compare retained data to the original witness before returning the same retained data; never rebaseline from callback-modified values. Every wrong-J/copied/forged/stale/closed/changed-phase/state case refuses with existing owning errors. This proves LOCAL J data only, not real lease/source/account authority.

Close retires before DB close. Claim attempts may conservatively retire the slot; successful claim MUST retire. Keep original state/full CAS/capacity behavior and errors. No view may revive after a failed capture/claim or equal-value recapture; malformed capture before I/O may also retire conservatively. Bind/release/register/row changes must be detected on require. Existing methods outside necessary slot lifetime hooks remain identical. Do not add unsupported arbitrary object-reflection protection or global token machinery.

## Test-first and final verification

Use real temporary J register/bind/claim and the existing canonical reservation/codec fixtures; never fabricate a positive CommandRecord. Start with missing-seam behavioral RED (assert missing callable, not collection/import error), then positive full/eval with actual original hold. Tests must prove exact matching full hash/epoch/revision/original timestamp, only-own exposure subtraction and all other holds retained, no source/SQL mutation by capture/require, original object lifetime and retained-witness corruption refusal.

Meaningful negatives include wrong/copy/object-new/other J, public excluded DTO, key/scope/identity/epoch, nonPENDING/terminal/V1/malformed row/frame/meta/Decimal reservation, current held/revision/command/state changes, equal-value recapture/failed capture, close and claim retirement, unchanged capacity pins on claim with phase invalidation. Preserve old registration/claim/reservation/codec behavior and archive bytes.

One final fixed run of NEW test plus tests/execution/test_reservations_v2.py and test_risk_v2_codec.py, owned Ruff/strict mypy, before/after source pins and complete original-unowned custody/AST scope. New F fixture/cache/TEMP roots, fixed Python3.12.10, -B/UTF8/bytecodeoff/five file selectors off/current W/src FIRST, SDK/network/provider effects forbidden. Mypy normal hard-exit missing runtime-after is disclosed; no fabricated record. Every failure/attempt and raw argv/PIDs/UTC/streams/exits is retained.

Report<=16KB, source/evidence manifest and receipt then STOP WRITES. A DIFFERENT independent CODE reviewer must review actual source/lifetime/canonical semantics; root integration/final exact whole CI and passing main remain required. Model Task2/3 starts separately only after this real-J interface is reviewed and frozen. No actual order/paid AI/server/private-store action is authorized to the child.

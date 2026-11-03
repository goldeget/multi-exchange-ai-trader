# Exact Strategy Catalog Design

2026-11-03 KST. Root-adopted binding contract after independent candidate preflight. User authorization covers continuous implementation, independent review and passing-main merges; no routine approval question. This contract is not implementation/test/runtime acceptance evidence. Original external drafts/candidate remain preserved.

## 1. 근거와 작성 범위

- R: `F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102`.
- 직접 읽기: HEAD `46d4d13de3f990e875c34bba3b4c34ffc3f81609`, tree `6266b5e4c50bec1c8dad24e8eea07cdd7cb7f413`; 확인 당시 status short 빈 출력. parent가 전달한 이전 d789와 tree가 같은 상태다. 동시 A02 작업이 있으므로 구현자는 새 preflight를 해야 한다.
- 전체 읽은 준비안: `A04_STRATEGY_REBASED_PREPARATION_20261103.md`, SHA256 `a20003aa86acf6c07056f87adce2e4e5445e38af8dd0ac18e64aa0d586d5226e`.
- 추가 근거: `wave1-catalog-contract.md`의 A04/정책/소유 경계, `spot-selection-contract.md`의 상품 identity·승인·전환 경계; 현재 `execution/modes.py`, `worker.py`, `runtime_policy.py`, `research/strategy.py`, root `trading_product.py`, `pyproject.toml`, modes 테스트 소스. 오래된 wave1의 별도 category 키/LINEAR-only 제안은 채택하지 않는다.
- 조상 및 R 아래 검색에서 적용할 AGENTS.md를 발견하지 못했다. 공개 소스 읽기와 요청된 두 외부 문서 작성만 수행했다. 코드·테스트·Git mutation·테스트 실행·네트워크·provider·private/default store·환경/키·note/vault/trial DB·계좌·native·주문·AI·서버 접근은 하지 않았다.

## 2. 목표와 한 개의 작업 경계

첫 task는 주입된 승인 metadata의 구조/중복/내용 digest와 exact 선택 집합을 검증하는 순수 부품이다. 신규 파일은 `src/multi_exchange_trader/execution/strategy_catalog.py` 및 `tests/execution/test_strategy_catalog.py`뿐이다. 기존 모듈 수정이나 package re-export도 없다.

기존 `execution.modes.StrategyReference`와 `multi_exchange_trader.trading_product.InstrumentKey`를 그대로 사용한다. 새 reference/key/category helper·default field·전략 seed·금융값·TTL·policy·issuer·current-generation service는 만들지 않는다. stdlib 외 새 의존성은 없다. HTTP/파일/DB/시계/환경/SDK/dynamic import/eval/registry/candidate 생성과 runtime 소비는 제외한다.

현재 runtime_policy는 schema1 string[]이고 worker는 ID 집합까지 대조한다. 이 부품을 추가해도 설정의 exact revision 영속 pin과 worker 경로가 해결되지 않는다. modes의 selector/priority/NO_CANDIDATE와 TrustedPlanRegistry는 그대로다. research의 SMA_CROSS_V1/BUY_HOLD_V1은 운영 승인 seed가 아니다.

## 3. 공개 인터페이스 — candidate의 정확한 이름과 타입

```python
from dataclasses import dataclass
from typing import Literal
from multi_exchange_trader.trading_product import InstrumentKey
from .modes import StrategyReference

ApprovalStatus = Literal["APPROVED", "DEPRECATED", "REVOKED"]
ApprovalEnvironment = Literal["DEMO", "LIVE"]

@dataclass(frozen=True)
class StrategyDescriptor:
    reference: StrategyReference
    implementation_sha256: str
    config_sha256: str
    config_schema_version: int
    engine_contract_version: str
    permitted_instruments: tuple[InstrumentKey, ...]
    approval_reference: str
    approval_source_reference: str
    approval_source_sha256: str
    approval_environments: tuple[ApprovalEnvironment, ...]
    approval_status: ApprovalStatus

@dataclass(frozen=True)
class StrategyCatalog:
    schema_version: int
    revision: str
    entries: tuple[StrategyDescriptor, ...]

@dataclass(frozen=True)
class StrategyCatalogSelection:
    catalog_revision: str
    instrument: InstrumentKey
    environment: ApprovalEnvironment
    allowed: tuple[StrategyDescriptor, ...]
    selected: tuple[StrategyDescriptor, ...]

class StrategyCatalogRejected(ValueError):
    code: str
    def __init__(self, code: str) -> None: ...

def build_strategy_catalog(
    entries: tuple[StrategyDescriptor, ...],
) -> StrategyCatalog: ...

def require_strategy_references(
    catalog: StrategyCatalog, *,
    expected_catalog_revision: str,
    allowed: tuple[StrategyReference, ...],
    selected: tuple[StrategyReference, ...],
    instrument: InstrumentKey,
    environment: ApprovalEnvironment,
) -> StrategyCatalogSelection: ...
```

모든 필드는 필수이며 default는 없다. 공개 함수는 위 두 개로 제한하고 serializer/validator/lookup은 private implementation detail로 둔다. Descriptor/catalog/selection constructors validate intrinsic value invariants through shared private validators in `__post_init__`; build/require revalidate even constructor-created input. Returned records remain caller constructible metadata, not authority. Missing required constructor arguments retain Python's ordinary TypeError; forged existing objects with missing attributes presented to validation receive the fixed rejection below.

## 4. 엄격한 값 검증 규칙

1. Records require `type(value) is ExpectedClass`; tuples require exact tuple, not list/subclass. Recheck nested fields at each public boundary, including frozen objects altered via `object.__setattr__` or missing fields. Malformed nested fields raise a fixed rejection, never AttributeError/TypeError leakage. No introspection-heavy generic validation framework is needed.
2. Identifier fields (`strategy_id`, strategy `revision`, engine/source/approval references) require exact str matching `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}`. Do not trim, casefold stored values, resolve aliases, or search another revision. A strategy revision whose `casefold()` equals `latest` is rejected as a forbidden moving marker. Other opaque values are not proof of immutability: issuer must establish their meaning across generations. Different case remains different identity.
3. All three SHA fields require exact str matching `[0-9a-f]{64}`. No uppercase conversion. `config_schema_version` requires exact int (not bool), `1 <= value <= 2147483647`; the upper bound is a local metadata resource bound, not a strategy or financial limit. Catalog schema requires exact int `1`.
4. `permitted_instruments` is a nonempty, unique tuple of at most 100 exact common InstrumentKey instances. After exact-class check, revalidate by reconstructing `InstrumentKey(venue=key.venue, product=key.product, symbol=key.symbol)` and catch ValueError/AttributeError as fixed rejection, suppressing nested error context. This delegates venue/product/symbol rules to the common constructor without duplicating regex/category mapping or importing private `_validate_key`. Never infer product from symbol/suffix. SPOT/BTCUSDT and LINEAR/BTCUSDT differ.
5. `approval_environments` is a nonempty, unique tuple of exact strings from DEMO/LIVE; maximum two. No DEMO→LIVE inference. `approval_status` is an exact string from the three declared literals. Non-APPROVED entries are permitted tombstones, but cannot resolve successfully.
6. Catalog entries and selection/require allowed/selected tuples each have at most 100 elements. A collection over its bound rejects before its nested traversal. Catalog validation first checks entries exact tuple/cap, then each entry exact class and accessible exact `permitted_instruments` tuple/cap before catalog-level nested descriptor/key traversal or canonical hashing. Consequently 100 entries ×100 instruments gives a **derived worst-case bound of10000 memberships**. No independent aggregate counter/framework/over-cap rejection branch is needed. Descriptor constructors may already have validated their own inputs; the preflight constrains each later catalog validation, not historical construction. Allowed must be nonempty at require and selection construction; selected may be empty. Duplicate allowed/selected references reject. These are local resource bounds, not statements of supported strategy/universe size.
7. Every descriptor is validated, including entries unused by selection. Same exact reference repeated with identical canonical descriptor payload rejects DUPLICATE; different payload rejects CONFLICTING. Same ID/different revisions remain distinct entries. No overwrite/first/last preference.

Fields are declared metadata. implementation/config hashes describe bytes produced by a later trusted build; this module does not read those bytes or define canonical execution-config serialization. Approval references identify an immutable public-safe approval record/source and hash, but do not prove signer authority, actual approval, source authenticity, or current state. Do not put keys, private config or account data in these fields.

### Constructor/shared-validator contract and safe precedence

Private validators contain the same intrinsic checks used by constructors/build/require; no public API depends on a prior constructor having run. They never call build or instantiate their own record type. Build validates entries, computes revision privately, then constructs StrategyCatalog; its constructor may repeat validation/hash calculation but cannot recurse into build. Require directly validates the supplied catalog and constructs the intrinsically validated selection after resolution.

- **Safe extraction:** exact record-class/tuple/scalar type checks and bounded shape/count checks precede collection hashing, equality, sorting, set/dict insertion and canonicalization. Safely fetch required attributes; missing nested attributes become STRATEGY_CATALOG_INVALID_INPUT with no raw object repr or nested exception context. Do not invoke a malformed field's `__hash__`, `__eq__`, ordering or string conversion. Only fully validated primitive values/canonical payloads may participate in those operations. Extra dynamic instance attributes are ignored; they are outside declared fields and grant no authority.
- **Descriptor constructor:** reference exact class and its ID/revision fields → implementation/config hashes → config schema → engine identifier → permitted tuple shape/cap, every exact shared key, duplicate keys, nonempty → approval/source identifiers and source hash → environment tuple shape/cap, values, duplicate/nonempty → status. Each malformed intrinsic value rejects INVALID_INPUT; full reason names are in §6.
- **Catalog constructor:** schema exact integer1 → revision exact syntax → entries exact tuple/cap → entry/permitted-tuple shape/cap preflight establishing the derived10000 bound → every descriptor intrinsic validation in entry order → duplicate/conflict pass → recomputed canonical digest equality. Empty entries are intrinsically valid. Valid-syntax wrong digest rejects REVISION_MISMATCH. It authenticates no source/current generation.
- **Selection constructor:** catalog_revision exact syntax → instrument exact shared-key revalidation → environment exact value → allowed/selected exact tuple/cap → all descriptor intrinsic checks, allowed then selected in their own order → duplicate reference within either tuple rejects INVALID_INPUT → allowed-empty rejects ALLOWLIST_EMPTY → each selected must have a reference present in allowed AND an equal canonical descriptor payload, otherwise SELECTION_OUTSIDE_ALLOWLIST → for each allowed descriptor in input order, check APPROVED, then environment, then instrument, finishing that descriptor before examining the next. Payload equality is set-order insensitive but includes every declared field. A same-reference/different-content selected descriptor is not a subset. Constructor cannot prove catalog membership, exact catalog digest/currentness, source authority or cross-generation immutability.
- **Exception constructor:** accepts only exact str in the eleven-code set of §6; everything else raises `ValueError("INVALID_STRATEGY_CATALOG_REJECTION_CODE")` with no raw custom code or context. Valid code sets `.code` and exact exception message; internal call sites use fixed literals.

All descriptor/catalog/selection intrinsic validation failures use StrategyCatalogRejected with the fixed applicable code. Required-argument Python TypeError and invalid rejection-code ValueError are the two explicit constructor exceptions; neither interpolates supplied values.

## 5. Canonical catalog revision — exact semantic bytes

`revision = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()`; exact regex `sha256:[0-9a-f]{64}`. Revision itself is excluded from payload. Do not include clocks, Python repr, hidden state, source fetching, or execution policy.

Payload object is exactly `{"schema_version": 1, "entries": [descriptor_payload, ...]}`. Each descriptor payload has exactly these keys and values:

```json
{
  "reference": {"strategy_id": "<exact>", "revision": "<exact>"},
  "implementation_sha256": "<exact>",
  "config_sha256": "<exact>",
  "config_schema_version": 1,
  "engine_contract_version": "<exact>",
  "permitted_instruments": [
    {"venue": "BYBIT", "product": "SPOT", "symbol": "BTCUSDT"}
  ],
  "approval_reference": "<exact>",
  "approval_source_reference": "<exact>",
  "approval_source_sha256": "<exact>",
  "approval_environments": ["DEMO"],
  "approval_status": "APPROVED"
}
```

This JSON shape is a schema illustration, not a concrete approved seed. All strings remain exact; numeric schema values stay JSON integers. Sort descriptor payloads by `(strategy_id, revision)` using Python string ordering. Sort instruments by `(venue, product, symbol)` and environments lexicographically. Serialize with `json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`, with no BOM/newline.

Build returns `StrategyCatalog(1, revision, entries)` preserving input entry order and original descriptor objects. Canonical digest alone is order-insensitive for the three set-valued collections; dataclass equality need not be. Duplicate/conflict classification compares canonical descriptor payloads. Changes to any declared semantic field, removed entry, or status/source/domain/environment change the digest; mere collection ordering does not.

Empty-catalog canonical bytes are exactly `b'{"entries":[],"schema_version":1}'`; golden revision is `sha256:1038daeddcbbd28c7ee6feb49e2ddbb6ea1587d4e9f8b3282ef5ed1813165f23`. This literal was calculated from those document bytes with .NET SHA256 while drafting; it is not a product test PASS.

`reference.revision` is an issuer-assigned opaque pin, not auto-replaced by the catalog digest. Same reference must map to the same implementation/config/schema/engine content across generations; a single catalog cannot prove that historical invariant. Approval domain/environment/source/status can evolve under the trusted issuer and alter catalog revision without automatically reissuing strategy content revision. Actual config canonical bytes and artifact packaging remain issuer/build decisions.

## 6. Rejection contract and deterministic order

For an accepted exact fixed code, `StrategyCatalogRejected(code)` stores `.code == code` and `str(error) == code`; its constructor rejects every other value as specified in §4. No input interpolation, nested exception text, raw payload or detailed path. All deliberately rejected metadata inputs from public functions use this exception. Unexpected implementation errors must not be broadly swallowed.

| Code | Condition |
|---|---|
| STRATEGY_CATALOG_INVALID_INPUT | Invalid record/field/type/identifier/hash/domain/environment/status, unsupported schema, resource bound, duplicate key/environment/allowlist/selection |
| DUPLICATE_STRATEGY_REFERENCE | Repeated exact reference with equal canonical descriptor payload |
| CONFLICTING_STRATEGY_REFERENCE | Repeated exact reference with different canonical descriptor payload |
| STRATEGY_CATALOG_REVISION_MISMATCH | Valid revision syntax but catalog digest mismatch or expected revision differs |
| STRATEGY_CATALOG_EMPTY | Valid empty catalog presented to require |
| STRATEGY_ALLOWLIST_EMPTY | Valid empty allowed tuple at selection construction, or require with a nonempty catalog |
| STRATEGY_SELECTION_OUTSIDE_ALLOWLIST | Selected exact reference absent from allowed; selection constructor also rejects same-reference/different canonical descriptor payload |
| UNKNOWN_STRATEGY_REFERENCE | Allowed reference not in catalog, including unknown revision of a known ID |
| STRATEGY_NOT_APPROVED | Allowed reference has DEPRECATED/REVOKED status |
| STRATEGY_ENVIRONMENT_MISMATCH | Allowed descriptor lacks requested environment |
| STRATEGY_INSTRUMENT_MISMATCH | Allowed descriptor lacks exact requested common instrument key |

Build: exact tuple/bound → entry/permitted-tuple shape/cap preflight → validate every descriptor in input order → duplicate/conflict in input order after descriptors are valid → digest → intrinsically validated catalog construction. Empty tuple is valid and produces a deterministic empty catalog. No build↔constructor recursion.

Require: (1) exact catalog and full constructor-intrinsic validation including shape/cap preflight and digest, without calling build; malformed revision syntax is INVALID_INPUT, valid-but-wrong digest is REVISION_MISMATCH; (2) validate expected revision syntax and exact equality; (3) validate instrument and environment; (4) reject empty catalog; (5) exact/bounded allowed and selected tuple/reference fields, duplicates, allowed-empty, selected exact-reference subset in that order; (6) resolve each allowed reference in input order, checking existence → APPROVED → environment → instrument; (7) construct the selection whose own intrinsic validator also runs. First failure aborts the whole operation. Validate all allowed, including unused ones; do not return a successful partial set.

Success returns exact catalog revision, original instrument/environment, allowed descriptors in allowed-reference order, and selected descriptors in selected-reference order. No automatic selected=allowed expansion. An empty selected tuple stays empty. This function does not call modes or issue plan/candidate/EntryDecision/protection/qualification tokens.

## 7. Trust, current generation and revocation limits

Metadata can be syntactically valid when created by a test or caller. CatalogSelection is an inspectable value result and carries no unforgeability or runtime authority. Do not add `trusted`, `approved`, `qualified` or `admitted` booleans, secret constructor seals, object identity registries, TTLs, or a pretend trusted issuer in this task.

New catalog status=REVOKED/DEPRECATED or deletion blocks the relevant reference; presenting its old expected revision with the new catalog also blocks. **Presenting old catalog + matching old expected revision together can still pass.** This pure task cannot know the currently authoritative generation, detect rollback, invalidate a previously returned result, authenticate approval source, or reject a stale selection after await.

Later server-owned issuer/current-catalog/admission work must establish source authority, actual artifact/config hashes, cross-generation reference immutability, authoritative current generation/rollback/revocation, and bind policy CAS/product/permission profile/current instrument/risk evidence to plan. Recheck after await and before PLAN→SUBMIT. Those are mandatory later acceptance gates, not extra fields silently inserted here.

Blocking new ENTRY because strategy approval changes does not stop existing ownership/protection/EXIT/reconciliation/recovery duties. Product switches remain subject to existing liability/quiescence/re-authentication gates. No worker/CLI qualification blocker is loosened.

## 8. Test acceptance matrix for the single task

All fixtures are explicitly synthetic, e.g. `fixture-trend/v1`, repeated fixed lowercase hash characters and `fixture-approval-v1`; no fixture is installed as a production default.

- Happy path: imported existing reference/key identity, exact fields/no defaults/frozen values and validating constructors, original descriptor retention and requested output ordering; selected-empty preserved; both products allowed only through explicit keys; DEMO/LIVE scope independently explicit.
- Strict records: constructors reject malformed intrinsic input; build/require recheck dict/list/subclasses/bool/missing attributes and altered frozen reference/key/catalog. Malformed fields with raising hash/equality/str hooks are rejected before those hooks run. Hash/identifier/schema/domain/status cases follow §4. Each major tuple accepts100/rejects101 before its nested traversal/hash. The derived10000 bound follows directly from these two catalog caps; no independent10001 aggregate test/branch, bypassed constants or generic aggregate framework is permitted. Distinct valid data avoids duplicate masking.
- Uniqueness: same reference/same descriptor and same reference/different content; domain/environment order-only duplicate uses DUPLICATE; same ID/different revisions valid and never automatically chosen.
- Revision: independently constructed canonical JSON oracle, exact empty digest golden fixture, permutations of entries/domains/environments preserve hash; each declared payload field change and deletion changes hash; no output order mutation; malformed catalog schema/digest is revalidated.
- Require: stale expected against new catalog, nonempty/empty precedence, exact revision subset, duplicates; selected but nonallowed fails before resolution; unknown/revoked/non-domain/non-environment unused allowed each rejects whole operation; deterministic first offending allowed entry.
- Products: SPOT approval rejects LINEAR same-symbol and inverse; explicit two-key domain resolves each. Reconstructed altered common key rejects without duplicating symbol/category logic.
- Mixed-fault selection precedence: first allowed descriptor APPROVED but wrong environment and later descriptor REVOKED yields ENVIRONMENT_MISMATCH; reversing them yields NOT_APPROVED. Pin constructor and require per-descriptor behavior, with valid intrinsic metadata.
- Constructor-specific selection: nonempty allowed, no duplicate exact references, full canonical descriptor subset (including same ref/different content rejection and order-only payload equality), APPROVED/environment/domain checks and deterministic precedence. Caller construction without proof of catalog membership/currentness remains possible; no token is added.
- Exception constructor: all eleven exact codes accepted; subclass/nonstring/custom string rejected with exact ValueError text and no raw value.
- Trust limits: caller-constructed valid catalog with matching digest behaves like build metadata; old+old success is an explicit test documenting limitation, old+new mismatch and new revoked/deleted failures; returned result has exactly five declared fields and no token/authority flags.
- Side effects/import discipline: module imports only stdlib, modes.StrategyReference and common InstrumentKey. Import/build/require do not read files, clock, network, settings, DB or instantiate worker/registry. Test meaningful forbidden operations with injected fail-fast spies where applicable and static module-import review; do not introduce a separate generic audit framework.

Future tests cover real issuer/artifact/current-generation/admission/CAS/worker/revocation-during-await. They cannot pass in this first task and must not be represented by mocked approval success.

## 9. Root-directed decisions incorporated; remaining cost and gates

Root supplied the following concrete decisions after reading both original drafts. They are adopted here after independent candidate preflight (`A04_STRATEGY_CANDIDATE_PREFLIGHT_20261103.md`); the per-descriptor constructor precedence ambiguity is resolved explicitly above. Independent implementation review remains. No builder should reselect these choices or ask the user for routine approval.

| Decision | Root-directed candidate contract | Cost / limitation |
|---|---|---|
| Metadata field set and scope | Existing11/3/5 fields, source/status/env, one pure task, no consumer | No runtime exact pin/current issuer follows merely from adding value contracts |
| Identifier/resource envelope | 96 ASCII; major tuples100; derived maximum10000 instrument memberships; schema int cap2147483647 | Legitimate longer IDs or >100 domains/strategies are bounded outliers and reject; must be consciously revised, never truncated/filtered. No unreachable independent aggregate branch/test |
| Validating constructors plus public revalidation | Shared private validators; no build/constructor recursion; fixed safe precedence | Extra explicit checks and repeated bounded hashing at build→constructor/require→result are intentional; avoid a generic validation framework or authority seals |
| Common key | Exact existing class reconstructed for intrinsic recheck | Small allocation cost, shared validation stays authoritative; no duplicate symbol/category helper |
| Canonical digest/output | Prefixsha256, exact §5 JSON/hash, original output order | Changing bytes later changes every pin; dataclass equality need not share set-order semantics |
| Rejection class | Only eleven exact fixed codes; unknown input yields fixed ValueError | More constructor tests; consumers receive no raw arbitrary diagnostic text |
| Empty selected | Allowed nonempty, selected may be empty and never expanded | Mode-specific launch requirements stay with later policy/selector owners |
| Authority / actual seed | Metadata only, old+old limitation, no issuer/generation/policy defaults | Actual artifact/config/approval/protection/re-entry source, current generation and CAS/worker integration remain required later root work |

This spec records root-adopted design choices. Independent implementation and whole-branch review, exact hosted/main checks and later actual issuer/admission/product acceptance remain required. Root supplies the task BASE/evidence directory and owns all Git.

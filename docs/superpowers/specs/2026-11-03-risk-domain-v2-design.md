# Risk V2 Full-Held Domain — Pure Specification Candidate

Status: PROPOSED; root review/adoption is required before code. This prepares original Risk R2 §17/Task13 Step3B before E05-M2-D; it issues no actual source/model/account/selection qualification.

## 1. Basis and scope
- Read-only basis E=`F:/자동매매프로그램/worktrees/bybit-linear-model-source-20261103`; original design §5/5A/5B/6/7/17 and plan Task13 domain step.
- Design SHA256 `0ba353e00eb7bc39ac48866ac4ab1f3b4d5f972010d110f2459a50b7ddee51dc`; plan SHA256 `f5bfc94f6e2fe3a3116e44dae4a0922d5079ef1a8d7e1c3f24470a0d6434eb44` at read time.
- Stable `execution/risk_v2_contracts.py` SHA256 `7128ab957cc2536f87eb85e4c4bff8c32913b97a745fe6fa01b349a15ba74faa`.
- Stable TASK4-only `execution/linear_risk_evidence_v2.py` SHA256 `340c4023074346f81f81217a485ce64630542ebdc4e3e01b82d063f9e198f35d`.
- Stable `execution/risk_v2_math.py` SHA256 `1bfb183b310f5a4c086fa1b74b0a3018829539a26ce0a64fcea150eaefd0fe70`.
- A02 `market_data/instrument_definition.py` SHA256 `4c4e1c60ef0ff9c564478503cdce35198d1e2edabcd6730a20761aad8e31a6a5`; `instrument_catalog.py` SHA256 `755b7beb5dd77db307ef3421eaa0129520136668e007e17a1a56c666039c85a4`.
- Future ownership: NEW `src/multi_exchange_trader/execution/risk_domain_v2.py` and NEW `tests/execution/test_risk_domain_v2.py` only. Existing generic files stay read-only.
- No reading/adoption of active Bybit model/account/risk-limit WIP, Task6/P WIP, private/default/.env material; no provider/SDK/native/WSL/DB/order/AI/server/install/Git activity.

## 2. Root API amendment recommended
Existing Coverage contains `source_record_sha256s`, but its builder has no corresponding argument. Generic DTOs expose raw-row/payload/input/snapshot hashes, not complete Bybit registry records; IDs cannot reconstruct those records.
Choose a REQUIRED explicit `source_record_sha256s: tuple[str, ...]` builder argument. Preserve both existing DTO field lists. Require 1..1024 exact lowercase SHA256 strings, ascending and unique; reject rather than sort/deduplicate. The 1024 ceiling is a proposed local resource bound, not an exchange rule.
The tuple commits supplied registry-record hash VALUES. The builder neither computes them from IDs nor certifies that the tuple is authentic/complete. Actual issuer owns record selection, registry hash format, resolution and content correspondence.
Alternative: derive a list of known generic input hashes. It is reproducible locally, but mixes raw/payload/computation-input/snapshot hash domains, omits registry wrapper/content and cannot truthfully retain `source_record_sha256s` meaning; adopting it would need a differently named/schema-amended commitment. Reject this alternative for the minimal closure.
Never require an opaque computation `input_sha256` or source-record hash to equal an invented digest of visible generic fields. Such equality is not specified by the source owner.

## 3. Exact public DTOs and signatures
All fields mandatory, no defaults; exact frozen dataclasses are non-authoritative value containers. Imported types below are the existing stable types, not parallel replacements.
```python
@dataclass(frozen=True)
class LinearRiskDomainMemberV2:
    instrument_pin: InstrumentPinV2
    mark: LinearMarkQuoteV2
    tiers: LinearTierSnapshotV2
    account_component_ids: tuple[str, ...]
    reservation_component_ids: tuple[str, ...]
    account_risk_quote: Decimal
    account_margin_quote: Decimal
    account_position_count: int
    reserved_risk_quote: Decimal
    reserved_margin_quote: Decimal
    reserved_position_count: int
    account_margin_reference: RiskObservationRefV2
    account_risk_reference: RiskComputationRefV2

@dataclass(frozen=True)
class LinearRiskDomainCoverageV2:
    account_scope: ProtectionScope
    catalog_revision: str
    selected_entry_key: InstrumentKey
    coverage_revision: str
    source_generation: str
    members: tuple[LinearRiskDomainMemberV2, ...]
    account_snapshot_sha256: str
    reservation_snapshot_sha256: str
    mark_set_sha256: str
    source_record_sha256s: tuple[str, ...]

def build_linear_risk_domain_coverage_v2(*, policy: LinearRiskPolicyV2,
    catalog: InstrumentCatalog, catalog_revision: str, coverage_revision: str,
    source_generation: str, account: LinearAccountRiskSnapshotV2,
    reservations: LinearReservationSnapshotV2, marks: tuple[LinearMarkQuoteV2, ...],
    members: tuple[LinearRiskDomainMemberV2, ...],
    source_record_sha256s: tuple[str, ...], now: datetime) -> LinearRiskDomainCoverageV2: ...
def risk_domain_sha256_v2(value: LinearRiskDomainCoverageV2) -> str: ...
```
No proposed quantity/envelope/sizing is accepted. `selected_entry_key=policy.scope.instrument` only identifies the candidate. This does not establish actual approved selection.

## 4. Closed validation order and supplied-data checks
1. Structural pass: validate exact top classes, every required field, nested records, tuple/scalar types, sizes, Decimal roles and timestamps before cross-record equality, ordering, hashing or dictionary/set lookup. Revalidate forged `object.__new__`/mutated records; never invoke arbitrary equality/repr/iteration/tz/attribute hooks. Reuse stable validators, not dataclass equality on unchecked inputs.
2. Validate `policy`, account/reservations, marks, every member pin/mark/tier/reference and all IDs/hashes. Amounts in members are nonnegative DERIVED Decimals; counts exact nonnegative ints. ID tuples are 0..1024, each ASCII identifier, strictly lexicographic and unique. All account IDs across members total <=1024; all reservation IDs total <=1024; reject any global duplicate or cross-set overlap.
3. Catalog helper uses exact `InstrumentCatalog` fields and public `build_instrument_catalog(observations, *, declared_universe, now, max_age)`. This invokes existing `validate_instrument_observation` and public catalog freshness rules. Require original schema=exact int1, canonical original declared/observation key order, original revision and UTC expiry equal the rebuilt catalog; `catalog_revision` is a lowercase digest equal to its revision. Do not normalize a malformed original catalog into acceptance; do not call selection with synthetic allowed/selected values. Translate every A02 `CatalogRejected` to fixed `RISK_V2_INPUT_INVALID` without its message.
4. Require policy account == account.account == reservations.account using validated scalar payloads; account margin/position modes match policy's supplied modes. Generic typed CROSS is not actual E05 support. `coverage_revision` and `source_generation` are exact generic IDs; do not equate them to account.coverage_revision or reservation.owner_generation by name.
5. Compute sorted unique `risk_keys={policy.scope.instrument} ∪ account.component instruments ∪ reservation.component instruments`, order `(venue, product, symbol)`. Require 1..100 LINEAR keys, all present in current declared catalog. Members and supplied marks each match this tuple exactly; no omitted/extra/duplicate key or unsupported-product filtering.
6. For each key, resolve its whole A02 observation; require member pin payload equals `pin_instrument_v2(observation, catalog_revision=catalog_revision)` in every field. Require member.mark payload equals that key's supplied mark, member.tiers.instrument equals key, and tiers.instrument_revision equals pin.specification_revision. Mark/tier public environment equals pin.source_environment; endpoint-specific source_id/rule strings need not equal one another. Public→private account mapping remains actual issuer work.
7. Each member ID tuple equals precisely the corresponding account/reservation component IDs filtered by that key, in their existing canonical order. Header components already enforce account origins versus PENDING_ENTRY and global identity disjointness. Local IDs cannot establish native order/ACK completeness or prove that distinct IDs mean distinct actual orders.
8. Exact sums across all members of account_risk_quote/account_margin_quote/account_position_count equal account.total_risk/total_margin/position_count; reserved equivalents equal reservations.risk_amount/margin_amount/position_count. Per-key economic attribution/count semantics are source claims, not inferred from ID cardinality or fabricated from totals.
9. For each member, compute `U=sum(account base_quantity × fresh mark)+sum(reserved base_quantity × fresh mark)` for that key, never net BUY/SELL. Reuse `_gross_components_v2`; use exact Fraction/int math. Full tier validation supplies contiguous `(0,last_upper]` coverage; require `U<=last_upper`. U=0 is valid, equality passes. No proposed quantity is added here; later entry tier/admission adds it. No user-X leverage intersection or grid selection is applied to held Y; do not call `assess_linear_tier_v2` from this builder.
10. Freshness: both mark source times use policy.max_quote_age_seconds. Both account reference times, reservations.observed_at, each pin response/receipt, tier source times, member.account_margin_reference source times and member.account_risk_reference.computed_at use policy.max_evidence_age_seconds. Require source server<=receipt<=now; computation<=now; use exact integer microseconds and Fraction max-age, equality to policy age passes. Catalog's own boundary remains `now>=expires_at`/age equality rejected as A02 defines. No clock/TTL default.
11. Member account-margin environment must be account_scope.environment or FIXTURE; risk computation target likewise. Fresh computation cannot refresh stale underlying visible sources. Do not invent a risk-computation input digest or undocumented causal edge from its opaque input hash. Actual model's §5B margin lower bound includes account receipt, reservation completion and ALL domain pin/mark/tier/margin receipts and risk computed_at, plus issuer-resolved extra inputs. Equal timestamps are allowed; actual action order/control/independent GET remains issuer work.
12. Produce coverage using validated values, account/reservation public snapshot hashes and public mark-set hash. `source_record_sha256s` is preserved exactly. No policy cap/leverage/admission verdict, new receipt, qualified flag or fake authority DTO is emitted.

## 5. Canonical content binding and digest limits
- Reuse exactly `risk_v2_contracts._canonical_sha256_v2("LinearRiskDomainCoverageV2", fields)`; no new parser/serializer or dynamic type dispatcher. Top keys/schema remain `schema`, `record_type`, `fields` / `RISK_V2_CANONICAL_V1`.
- `fields` contains ALL ten Coverage fields with their exact names. Member objects contain ALL thirteen Member fields; nested pin, mark, tiers, source/computation references contain every field via the stable closed payload helpers. No audit-time exclusion exists in these DTOs: computed/source times, raw/payload/input hashes, all tier rows/rates/deductions, all IDs/amounts/counts and the entire source-record tuple are committed.
- ProtectionScope/key use existing explicit object payloads; Decimal uses `decimal_text_v2` preserving sign/coefficient/trailing zeros/scale; UTC uses microseconds ISO8601 +00:00; tuples are ordered arrays; object keys are sorted by the one existing canonical encoder; ASCII bytes → SHA256 lowercase.
- Members/marks sort by key; member ID tuples sort lexically; tier rows retain RANGE order, never lexical tier-ID order; source-record hashes sort lexically. Reject bad order; do not silently canonicalize input records.
- Hash API revalidates every available field and intrinsic links: selected key is a member; member keys unique/ordered; pins have Coverage catalog_revision; member mark/tier key/revision/environment links; private reference environments; global ID order/disjointness. Recompute mark_set_sha256 from the member marks and require equality. It has no catalog/account/reservation/now arguments, so it cannot recheck their bodies, aggregate headers, original risk-key completeness or temporal freshness; builder owns those supplied-input checks. Account/reservation hash claims still require actual resolution.
- Source Decimal fields retain <=256 digits and |exponent|/|adjusted|<=1000; A02 DecimalField retains 64 digits/32 fraction. Member aggregate amounts are derived: do not reapply source256. Existing math limits (65536 digits/exponent magnitude, 1,000,000 integer bits) and whole canonical encoded payload <=65536 bytes remain technical bounds. Tier rows 1..1024 each; members/marks 1..100; original component tuples 0..1024. Tuple maxima do not guarantee acceptance: the byte limit can reject lower counts. No truncation/sampling.

## 6. Exact helper/dependency ownership
- Import stable contracts as `c`; use `_fields_v2`, `_identifier_v2`, `_digest_v2`, `_tuple_v2`, `_count_v2`, `_decimal_v2`, `_utc_v2`, `_fresh_v2`, `_key_order_v2` and existing closed scope/pin/mark/observation/computation/account/reservation payload helpers. Use `pin_instrument_v2`, account/reservation/mark public hashes, `_mark_values_v2`, `_gross_components_v2`.
- Import TASK4 `_tier_snapshot_payload_v2`; keep its generic row invariants. Do not add M1-specific first-deduction-zero/LP/rounding proofs or reverse-import `linear_risk_v2`/Task5/Task6/Bybit model.
- Reuse math `decimal_fraction_v2`, `finite_decimal_v2`, and its existing private `_fraction`/`_compare` guards for bounded intermediate sums/comparisons; exact counts use `_count_v2` after sum. No new arithmetic engine or ambient Decimal operations/context mutation.
- New module-local helpers only: `_member_payload_v2(value: object) -> dict[str, object]` (closed structural payload); `_coverage_payload_v2(value: object) -> dict[str, object]` (all structural pass, then intrinsic links); `_catalog_risk_observations_v2(catalog: InstrumentCatalog, *, required: tuple[InstrumentKey, ...], expected_revision: str, now: datetime) -> tuple[InstrumentObservation, ...]`; `_source_record_sha256s_v2(value: object) -> tuple[str, ...]`. Shared existing modules are not edited.

## 7. Fixed errors, actual gates and acceptance
- Reuse `RiskV2Rejected` only; existing nested validators retain their fixed codes. New domain checks use: malformed type/field/member-or-ID-or-source-list order/ID/hash/list or A02 rejection → INPUT_INVALID; technical bounds → RESOURCE_LIMIT; supplied account/key/mode mismatch → SCOPE_MISMATCH; pin/tier revision mismatch → REVISION_MISMATCH; mark/pin/reference/environment/digest content link mismatch → SOURCE_MISMATCH.
- Missing/extra member, catalog risk key, member ID coverage or aggregate mismatch → HELD_COVERAGE_REQUIRED; supplied mark key coverage → MARK_COVERAGE_REQUIRED; duplicate/overlapping component IDs → EXPOSURE_OVERLAP; U beyond table → RISK_TIER_UNVERIFIED; unsupported supplied product → UNSUPPORTED_PRODUCT; visible time failures → CAUSAL_ORDER_INVALID/FUTURE_EVIDENCE/STALE_EVIDENCE as stable helpers define. All names have `RISK_V2_` prefix; expose only fixed code, no values/repr/URLs/provider text. Do not catch arbitrary errors as success.
- Actual producer FIRST calls unchanged `require_catalog_selection(catalog, *, allowed, selected, expected_product, expected_source_environment, expected_revision, now)` with genuine approved inputs. Its returned selected observations never replace the held domain. This builder has no allowed/selected authority information.
- Actual registry must retain/resolve Coverage and all underlying source records; establish supplied source-record tuple correspondence/completeness, native/account/ACK/order classification, current generation and every computation's real input content/times. Generic hashes, labels and timestamps cannot establish these. FIXTURE remains pure-test-only and rejected by actual admission.
- Supported LINEAR gross-domain completeness is distinct from complete actual account/shared capital/debt/external activity gates E04/A08/E16. Unsupported holdings cannot be dropped/relabelled. Actual E05 model consumes every member; source mapping/actual applied leverage/readback qualification stays with M1/M2/M3/Task13. Existing owned reduction EXIT has its own gates.
- Pure acceptance: canonical XAA/YBB fixture with allowed/selected XAA still includes held YBB/pending XAA; all supplied ID/aggregate/pin/source/time/U checks and digest mutations have deterministic tests; absent YBB fails. Root may schedule this definition/builder slice before runtime Task13, but E05-M2-D cannot consume an unadopted/untested substitute and no actual support/launch claim follows.
- Remaining root choices: adopt the required explicit source-record tuple argument plus its 1..1024 bound; adopt this exact canonical/validation recipe and the NEW two-file implementation scope/order. Actual source-record schema/mapping/completeness is deliberately an issuer acceptance gate, not a missing generic implementation formula.

# Current root adoption — original M2-D full binding and two-stage assembly

ADOPTED for SOURCE-FREE D implementation only. Root fully read the original R2 spec/plan, preflight28301B/SHA029509c912e18df7017c882c4cd5c13dd30a0b878e31a551ab5711edc633b851, candidate5204B/SHA120fa92bf5dbcef30740648bf040339b1d274c5b11a3721c7d958493f48c95db, position-quantity closure3055B/SHA56cd4082a1d31ea0b0cc973547424b473f2d63e4f7cf3dc3930388bc971becc0, fresh design review26325B/SHA1599bff82dabafdc2f261f780d1b945335011fef8fe96d0dfc664e598c2bfccb, and golden closure5930B/SHA8e9e59de0489954c4bcd8ca3f78420936024b9908fe901b0ef256d8e3eea13d3. Important represented-position ambiguity is explicitly closed below; no remaining design finding. These documents do not supply actual source/model/issuer/native/account/DEMO qualification.

E integrates mainedee at12e652668c93cefd59aae7990c02b7fe249d5a4c after preserving30incomingpath mappings and verifying all6 retained Bybit/domain source/test bytes unchanged. Original generic5/6 are now real E dependencies, not hypothetical imports. Mainedee has exact PRCI135 and mainCI136 success with fresh actual native36/restart6/restored12zero bad; this accepts those prior main scopes, not unimplemented D. Actual source CLOCK_INVALID remains the earlier NONPASS, no new provider/clock action.

## Scope and exact amendment

Sequentially extend ONLY execution/bybit_linear_model_inputs_v1.py, execution/bybit_linear_model_v1.py and their two existing execution tests. Inputs owns projection/Venue/FullInputs/binder/venue-model digests; model owns BoundCalculation/calculation/assessment builders/bound digest. Inputs never imports model. Original A/B/C bodies/tests stay unchanged unless a concrete composition defect is returned to root before editing. Generic/domain/A02/account/history/native/policy/fixtures/config/workflows remain read-only.

Retain all original D interfaces/fields/recipes/rejection/complete-table/proof obligations in M2 §§3/4A/5/7/8 and original TaskD, except these explicit preimplementation changes:

    bind_bybit_model_inputs_v1(inputs:LinearRiskInputsV2, *,
        preparation:LinearEntryPreparationV2, domain:LinearRiskDomainCoverageV2,
        venue:BybitVenueStateV1, catalog:InstrumentCatalog,
        selected_mark:BybitDecimalFieldV1, now:datetime) -> BybitLinearModelInputsV1

FullInputs required fields, exact order: risk_inputs, preparation, domain, venue, point, catalog, valid_until, model_rule_revision. Catalog is the real existing InstrumentCatalog. Venue/Point field lists are unchanged. Later rebind uses inputs.catalog and inputs.point.selected_mark. No default, fake field reference, JSON pointer/spelling reconstruction or extra loose source store.

Recompute real prepare_linear_entry_v2 from full original inputs and real build_linear_risk_domain_coverage_v2 from the supplied catalog/policy/headers/marks/members/explicit opaque source-record values. Compare validated complete economic outputs/digests and every Point projection; ignore only existing audit assessed_at exclusions. Every risk key gets its real InstrumentObservation/full eleven-field pin and entire Bybit table projection, including Y min_leverage from its actual spec. Selected X observation/table also matches inputs. No selected-only domain, caller-supplied capacity shortcut, Y user-range/A0 substitution or row pruning.

## Exact added catalog commitment

One private closed helper _catalog_input_sha256_v1(catalog:InstrumentCatalog)->str uses ONLY the existing c._canonical_sha256_v2, record_type exactly BybitInstrumentCatalogInputsV1. Exact payload keys:

- schema_version: exact supplied catalog int;
- revision: catalog revision;
- declared_universe: original ordered closed InstrumentKey payloads;
- max_age_microseconds: exact int `(days*86400+seconds)*1000000+microseconds` from exact timedelta;
- expires_at: original UTC microsecond text;
- observations: original ordered objects with exact keys key,native_instrument_id,parser_revision,source_rule_revision,specification_revision,source,response_time,raw_row_sha256. source has exactly source_id,source_environment,endpoint,received_at. Key/time/scalars use the existing closed recipes.

Revalidate all A02 raw_row_canonical/spec/raw_row_sha256/specification_revision links with the existing A02 validator before committing validated preimages. Do not serialize raw bytes, duplicate its parser or use LINEAR-only pin_instrument_v2 on unrelated catalog rows. Other declared SPOT/LINEAR observations retain their own product; risk members remain LINEAR. Changed unusedZ source receipt/raw row must change this digest even when semantic catalog revision/minimum expiry is unchanged. No silently sorted/truncated/sampled input or raised64KiB cap.

FullInputs compact model digest has EXACTLY eight keys: binding_sha256,risk_domain_sha256,requirements_sha256,venue_state_sha256,point_input_sha256,catalog_input_sha256,valid_until,model_rule_revision. Existing child recipes supply values. Record_type remains BybitLinearModelInputsV1; D had no shipped implementation. Historical seven-key framing remains superseded evidence. Point/Venue/Bound and all A/B/C/domain hash recipes are unchanged.

Independent framing literal is companion2026-11-03-bybit-linear-model-m2-d-golden.json:722ASCIIbytes/noBOM/CR/LF, SHA476db19b6adeaac6439ee95e637ea48c4bf2b0a04b5bd1b869f71bb757fceefd. Reviewer independently checked framing and exactly one unchanged accepted canonical-helper call matched. Placeholder child hashes do NOT form an admissible FullInputs graph; tests must independently build full graph payloads as well.

## Deterministic expiry and completion

After complete validation/replay set E to exact UTC minimum of these EIGHT existing bounds: preparation.sizing.envelope.valid_until; inputs.proposal.entry.valid_until; inputs.costs.valid_until; inputs.costs.next_funding_at; inputs.costs.holding_until; inputs.minimum_amount.valid_until; venue.valid_until; catalog.expires_at. Point/FullInputs/Bound/margin/LP all use exactly E. Reject now>=E and any stored model expiry different from recomputed E, including a forged shorter expiry. Preserve original envelope Q/P/stop/origin/expiry. No new TTL or source-receipt+age extension.

Funding/holding/expiry schedules, audit now and assessed_at are NOT completion predecessors. All actually consumed available source receipts/computations, every page/mark/pin/catalog observation (including retained unusedZ), held/profile/fee/settings/wallet source, domain margin/risk readback, account/reservation/cost/minimum and fee-document capture precede margin completion. LP completion>=margin completion and all additional work/sources, both completions<E. Each individual source/time is fresh under its explicit age bounds; no aggregate-max timestamp substitutes for an old child. Equality timestamps are allowed, not proof of independent GET.

## Represented positions and actual mapping boundary

For EVERY required risk key and each BUY/SELL side require exact NON-NET bounded sums:

sum(account ACCOUNT_POSITION base_quantity for key/side) == sum(all supplied raw held-position size.value for key, mapping Buy→BUY and Sell→SELL).

All raw held positions have the original supported positive-size/idx0/Normal profile, unique one-way key/idx order and exact full domain/catalog pins. Missing/extra raw/generic Y, changed side/size or opposing generic positions cannot be hidden by equal headers or netting. Empty mathematical sums are exact0, not invented source observations. Multiple same-side generic components may sum to one raw position; no one-component/cardinality/native-ID/count/margin formula is invented. This mismatch raises existing RISK_V2_HELD_COVERAGE_REQUIRED at full binder before selector and propagates unchanged through calculate_bound. Earlier malformed/source/revision errors retain original owners. Selected X flat independently rejects raw X and pending/unresolved X.

Mandatory wallet available-margin equation remains exact walletBalance-totalPositionIM-totalOrderIM-locked-bonus==account.available_margin, under the original no-borrow supported profile. Do not substitute USD available balances or subtract local reservation twice. Domain member/header sums and source/pin/mark/full-table/ID/coverage/generation links remain mandatory.

Opaque domain.source_record_sha256s are registry-record hash VALUES, NOT Bybit source-ref hashes. No invented equality or raw preimage. Raw held orders remain validated/committed, including additional records. Actual ACCOUNT_ENTRY_ORDER classification, native-ID/ACK/local reservation true disjointness, registry authenticity/contents, raw occupied margin/risk/count/equity/shared-capital attribution need a real source-owner projection contract BEFORE runtime admission. Equal quantities and copied hashes do not prove those facts. They cannot be silently filtered, called qualified, or counted as completed by pure D.

## Error ownership, calculation and assembly

Exact closed graph/scalars/tuples/tz/classes are validated BEFORE any unchecked equality/hash/arithmetic, including forged/partial/subclass children. Reuse accepted bounded Fraction math and one canonical encoder. Only CatalogRejected maps to fixed RISK_V2_INPUT_INVALID without text, including at the new static catalog helper; A02's own resource rejection follows that existing translation. Direct new technical bounds use RISK_V2_RESOURCE_LIMIT. Existing nested Risk errors keep their owner. No arbitrary catch, reverse import, generic authority framework or new error enum.

Full binder/preparation failures propagate unchanged before C; actual C Rejected remains unchanged. Rebind/replay full inputs, complete selected C prefix/first-feasible result and B full five-economic-input/table LP proof; no matching supplied digest alone is evidence. Bound self digest binds all original exact fields. Pure calculation creates no clock/IDs/receipt.

Preserve two-stage order: completed unsealed calculation→actual owner captures margin completion→margin assembly→any added LP binding work→actual owner captures LP completion→LP assembly. Supplied reference checks do not authenticate the owner. Margin input_sha256 is generic binding; LP input_sha256 is actual margin digest. Exact canonical ratio/no-root tags, consumed full-domain/admission IDs, source/raw/full table/proof/model/exact hashes and E all match original requirements. Generic Task6 remains responsible for exact buffer/reserve/capacity and PLAN missing evidence/SUBMIT strictness; no leverage readback is manufactured. HARD fee/SELL hard quote errors retain their generic owner.

## Required implementation evidence

Genuine behavioral RED/GREEN with real synthetic X/Y and non-riskZ catalog, selectedXflat/heldY/pendingY/proposedX; independent raw/generic position omissions/extra/side/size/opposing sides, complete table/projection/minlever and every represented source/total/time mutation. Raw selected mark cannot be inferred from a generic Decimal/hash. Changed relevant receipt invalidates reuse; unusedZ valid raw/receipt changes new catalog digest. Verify all eight expiry minima, exact equality rejection and audit-only unchanged economic hash. Full table/proof/prefix/ratio/no-root/hard fee/SELL/readback/causal fake-owner cases remain original. Demonstrate actual mapping/authority limitations explicitly.

After stabilization run original scoped E05+domain+accepted generic Tasks1–6/math+V1 risk regression ONCE with exact new dependencies and touched Ruff/strictmypy4. No full/native/PAPER/DB/runtime/coordinator suites. Save actual commands/results/failures and source/report/execution/artifact/protected freezes; root fresh independent implementation/wholeE review and exact CI/main remain. Same-client capture M2-E and actual M3/source-owner/current publisher/native/DEMO/protection/server/PC-off/full63 are still required later; no partial-feature final.

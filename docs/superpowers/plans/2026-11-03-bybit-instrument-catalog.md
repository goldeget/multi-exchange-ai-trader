# Bybit Instrument Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Root owns all Git and passing-main integration.

**Goal:** Supply accurate pure SPOT/LINEAR source definitions and a complete, fresh declared-universe catalog for later trusted producer/risk integration.

**Architecture:** Two new stdlib-only modules separate immutable source parsing from coverage/freshness/selection. Reuse the current shared product key/helper and keep legacy execution behavior intact.

**Tech Stack:** Existing Python3.12/pytest/Ruff/mypy; native SDK1.231.0 and dependencies unchanged.

**Spec:** docs/superpowers/specs/2026-11-03-bybit-instrument-catalog-design.md

## Global Constraints

- Worktree F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102, accepted baseline main46d4d13de3f990e875c34bba3b4c34ffc3f81609, branchgpt/bybit-catalog-risk-contracts-20261103. Root owns Git; no worker Git mutation/new worktree/subagents.
- Four NEW files only: definitions/parser+test, catalog+test. Root owns spec/plan/factualdocs separately. Existing shared product, adapter allowlists, native, risk, worker/settings/DB/locks/UI stay unchanged. No execution consumer or capability is added.
- Exact fields/types/constants/errorallowlist/canonical payload/resource/time/source/product rules in the spec are binding. No coercion/aliases/defaultproduct, schema duplication, made-up venue limit, capital, strategy approval, universe or operating TTL.
- Both modern order-kind quantity caps required. KnownNotPublished only for supported-rule max_quote_amount; deprecated SPOT caps never fallback. SPOT and USDT LinearPerpetual fields remain distinct. Regular Limit/Market source metadata is not PostOnly/RPI/conditional/protection support or qualification.
- Values and source/environment labels are not authority/current issuer/transport/DEMO mapping. Revalidate forged/copy/missing/nested fields and raw digest/spec; caller-valid time replacement cannot be detected from row-only evidence. No partial result or unknown text diagnostic.
- Product code stdlib+sharedrootproduct only; no execution/native/HTTP/account/credential/environment/file/DB/clock/client/network effects. Workers use synthetic fixtures with defaultstores/envsource OFF; actualprivatekey/UID/note/env/vault/trialDB/provider/venue/browser/WSL/native/order/paidAI/server access0.
- Behavioral RED then focused GREEN+covering legacy unit checks, separate stdout/stderr/exit static receipts and final source hashes. No collection/import error as featureRED. Exact full hosted CI/main owns project regression; do not rerun full local Windows/native/PAPER suite. Preserve original evidence/worktree.

## Review Focus

1. Same symbol across products or omitted cap/source field must not silently inherit another product/orderkind or become KnownNotPublished. Task1 pins both products and every missing/type/range case.
2. Hostile subclasses, cyclic/oversized JSON and forged raw bytes must fail before hooks/unbounded allocation/Decimal conversion; Task1 pins exact budget accounting, duplicate-key decode and canonical reparse.
3. Hashes must reflect supported raw precision/rules/domain/selection semantics, while timestamp/sourceID/irrelevant raw changes do not fabricate semantic changes; Task1+2 pin mutation tables and low Decimal precision.
4. Declared completeness/freshness must cover ALL observations, including unselected products, with same source domain/id and safe datetime overflow; Task2 pins hidden stale/extra/duplicate/cross-source data.
5. Static valid observations/old snapshots cannot authorize execution or prove envelope source; both tasks pin fresh-process import/effects and copied/forged result boundaries, without widening existing execution gates.

### Task 1: Immutable product definitions and bounded single-symbol parser

**Files:** Create src/multi_exchange_trader/market_data/instrument_definition.py and tests/market_data/test_instrument_definition.py only.

**Interfaces:** Produce CatalogRejected, SourceEnvironment, four fixed constants (PARSER_REVISION, LINEAR_SOURCE_RULE_REVISION, SPOT_SOURCE_RULE_REVISION, endpoint constant named INSTRUMENT_ENDPOINT), DecimalField, CatalogSource, KnownNotPublished, LinearInstrumentSpec, SpotInstrumentSpec, InstrumentObservation, parse_bybit_instrument_response(body:object,*,expected:InstrumentKey,source:CatalogSource)->InstrumentObservation, validate_instrument_observation(observation:InstrumentObservation)->None. Shared internals _canonical_json_bytes(value:object)->bytes, _key_payload(key:InstrumentKey)->dict[str,str], _utc_datetime(value:datetime)->datetime. No catalog implementation. Exact DTO fields/constant values in spec; all mandatory, raw repr=False.

- [ ] Write synthetic regular SPOT/LINEAR single-row envelopes and minimal importable stubs; behavioral assertions must fail for missing numeric/identity/scale/validation semantics, not collection/import errors. Test current shared same-symbol product IDs, explicitUSDT/base/status/contract/isPreListing bool, wirepriceScale strictstring vs storedint, all modern/deprecated/missingcaps and unit-specific ranges, fixed-source rules/absence.
- [ ] Pin Decimal raw/value/scale exactconsistency,64digits32fraction boundaries, signs/aliases/bool/subclasses/NaN/exponent/float rejection and contextprecision2 independence. Pin source mandatory fields/id/env/path/standardoffsetUTC normalization versus naive/custom/ZoneInfo rejection.
- [ ] Pin JSON depth8/nodes4096/container128/scalars128/int64/encoded65536 boundaries; cycles/unknown exactsubclasses/hostile hooks/floats/duplicate rawkeys/noncanonical bytes/oversized decode/giantinteger/invalidUTF/recursion/overflow; fixed errors containing no unknown marker. Test rawrowhash vs specsemantic hash, extra fields/time/sourceid exclusions, scale/source rule changes and no false envelope-receipt guarantee.
- [ ] Implement typed narrow parser/DTO/revalidation/sharedhelpers per spec, avoiding duplicate algorithms and constructor recursion. Test forged/missing exact and nested values revalidated and originalmutableinput notretained. Fresh isolated process imports and evaluation/constructors use no application/native/network/private I/O or clock.
- [ ] Run focused new tests plus tests/test_trading_product.py and existing tests/market_data (exclude not-yet-created catalog test). Ruff new2files and strict mypy newsource; exactcommands/environmentOFF/stdout/stderr/exit receipts. Record genuineRED vs harnessfailures separately. Freeze bothfileSHA256 and write task-1-report.md with ownership/readback/selfreview/limits; root commits and dispatches fresh dualtaskreview beforeTask2.

### Task 2: Declared-universe catalog, freshness and exact selection

**Files:** Create src/multi_exchange_trader/market_data/instrument_catalog.py and tests/market_data/test_instrument_catalog.py only; Task1 files frozen unchanged.

**Interfaces:** Consume Task1 classes/revalidation and three named sharedinternals. Produce CATALOG_SCHEMA_VERSION=1, InstrumentCatalog(schema_version,revision,declared_universe,observations,max_age,expires_at), computed complete_for_declared_universe; build_instrument_catalog(observations:tuple[InstrumentObservation,...],*,declared_universe:tuple[InstrumentKey,...],now:datetime,max_age:timedelta)->InstrumentCatalog; require_catalog_selection(catalog:InstrumentCatalog,*,allowed:tuple[InstrumentKey,...],selected:tuple[InstrumentKey,...],expected_product:TradingProduct,expected_source_environment:SourceEnvironment,expected_revision:str,now:datetime)->tuple[InstrumentObservation,...]. Exact semantics/payload/errorcodes in spec; all args mandatory, no clockdefault.

- [ ] Write minimal importable stubs and obtain behavioralRED for exact declared coverage/canonical order/source consistency/semanticrevision/freshness/selection rather than import errors. New tests may build synthetic observations through Task1 parser, no new operational seed/cap/TTL.
- [ ] Pin1..100 exacttuples/no subclass/duplicate/missing/extra keys, mixedproduct same-symbol identity, same-source id/env/path with varyingreceivedtimes, full catalog validation including unselectedstale/forged/otherproduct observations. Manual noncanonical constructor mustreject; build canonicalizes only accepteduniqueunorderedtuples.
- [ ] Pin exact canonical revision payload fields from spec, goldenfixture digest, times/sourceid/rawextra change independent, supportedfilter/scale/domain/max_age change bound. Catalog has no currentissuer authority; oldcatalog+oldexpectedrevision may satisfy staticchecks beforeexpiry but no runtimebinding/activation exists.
- [ ] Pin exactstandarddatetime/timedelta/subclasses/future/inverted/naive/overflow/expiry equality; min(response/received)+TTL acrossallobservations, now>=expiry rejected. Selection returns selectedinputorder, no unknownallowed filtering, allallowed/selected match expectedproduct while fullmixedcatalog remainsvalidated; empty/dupe/subset/revision/env/wrongtypes fixedreject.
- [ ] Implement via sharedhelpers/revalidation with no Task1/legacy edit, no new source/clock/credential/native/client/consumer I/O. Fresh isolated effect checks on moduleimport/build/require with syntheticvalues. Run bothnew suites+sharedproduct+existingmarket_data tests; Ruff2newfiles and strictmypy2newsources. Freezehashes and task-2-report.md; root commits/freshdualreview.

## Integration gates and continuous next work

Root self-review/record preflight table, exact BASE per task, frozen source receipts, independent spec+quality gates then broad wholebranch review and exact full hosted CI/main36/6/12. Root may combine reviewed adjacent contract work on this branch before its final wholebranch gate; no current source-only result is overallproduct completion. After this plan continue A04/A07, trusted producers, economics/native/protection/recovery, policy/worker/UI/bootstrap and actual authorized acceptance. No interim final completion message. LIVE, extra paidAI and newserver spending remain prohibited; prior authorized DEMO only after admission/protection/native gates are genuinely met.

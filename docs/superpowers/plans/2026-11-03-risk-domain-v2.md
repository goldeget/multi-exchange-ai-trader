# Risk V2 Full-Held Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** PROPOSED; preparation only. Root fresh review/adoption precedes any code; no implementation/tests/Git action occurred while authoring this candidate.
**Goal:** Implement the pure §17 member/coverage contracts, builder and deterministic hash before E05-M2-D needs the domain, retaining Task13 actual issuer gates.
**Architecture:** One new value/builder module imports stable contracts, TASK4 tiers, exact math and public A02 APIs. It commits explicit source-record hash values while proving only the supplied-data relationships it can inspect. Producer selection/authenticity/completeness remain separate.
**Tech Stack:** Existing Python dataclasses/datetime/Decimal/Fraction/hash engine, existing pytest/Ruff/strict mypy; no dependency/install/network/runtime changes.
**Spec:** `F:/자동매매프로그램/final-completion-audit-20261103/A07_RISK_DOMAIN_V2_SPEC_CANDIDATE_20261103.md` plus original E design §5/5A/5B/6/7/17 and original plan Task13 Step3B.

## Global Constraints
- Only NEW `src/multi_exchange_trader/execution/risk_domain_v2.py` and NEW `tests/execution/test_risk_domain_v2.py`; all existing generic source/tests/docs read-only during this slice.
- Before implementation root verifies the exact stable source SHA pins recorded in the companion spec and adopts the explicit `source_record_sha256s` API amendment. If pins drift, review relevant changes; do not quietly implement against WIP.
- No actual issuer/receipt, Bybit model/source implementation, selected authorization, order submission, DB/native/provider/SDK/AI/server/install/Git operation is authorized by this plan.
- Exact tuple containers, member/key/ID/source-list order, full fields canonical binding, source-vs-derived Decimal roles, original resource/error/time contracts and one hash engine are mandatory.
- No proposed quantity in builder; U is held+pending gross only. Do not net, filter unsupported exposures or apply X's user leverage bounds to Y. No fake provenance/authentication DTO.

## Review Focus
- A selected-only fixture must fail when held Y lacks mark/tier/member/IDs; removing Y from allowed does not remove its held exposure. Task2 tests own this.
- A forged nested record must fail with a fixed typed error before cross-record comparisons/hooks; a plausible source-record tuple may still be non-authentic. Tasks1/3 own both boundaries.
- Fresh risk computed_at cannot refresh stale source receipts, and catalog age equality differs from policy age equality. Task3 owns time-boundary tests.
- Coverage alone lacks original account/reservation/catalog/now; hashing cannot certify their bodies, aggregate totals or currentness. Task1/3 prove the hash/builder distinction.
- A change in any nested receipt/time/tier/ID/amount/hash/source-list field changes the digest or is rejected; no omitted field or lexical reordering of tier IDs. Task1/3 own canonical tests.

## Task 1: Exact value boundary and one canonical digest
**Files:** Create the two files above; do not create helper/fixture modules.
**Consumes:** Stable `c` payload/validation/hash helpers, TASK4 `_tier_snapshot_payload_v2`, existing math guards and public A02 type definitions pinned in the spec.
**Produces:** Exact frozen `LinearRiskDomainMemberV2`, `LinearRiskDomainCoverageV2`; `risk_domain_sha256_v2(value: LinearRiskDomainCoverageV2) -> str`; the four private helper signatures fixed in spec §6; importable builder signature stub for the next task.
- [ ] Write focused failing tests `test_exact_domain_field_lists_no_defaults`, `test_coverage_hash_closed_payload`, `test_member_id_source_hash_order`, `test_coverage_hash_revalidates_forged_records`, using pure fixtures derived from stable existing test/source contracts. No exchange values are promoted to a real rule.
- [ ] Add importable declarations/stubs only in the NEW module; run `python -m pytest tests/execution/test_risk_domain_v2.py -q` in the existing approved environment and record genuine RED assertions/NotImplementedError, not import/dependency failure.
- [ ] Implement fields and closed payload helpers; structural pass covers ALL inputs before intrinsic cross-record links; preserve nested payloads and every field. Share `c._canonical_sha256_v2`, not a second encoder. Recompute mark-set hash from member marks; account/reservation hash claims remain opaque here.
- [ ] Implement hash API intrinsic checks and exact source-record tuple validation (1..1024 ascending unique lowercase digests). Use an independently specified literal scalar payload/ASCII-byte oracle in tests; no production serializer/parser duplication.
- [ ] Assert SHA changes for each independently valid field mutation, or exact fixed rejection for a mutation violating an intrinsic link. Assert tuple/source Decimal representation/tier RANGE-order preservation and unchanged ambient Decimal context. Run Task1 focused tests to GREEN; retain builder stub pending Task2.

## Task 2: Full supplied held-domain builder
**Consumes:** Task1 DTO/hash/helpers; `build_instrument_catalog(observations, *, declared_universe, now, max_age)`, `validate_instrument_observation`, `pin_instrument_v2`; stable account/reservation/mark validators/hashes, `_mark_values_v2`, `_gross_components_v2`, exact math.
**Produces:** `build_linear_risk_domain_coverage_v2(*, policy, catalog, catalog_revision, coverage_revision, source_generation, account, reservations, marks, members, source_record_sha256s, now) -> LinearRiskDomainCoverageV2` with exact annotations from spec §3, no defaults or added arguments.
- [ ] Write failing `test_selected_x_keeps_held_y`, `test_missing_extra_member_or_ids_rejected`, `test_member_aggregates_match_headers`, `test_builder_binds_actual_supplied_snapshot_hashes`, `test_u_uses_held_pending_gross_only`.
- [ ] Use original counterexample: declared XAA/YBB, genuine producer allowed/selected XAA, account held YBB/pending XAA/candidate XAA. Builder receives no allowed/selected argument; test demonstrates Y remains present and no proposed size is consumed. Assert every missing Y input/ID/amount/count fails at the relevant fixed code.
- [ ] Run those tests and capture genuine RED. Implement spec §4's structural pass, public A02 catalog reconstruction with original canonical/schema/revision/expiry checks, exact supplied account/mode agreement and risk-key union. Never fabricate selection arguments to reuse `require_catalog_selection` inside builder.
- [ ] Implement all-field pin equality, supplied-mark equality, tier key/spec/environment links, exact member ID set/order agreement and global overlap prevention. Compute member sums with bounded Fraction/int guards and require exact account/reservation totals; do not derive count semantics from ID cardinality.
- [ ] Implement per-key held+pending U with stable gross helper and range-only table coverage. Tests pin U=0, U=last_upper, U>last_upper and opposing BUY/SELL gross; valid held Y remains accepted despite X user-leverage range not fitting Y. Generic full-table row validation remains TASK4's existing invariants.
- [ ] Finish construction using public account/reservation/mark hashes, preserved source tuple/revision/generation, `selected_entry_key=policy.scope.instrument`. Run Task1+Task2 tests to GREEN.

## Task 3: Time/resource/error boundaries and acceptance evidence
**Consumes:** Complete Task1/Task2 public interfaces; stable `RiskV2Rejected` vocabulary and exact math limits. No Task5/Task6/Bybit model import.
**Produces:** Deterministic negative/boundary evidence and a root-reviewable two-file diff; no runtime capability claim.
- [ ] Add `test_all_visible_sources_fresh_independently`, `test_equal_timestamps_and_policy_age_boundary`, `test_catalog_expiry_boundary`, `test_future_and_reversed_receipts`, `test_fresh_computation_cannot_launder_old_source`. Each mark uses quote age; every other timestamp in spec §4.10 uses evidence age; catalog delegates its original stricter expiry semantics.
- [ ] Add `test_public_private_environment_links`, `test_opaque_computation_hash_not_reconstructed`, `test_source_list_values_bound_not_authenticated`, `test_policy_scope_not_selection_authority`, `test_hash_without_original_inputs_does_not_claim_builder_proof`. Well-formed invented registry hashes/false source claims cannot be called authenticated by this module; actual rejection is the later issuer gate.
- [ ] Add `test_typed_error_precedes_cross_record_comparison`, with forged missing fields/subclasses/bool counts/mutable containers/hostile equality-repr-tz hooks; assert no hook execution or data-bearing error. Invalid local structure is rejected before a competing supplied scope mismatch.
- [ ] Add source256/exponent/A02 limits, derived large-coefficient preservation, 1..100 keys/marks/members, 0..1024 account/reservation components/ID totals, 1..1024 tier rows/source hashes and whole canonical 65536-byte boundary tests; excess rejects without truncation. Use existing approved synthetic fixtures and technical boundary constructions, not invented financial assumptions.
- [ ] Verify fixed INPUT_INVALID/RESOURCE_LIMIT/SCOPE_MISMATCH/SOURCE_MISMATCH/REVISION_MISMATCH/HELD_COVERAGE_REQUIRED/MARK_COVERAGE_REQUIRED/EXPOSURE_OVERLAP/RISK_TIER_UNVERIFIED/UNSUPPORTED_PRODUCT/time codes exactly as companion §7. Every error string equals its fixed `.code`; unknown code constructor behavior stays unchanged.
- [ ] Run new tests, then ONE focused stable regression pass: `python -m pytest tests/execution/test_risk_domain_v2.py tests/execution/test_risk_v2_math.py tests/execution/test_risk_v2_contracts.py tests/execution/test_linear_risk_v2_valuation.py tests/execution/test_linear_risk_v2_tiers.py -q`.
- [ ] Run touched-only `python -m ruff check src/multi_exchange_trader/execution/risk_domain_v2.py tests/execution/test_risk_domain_v2.py` and `python -m mypy --strict src/multi_exchange_trader/execution/risk_domain_v2.py tests/execution/test_risk_domain_v2.py` in the current approved environment; record absent tools/failures as non-passes, do not install or expand scope.
- [ ] Root reviews exact diff, hashes, commands/results and original stable-pin recheck; independent review may proceed through the already-authorized execution method. Do not run broad/native/PAPER/network suites for this pure slice without a newly justified need.

## Ordered integration gates after pure acceptance
1. Root adopts the candidate API/canonical/resource choices, then the pure two-file implementation is reviewed/tested and frozen. This slice may precede runtime Task13 and is a prerequisite for E05-M2-D domain consumption.
2. E05-M2 uses the actual domain object and hash with ALL members. It must implement accepted M1 source/mode/math semantics independently; a pure builder pass does not qualify the model/source.
3. Task13 actual producer separately validates genuine allowed/selected through A02, retains actual source registry records, authenticates source-list completeness/content, account/native/ACK/pending coverage and causal model inputs/current generation. No private receipt registry or actual issuer is built in this plan.
4. Full account/shared capital/E04/A08/E16, independent leverage readback and M3 actual DEMO acceptance remain separate. Until actual gates close, financial ENTRY remains blocked; pure tests/hash equality are never a launch or actual support claim.

**Remaining root decisions:** adopt the explicit required source-record tuple and 1..1024 bound; adopt the companion exact recipe and early pure-slice ordering. No questionnaire/operator financial-value choice is needed to implement this pure boundary. Actual source-record selection/schema/completeness remains issuer-owned acceptance work.

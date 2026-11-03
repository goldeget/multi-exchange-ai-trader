# Exact Strategy Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task; root owns Git. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one independently reviewable pure A04 metadata catalog/exact-reference selection component, preserving all existing runtime blockers.

**Architecture:** A new execution module consumes existing StrategyReference and common InstrumentKey. Shared private validators enforce constructor intrinsic invariants and revalidate public build/require inputs; canonical metadata resolution has no IO or runtime authority. Existing selectors, settings and worker are untouched.

**Tech Stack:** Existing Python >=3.12,<3.13, stdlib dataclasses/hashlib/json/re/typing, pytest >=8,<10, existing ruff/mypy; no dependency/SDK changes.

**Spec:** `docs/superpowers/specs/2026-11-03-exact-strategy-catalog-design.md`.

**Status:** Root-adopted after independent preflight; no routine user confirmation. Original external drafts/candidate are preserved. All code/tests/reviews/CI remain future evidence at this plan seed.

## Global Constraints

- R = `F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102`; drafting read at HEAD `46d4d13de3f990e875c34bba3b4c34ffc3f81609`, tree `6266b5e4c50bec1c8dad24e8eea07cdd7cb7f413`; fresh execution identity is required because A02 may advance it.
- Only create `src/multi_exchange_trader/execution/strategy_catalog.py` and `tests/execution/test_strategy_catalog.py`; no existing repository file modification, package re-export, schema2, settings DB, worker/runtime consumer or registry change.
- Reuse `modes.StrategyReference(strategy_id: str, revision: str)` and root `trading_product.InstrumentKey(venue: Literal["BYBIT"], product: TradingProduct, symbol: str)`; no duplicate reference/key/category mapper and no default fields.
- Exact public fields/signatures/canonical bytes/codes/precedence are spec §§3–6; do not add public helper APIs or choose alternative names mid-task.
- All metadata strings/tuples/types strict; identifiers96 ASCII, SHA lowercase64, major tuples100, config schema1..2147483647, catalog schema1, only APPROVED resolves. Entry100×per-entry instrument100 gives a derived worst-case10000 memberships; validate tuple lengths before nested traversal/hash, with no independent unreachable aggregate branch/test.
- Descriptor/catalog/selection constructors validate intrinsic invariants through shared private validators; build/require independently revalidate forged/missing objects. No build↔constructor recursion. Selection requires nonempty allowed, canonical-descriptor/exact-reference subset, APPROVED/domain/environment consistency; it proves no catalog membership/current issuer.
- StrategyCatalogRejected accepts only the eleven exact string codes; other inputs raise fixed ValueError("INVALID_STRATEGY_CATALOG_REJECTION_CODE") without supplied-value interpolation or nested exception context.
- No actual approved seed, capital, TTL, policy, current-generation issuer, network/provider/private/default store/env/key/note/vault/trial DB/account/native/order/AI/server access.
- Caller-constructible catalog/selection are metadata, not authority. Old catalog + matching old expected revision can pass; this limitation is tested and reported explicitly.
- Implementation is authorized within the two-file task scope. Fresh builder identity/ownership checks must use fixed offline interpreter `C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe`; set source environment variables `TRADER_CREDENTIAL_FILE`, `TRADER_ACCOUNT_PROFILE_FILE`, `BYBIT_ENV_FILE`, `AI_ENV_FILE`, `ANTHROPIC_ENV_FILE` to exact `off` before any runner invocation. Do not inspect secret values, discover credentials, install/sync dependencies or bootstrap services.
- Separate stdout/stderr/exit artifacts, a source hash freeze and verification report are mandatory in the root-assigned A04 SDD evidence directory. Root later owns full hosted/main verification; no full local native/PAPER/Windows project suite.
- No automatic commit/push/PR/CI/deploy in this plan. Root owns Git integration after independent review; any authorized commit must include only these two files.

## Review Focus

1. Constructor and revalidation paths must reject altered frozen/missing-field values before hash/equality/str hooks or type errors can leak (Task1 intrinsic/revalidation tests).
2. Set-valued metadata order must not change catalog hash or duplicate/conflict meaning, while output ordering must preserve the caller's request (Task1 canonical tests).
3. Invalid unused allowlist references must reject the entire request rather than being filtered or bypassed by selected-only resolution (Task1 require tests).
4. Same symbol across SPOT/LINEAR and DEMO/LIVE must stay separate without treating domain inclusion as actual qualification (Task1 scope tests).
5. Reusing an old catalog and old expected revision must be documented as static success, without fabricating current-generation revocation protection (Task1 trust-limit test).

---

## File structure and task boundary

| File | Responsibility |
|---|---|
| Create `src/multi_exchange_trader/execution/strategy_catalog.py` | Frozen records, fixed rejection, private structural/canonical helpers, two public build/require functions |
| Create `tests/execution/test_strategy_catalog.py` | Synthetic metadata fixtures, strict identity/hash/domain/selection/revocation-limit boundary tests |

There is one implementation task. Data declarations, canonical digest and strict require form one coherent observable contract; splitting them into skeleton/hash/selection tasks would create artificial incomplete gates. Final independent spec/code review is a gate, not a second coding subsystem.

### Task 1: Pure exact strategy metadata catalog and selection

**Files:**
- Create: `src/multi_exchange_trader/execution/strategy_catalog.py`
- Test: `tests/execution/test_strategy_catalog.py`
- Read-only integration references: `src/multi_exchange_trader/execution/modes.py`, `src/multi_exchange_trader/trading_product.py`, `execution/worker.py`, `execution/runtime_policy.py`, `research/strategy.py` (execution/research paths under the same package).

**Interfaces:**
- Consumes: existing `StrategyReference(strategy_id: str, revision: str)` and `InstrumentKey(venue: Literal["BYBIT"], product: Literal["SPOT", "LINEAR"], symbol: str)`; the shared constructor validates key fields when reconstructed.
- Produces: exact `ApprovalStatus`, `ApprovalEnvironment`, `StrategyDescriptor`, `StrategyCatalog`, `StrategyCatalogSelection`, `StrategyCatalogRejected(code: str)` declarations from spec §3 (all fields required, frozen records, no extras), with constructor invariants and safe rejection precedence from spec §4.
- Produces: `build_strategy_catalog(entries: tuple[StrategyDescriptor, ...]) -> StrategyCatalog`.
- Produces: `require_strategy_references(catalog: StrategyCatalog, *, expected_catalog_revision: str, allowed: tuple[StrategyReference, ...], selected: tuple[StrategyReference, ...], instrument: InstrumentKey, environment: ApprovalEnvironment) -> StrategyCatalogSelection`.
- Does not produce: issuer/generation/capability, approved live strategy, policy persistence, candidate/plan, worker or execution authority.

- [ ] **Step 1: Fresh builder preflight and evidence assignment.** Read the binding adopted spec+this plan, applicable AGENTS/skills and current R identity/origin/branch/HEAD/status. Confirm A02/common-key API has not drifted and these two files are free of someone else's changes. Record root-assigned absolute evidence directory and fixed interpreter version; set the five source variables to `off`. If files now exist or shared contract differs, return exact conflict to root without overwriting/adapting. No user question is required.

- [ ] **Step 2: Create minimal importable API stubs and write behavioral failing tests.** In the new module declare all exact fields/signatures from spec §3; initial frozen dataclass constructors have no validation, build/require raise NotImplementedError and rejection class has the declared callable/code attribute without code allowlisting. This is interface scaffolding only. In the new test file use synthetic `fixture-trend/v1`, `fixture-trend/v2`, `fixture-approval-v1`, lowercase hashes and explicit keys. Tests must import/collect successfully, then fail because required behavior is absent. Never count missing module/name/collection failure as RED. Pin these groups:

| Test name/group | Exact assertions |
|---|---|
| `test_record_contract_uses_existing_types_and_required_fields` | imported reference/key are existing classes; descriptor11/catalog3/selection5 declared fields match spec; no defaults; frozen; descriptor/catalog constructors enforce their intrinsic spec §4 invariants; negative selection cases belong to Step6 |
| `test_build_empty_catalog_has_exact_canonical_revision` | empty build succeeds schema1/entries(); exact revision equals `sha256:1038daeddcbbd28c7ee6feb49e2ddbb6ea1587d4e9f8b3282ef5ed1813165f23`; independently hash exact bytes `b'{"entries":[],"schema_version":1}'` with stdlib in test, without invoking module canonical helper |
| `test_build_preserves_entries_but_hashes_set_order_canonically` | object identity/input entry order preserved; entry/domain/env permutations have same revision; canonical JSON oracle uses exact schema/sorts/options from spec §5 |
| `test_each_semantic_field_and_removal_changes_revision` | parameterize every descriptor payload field incl reference components, schema/engine/hashes, product/symbol/domain/source/status/env plus entry deletion; every valid change differs |
| `test_duplicate_and_conflicting_exact_references` | equal canonical duplicate→DUPLICATE; domain/env order-only duplicate→DUPLICATE; different payload→CONFLICTING; same ID/different rev valid |
| `test_strict_record_field_and_bound_rejections` | descriptor/catalog constructors and build revalidation reject spec §4 malformed fields, altered key/reference and missing attrs, latest variants/schema bool/0/2147483648/ASCII len97/hash errors; fixed code, no raw input or unsafe hooks |
| `test_collection_resource_limits` | exact bounds100/101 for catalog/domain before nested traversal/hash; distinct values avoid duplicate masking; environment2 succeeds/3 fails;10000 is derived, not independently over-cap tested; allowed/selected bounds belong to Step6 |
| `test_rejection_code_constructor_is_closed` | all eleven codes accepted exactly; str subclass/nonstring/custom code raises ValueError with exact INVALID_STRATEGY_CATALOG_REJECTION_CODE text, no supplied value |

- [ ] **Step 3: Run the new tests and verify behavioral RED.** From R run fixed-interpreter `-m pytest -q tests/execution/test_strategy_catalog.py`. Expected: successful collection followed by assertion failures for missing intrinsic rejection and NotImplementedError on build behavior. Capture red-tests.stdout.txt/red-tests.stderr.txt/red-tests.exit.txt separately; record collected count and representative behavioral failure in report. A collection/import failure is a harness defect, not RED; fix only new stubs/tests, never conftest or unrelated files.

- [ ] **Step 4: Implement descriptor/catalog intrinsic validation, closed rejection and build.** Shared private validators match spec §4 safe extraction/precedence, reconstruct common keys and check tuple shapes/caps before nested traversal/hash/equality. Catalog uses private entries/canonical helpers directly, never build; build may construct/revalidate catalog without recursion. Validate full entries before duplicate/conflict pass; canonical bytes follow §5 and output order stays original. Keep selection/require stubs until their behavioral tests. No generic validation/aggregate framework, category duplicate, default seed or extra imports.

- [ ] **Step 5: Run record/build tests to GREEN.** Run fixed-interpreter `-m pytest -q tests/execution/test_strategy_catalog.py`; at this point it contains only Step2 tests. All must pass; capture separate build-tests.stdout.txt/build-tests.stderr.txt/build-tests.exit.txt. Selection/require implementation is still pending and must not be falsely reported complete.

- [ ] **Step 6: Write failing strict-require/scope/trust-limit tests.** Add the following cases to the same test file; use a local typed fixture/helper for descriptor replacement and request arguments, not a second public product API.

| Test name/group | Exact assertions |
|---|---|
| `test_require_preserves_allowed_selected_order_and_empty_subset` | allowed/reverse selected return their own input order, original descriptors; empty selected stays(); exact catalog/key/env returned |
| `test_require_revalidates_entire_catalog_and_digest` | malformed schema/revision/hash/nested altered record→INVALID_INPUT or REVISION_MISMATCH exactly as spec §6; unused malformed descriptor also fails |
| `test_require_rejects_stale_expected_revision` | old expected against new catalog→STRATEGY_CATALOG_REVISION_MISMATCH |
| `test_require_empty_and_subset_precedence` | valid empty catalog→EMPTY; valid nonempty/allowed()→ALLOWLIST_EMPTY; selected outside exact allowed incl same ID/different rev→SELECTION_OUTSIDE_ALLOWLIST before resolution |
| `test_unused_invalid_allowed_rejects_whole_selection` | unknown→UNKNOWN, deprecated/revoked→NOT_APPROVED, domain→INSTRUMENT_MISMATCH, environment→ENVIRONMENT_MISMATCH even when selected excludes offender; no partial result |
| `test_require_same_symbol_product_and_environment_separation` | SPOT-only rejects LINEAR/BTCUSDT and inverse; explicit two-key domain resolves either; DEMO-only rejects LIVE; two-env tuple resolves each |
| `test_require_failure_order_is_deterministic` | all spec §6 stages and per-allowed input order pinned with combined faults; constructor first-environment/later-revoked and reversed-order cases match the per-descriptor spec; existence→status→env→instrument precedence |
| `test_old_catalog_and_old_revision_are_only_static_consistency` | old APPROVED + old matching expected succeeds; new REVOKED/DEPRECATED→NOT_APPROVED; removed ref in still-nonempty catalog→UNKNOWN; old expected+new→REVISION_MISMATCH |
| `test_constructible_metadata_result_has_no_authority` | valid direct catalog construction+matching canonical digest works; selection declares only five fields; no qualified/trusted/admitted/token flags; returned record does not populate registry/worker |
| `test_selection_constructor_intrinsic_subset_and_scope` | allowed nonempty; duplicate references invalid; selected ref absent or same ref/different canonical payload→SELECTION_OUTSIDE_ALLOWLIST; order-only canonical payload difference accepted; all allowed APPROVED/domain/env checks and exact precedence; catalog membership/currentness not proven |
| `test_selection_and_require_tuple_caps` | each allowed/selected tuple accepts100/rejects101 before nested validation; use distinct valid descriptors/references so bounds are exercised without duplicate masking; malformed101st element with unsafe hooks is never traversed |
| `test_forged_nested_fields_reject_before_unsafe_hooks` | construct forged exact-class records without validating constructors; missing attrs and custom field hash/eq/str hooks reject fixed INVALID_INPUT without hook calls, before equality/canonical hashing |
| `test_import_and_calls_are_pure` | fail-fast file/clock/network operation spies and approved import review show import/build/require do not invoke forbidden operations; no provider/SDK/worker/settings/DB import. Avoid a generic self-audit framework |

- [ ] **Step 7: Run newly added selection/require tests and verify behavioral RED.** Same fixed runner and targeted file; import/collection succeeds, required constructor rejections fail and require raises NotImplementedError. Capture require-red stdout/stderr/exit separately and identify actual missing behavior in report. Do not use fixture/import errors as RED.

- [ ] **Step 8: Implement selection intrinsic validation and require.** Use shared private validators and Step4 canonical helpers; selection subset compares validated exact reference plus full canonical descriptor payload, then applies allowed approval/environment/instrument checks. Require revalidates catalog without build, uses exact §6 stage order/codes, resolves all allowed with one lookup after validation, and constructs/revalidates its result. Keep input order, local state and no fallback. No modes selection/runtime/freshness issuer.

- [ ] **Step 9: Freeze sources and run targeted verification once.** Freeze SHA256 of both owned files and candidate spec/plan plus exact source HEAD/tree in source-freeze.sha256.json before verification. Through `C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe`, with five source vars still `off`, run `-m pytest -q tests/execution/test_strategy_catalog.py tests/execution/test_modes.py tests/test_trading_product.py`; then `-m ruff check src/multi_exchange_trader/execution/strategy_catalog.py tests/execution/test_strategy_catalog.py`; then `-m mypy src/multi_exchange_trader/execution/strategy_catalog.py`. Capture each into distinct tests/ruff/mypy.stdout.txt, .stderr.txt and .exit.txt files in assigned evidence directory. Verify frozen source hashes unchanged after runs. No install/sync, full local native/PAPER/Windows suite or external effect. Root later owns full hosted/main checks.

- [ ] **Step 10: Independent review and concrete evidence handoff.** Confirm only two owned files changed, safe validating constructors plus independent public revalidation, no recursive build/constructor path, exact canonical oracle/golden bytes, every spec §8 requirement mapped, old+old limitation explicit. Fresh reviewer checks spec compliance then code quality under root's SDD assignment. Address findings and rerun affected checks, re-freezing changed source. Write verification-report.md with commands/interpreter/source vars by name only/HEAD/tree/pass-fail-incomplete/counts/review findings/remaining gates; write artifact-hashes.sha256.txt over source freeze, logs/exits and report (exclude manifest itself). Root owns authorized Git integration; builder does not auto-commit.

## Completion evidence and remaining gates

Required handoff: root-assigned A04 SDD evidence directory; separate stdout/stderr/exit files for each run; source-freeze.sha256.json, verification-report.md and artifact-hashes.sha256.txt; exact HEAD/tree/owned paths/candidate hashes/commands/pass-fail-incomplete/counts/review findings. Explicitly retain unimplemented issuer/current generation/CAS/worker/artifact/actual strategy approval/DEMO gates. No network, repository change, test run or Git effect was performed by the planning agent.

Permitted claim after implementation/review: **“A04 순수 승인 metadata catalog/strict exact require 원천부품 부분 구현”**. A04 full completion, live strategy approval, current revocation enforcement, worker exact pin and actual DEMO/LIVE execution remain separate and unclaimed.

## Plan self-review (candidate author)

- Coverage: spec §§3–6→Task1 steps2–9; constructor/closed exception/forged-hook/type/order/product/stale limits→steps2/6; behavioral RED→steps3/7; source freeze/separate evidence→steps9/10. Genuine issuer/integration belongs to future gates.
- Type consistency: two public function signatures and eleven/three/five record fields match spec exactly; no invented integration types.
- Right-sizing: one coherent module/test task; preflight, canonicalization and docs/review are folded into its own test cycle.
- Review Focus: all five focus rows map to named Task1 tests.
- Root choices: adopted spec §9 records choices and costs, including extra bounded constructor checks and rejected legitimate outliers. Independent candidate preflight completed; its per-descriptor constructor precedence ambiguity was resolved explicitly. Builder identity/SDD/evidence ownership checks and independent implementation review remain. No user decision is requested.

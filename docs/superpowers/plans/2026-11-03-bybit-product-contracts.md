# Bybit Product Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add pure SPOT/LINEAR identity/scope and safe permission metadata while preserving legacy execution behavior.

**Architecture:** Public stdlib product values, private pure account/product selection values, and non-authoritative permission observations in three new modules. No existing execution caller wiring.

**Tech Stack:** Current locked Python/pytest/mypy/Ruff; SDK1.231.0 unchanged, no dependency/workflow/UI/DB change.

**Spec:** docs/superpowers/specs/2026-11-03-bybit-product-contracts-design.md

## Global Constraints

- Existing worktree F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102, baseline main94779a440d0fdddd804a926b46838c12db2443f4, branchgpt/bybit-product-contracts-20261103. Root owns Git and exact file sets. No another worktree.
- Six new implementation/test files only; root may add factual progress documentation separately. Existing account_binding/parser/linear predicate/native/settings/worker/qualification/ownership behavior is unchanged. No new execution caller, no product in account/environment lock key.
- Pure values/metadata are NOT authenticity, venue support, execution capability or E16 completion. Metadata product grants remain observed even when READ_ONLY/missingWallet/forbidden block. Unknown/malformed data gives safe empty result and fixed codes only.
- Exact immutable runtime types, safe fixed errors, no coercion/aliases/default product/normalization. Symbol LOCAL2..48 uppercaseASCII; revision LOCAL1..96 ASCII; account LOCAL1..256 exactstr/no edgewhitespace/C0/DEL. Profile exact BYBIT_SPOT_LINEAR_NO_FUNDS_V1 via product_scope.BYBIT_PERMISSION_PROFILE_REVISION.
- root trading_product stdlib-only/no execution/account/native/credentials/httpx import. product_scope depends only on root product/pureProtectionScope. product_permissions light TYPE_CHECKING module import, lazy exactidentity/current schema reuse on evaluation; no actual credential/env/file/DB/clock/client/session/nativeSDK/loader/network operation.
- Workers synthetic/offline fixtures only, real/default stores/key/UID/env/note/trialDB/provider/actualAPI/browser/WSL/native/order/paidAI/server0. LIVE/newserver forbidden. No subagents or Git mutations by workers. Existing source-file options/defaultstores off during tests.
- Behavioral RED then focused GREEN and covering legacy unit checks, separate Ruff/mypy receipts, source freeze/hash report. No collection error as RED. No redundant full Windows/native/PAPER suite. Exact full hosted/main gates own whole qualification, preserving native36/6/12 unskipped and known general/viewport skips separately.

## Review Focus

1. Same symbol/product/environment confusion: Task1 pins distinct IDs, scope product consistency and explicit DEMO/LIVE values without authority.
2. Frozen/subclass/forged nested values and secret-like invalid inputs: Task1/2 reject or safely classify before hooks/formatting; no implicit conversion.
3. Duplicate Wallet/group/grant erased by dict conversion: Task2 validates original immutable tuple first, known schema bounds/case and duplicates, preserves input.
4. Readonly/missingWallet/knownforbidden versus observed trade grants: Task2 preserves valid observation while complete/blockers remain accurate; unknown/malformed is empty and secret-safe.
5. Import/evaluation/caller boundary: Task1 fresh-process no heavy imports; Task2 no credential/network/native effects and copied profiles cannot issue binding/submit; exact diff adds no consumer.

### Task 1: Pure product identity and explicit product selection

**Files:** Create src/multi_exchange_trader/trading_product.py, src/multi_exchange_trader/execution/product_scope.py, tests/test_trading_product.py, tests/execution/test_product_scope.py. No existing file modification.

**Interfaces:** TradingProduct Literal SPOT/LINEAR, frozen InstrumentKey(venue,product,symbol), bybit_category(product), bybit_native_instrument_id(key); product_scope constant BYBIT_PERMISSION_PROFILE_REVISION, frozen ProductScope(account:ProtectionScope,product), frozen ProductExecutionPolicy(scope,revision,allowed_products:frozenset,selected_product,permission_profile_revision). Exact bounds/mapping in spec.

- [ ] Write tests and minimal importable stubs; obtain assertion/no-raise behavioral RED, not missing-module/collection errors. Cases sameBTCUSDT SPOT/LINEAR exactIDs/category, mandatory fields/no default/mixed/None/bool/subclass/Enum/Unicode/suffix/whitespace/coercion; symbol2/48 pass1/49 reject, venue/product exactcase.
- [ ] Pin helper exact key/revalidation after forged mutation, immutable/hashable values, privateScopeexactnestedProtectionScope revalidation(accountbounds/controls/envtype), explicit DEMO/LIVE distinct scope, exactfrozenset nonempty and exactproducts, selected inallowed AND scope.product==selected, revision1/96 pass0/97/invalidASCII reject, fixedprofileversion only.
- [ ] Implement narrow validation/mapping only. No venue fetch/listing/quote/market-type inference or new methods that execute/lock/configure. IDs don't validate USDT perpetual support; later catalog owns it.
- [ ] Fresh isolated interpreter import tests prove root product loads no execution/account/credentials/httpx/native and root product+scope loads no credentials/native/network. Use explicitly isolated fixture environment and guarded imports/effects; don't rely on already-loaded sys.modules.
- [ ] Run new tests plus tests/execution/test_ownership.py. Ruff four new files and strict mypy two new sources, separate stdout/stderr receipts with exactcommands/exits. No old fullsuite/native/PAPER rerun. Freeze and write task-1-report.md with hashes/RED/GREEN/static/selfreview/limits; root commits then fresh taskreview before Task2.

### Task 2: Safe local permission metadata profile

**Files:** Create src/multi_exchange_trader/execution/product_permissions.py and tests/execution/test_product_permissions.py only. Do not change Task1 files or existing source/tests.

**Interfaces:** frozen CredentialPermissionProfile(revision,granted_products,forbidden_grants,metadata_complete,blockers); evaluate_bybit_permission_profile(identity:BybitAccountIdentity,*,profile_revision:str). Reuse Task1 constant and TradingProduct. Existing `_PERMISSIONS` known response schema reused lazily, never `_LINEAR_GRANTS` as new allowlist. Complete semantics/blocker names/invariants in spec.

- [ ] Write tests/minimal importable DTO/function stubs and obtain behavioral RED. Dual/spot-only/linear-only/Order-only/Position-only/Derivatives-only/emptyknown groups, readOnly true withtrade grants, missingWallet versusempty, each known forbidden Wallet/Options/othergrant, unsortedvalidtuple deterministicoutput/inputunchanged.
- [ ] Cover exacttypes/subclasses/hostileobjects before hash/repr/format, duplicate groups/grants, unknownemptygroup, unknownsecretlikegroup/grant, malformednestedtype/oversize/nonbool normalizedread_only. Unknown/malformed returns fixedsafeempty result; incorrectidentity/unsupportedrevision fixedValueError, no input values. Pin all resultconstructor types/knownpairs/blockercanonicalorder and cross-invariants including observed grants preserved for structurallyvalidblocked data.
- [ ] Implement localprofile fourallowedpairs and per-product requirements as spec, no UID/time/transport auth/fresh inference. Original identity/permissiontuple/legacylinear predicate unchanged; dualSpot metadata should not make legacylinear property true. Validate entiretuple before dict/set conversion or publishing partialknown values.
- [ ] Module import stays light via TYPE_CHECKING+postponed annotations; exactidentity/schema local import on evaluation is allowed. Test zero credential reads, HTTP clients/sessions/nativeSDK/loader/network/env/DB effects from evaluator, no rawunknown text in DTO/repr/errors. Synthetic identity/profile may be copied/constructed but existing binding constructor/session reject it, and no config/submit/qualification caller is added.
- [ ] Run new test plus Task1tests and existing tests/execution/test_account_binding.py/tests/execution/test_ownership.py. Ruff two newfiles, strict mypy three newsource modules, separate receipts. No redundant fullnative/PAPER/local fullproject suite. Freeze, report task-2-report.md exactcommands/exits/hashes/selfreview; root commits/fresh dual taskreview.

## Final gates

Root factual progress doc may note prior actualHaiku acceptance and current purecontracts only. Fresh whole-branch package from main94779, independent review, exact full hosted CI (Python/static/frontend/ops/browser/secret/dependency plus exactnative36/6/12), expected-head main merge/tree/src readback and main CI. Local/synthetic/code-regression versus actual venue/server/product qualification remain separate. No actual provider/key/store/order/server test in this slice. Preserve evidence/originals/worktree.

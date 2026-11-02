# Managed Program Credentials Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. Root serializes Git mutations.

**Goal:** Register secrets once and reuse them automatically in the program across restarts without recurring .env discovery.
**Architecture:** Private platform store plus one startup resolver, connected to existing client/config/CLI boundaries. Keep public DTOs and all execution gates unchanged.
**Tech Stack:** Python3.12, stdlib WindowsDPAPI/POSIX file protection, existing FastAPI/httpx/Nautilus1.231.0.
**Spec:** docs/superpowers/specs/2026-11-03-managed-credentials-design.md

## Global Constraints
- Keep Python3.12, NautilusTrader1.231.0, existing HTTP clients, OpenRouterGenerationClient/quote/AIDispatcher and API DTO shapes.
- Registration, client construction and status reads never authenticate, generate AI, place orders or enable modes.
- Windows stores ciphertext protected by current-user DPAPI with UI forbidden, never machine scope or plaintext fallback.
- Linux uses current service-user-owned directory0700/file0600 and is explicitly access-controlled plaintext, not encryption.
- Stored keys do not imply BYBIT_DEMO=true.
- Source .env is preserved. No keys in code, logs, chat, browser, business SQLite or backup inventory.
- Root alone handles operator credentials, metadata and actual authorized DEMO/paid AI tests. Implementers/reviewers use synthetic data only.
- LIVE orders and new server spending remain prohibited. No dependency upgrade/new remote service.
- load_ai_provider_config(environ=explicit_mapping) and load_anthropic_config(environ=explicit_mapping) never discover the user's default vault.
- Root serializes commits/push/merge. No subagents inside workers. Preserve existing ignored workspaces/evidence.

## Review Focus
1. A corrupt existing store must not silently choose a valid old .env identity (Task1/2).
2. A partial credential pair or LIVE/DEMO collision across sources must fail before client construction (Task1/2).
3. A saved credential must never activate practice orders/AI or give a default dry CLI access to it (Task2).
4. A simultaneous registration or file replacement must not lose an acknowledged revision or leak a plaintext temp (Task1).
5. Existing tests and pure config Mapping calls must not read a real operator store (Task1/2).

### Task 1: Private store and explicit one-time registration
**Files:** Create src/multi_exchange_trader/credentials.py, credentials_cli.py; focused helper module permitted for OS/file boundaries. Create tests/credentials/test_store.py and test_import.py. Modify tests/conftest.py for TRADER_CREDENTIAL_FILE=off only. Add docs/operations/CREDENTIALS_KO.md for the delivered CLI/storage guarantees.
**Interfaces:** CredentialStoreError; CredentialSnapshot; RegistrationResult; CredentialStore(path, optional test protector), load()->CredentialSnapshot|None, register(values:Mapping[str,str],*,expected_revision:int|None)->RegistrationResult; default_credential_store(environ:Mapping[str,str])->CredentialStore|None; import_env(path:Path,store:CredentialStore,*,expected_revision:int|None)->RegistrationResult. Resolver may be defined here for Task2 with bybit_pair(environment), ai_values().
- [ ] Write failing tests test_import_survives_unavailable_source_and_fresh_load, test_corrupt_store_never_returns_secret, test_expected_revision_prevents_lost_update, test_same_demo_live_pair_rejected, test_namespace_half_pair_rejected, test_import_does_not_import_activation_or_identity_claims, test_read_does_not_create_store, test_redaction_in_repr_cli_and_failure, test_real_windows_dpapi_roundtrip. Use the exact limits/modes in Spec.
- [ ] Run new focused tests; preserve RED evidence showing the missing feature, not a syntax/tool error.
- [ ] Implement strict platform persistence, locking/revision, one-time bounded parser/CLI and fixed error semantics from Spec. Keep OS helpers focused. Test dependency injection cannot make production silently choose insecure protection.
- [ ] Exercise malformed/duplicate/type/control/size/version, parent link/hardlink/reparse, POSIX owner/mode, failed replace/fsync, subprocess/concurrent revision, Windows actual DPAPI synthetic roundtrip and immutable safe metadata.
- [ ] Run focused tests, Ruff and mypy on touched source/tests. Record commands/results, inspect own diff and ask root to commit exact owned files. No Git mutations or operator access by implementer.
- [ ] Root creates task review package from recorded BASE to committed HEAD and dispatches independent reviewer; resolve findings before Task2.

### Task 2: Connect saved credentials to existing startup/clients without effects
**Files:** Modify account/config.py, account/bybit_readonly.py, account/bybit_demo_order.py only credential construction, api.py, controls.py, ai/provider_config.py/config.py as needed, execution/worker_cli.py. Create ai/private_factory.py or focused equivalent. Create tests/credentials/test_integration.py; update narrow existing tests only when signature-compatible setup requires it. Update docs/operations/CREDENTIALS_KO.md, LINUX_DEPLOYMENT_KO.md and docs/implementation/FINAL_PRODUCT_PROGRESS_KO.md.
**Interfaces consumed:** Task1 store/snapshot/default selector. Expose CredentialResolver(*,store:CredentialStore|None,environ:Mapping[str,str]), bybit_pair(environment:Literal['mainnet','demo'])->tuple[str,str], ai_values()->Mapping[str,str]. Preserve existing public signatures with keyword-only optional resolver/context injection; private factory constructs existing metadata/generation/dispatcher only.
- [ ] Write failing tests test_saved_pairs_construct_clients_without_env_file, test_fresh_startup_context_reuses_store_without_environment_mutation, test_explicit_mapping_loader_does_not_discover_store, test_corrupt_store_cannot_fallback_to_env, test_store_namespace_absence_preserves_legacy_setup, test_selected_pair_collision_across_sources, test_store_does_not_enable_demo_or_ai, test_default_worker_cli_does_not_open_vault, test_private_factory_has_no_provider_effects, test_connections_dto_contains_no_secret_or_path.
- [ ] Run focused new tests and preserve meaningful RED evidence.
- [ ] Implement one startup snapshot and resolver integration. Keep stored identity stable throughout a session and require restart for rotation; retain flags/expected-identity/permission/qualification validation. Avoid public raw secrets in app.state responses or model/error fields. Reuse existing provider validators and AIDispatcher.
- [ ] Run focused store/integration + account/ai/config/worker CLI/API contract/operations backup tests. Read failures before changing code; no broad test disable, fake financial qualification or actual credentials.
- [ ] Update progress: A13/P06/U05 partial managed-registration delivery only. E08/E09/P01 remain queued/in-progress as appropriate, PR20~23 preserved. Latest authority permits root actual DEMO/min-cost paid AI tests; LIVE/new server spending still forbidden. External unresolved facts remain distinct.
- [ ] Run Ruff/mypy and report self-review/test evidence; root commits exact files, packages independent task review and resolves findings.

## Final integration and finish
- [ ] Run whole-branch independent review from f629 baseline including docs/store/clients/tests; one consolidated fix wave if required, then scoped re-review.
- [ ] Run one full exact-head CI, preserving native36/restart6/restored12 and current browser/operations gates; inspect all jobs/real failures and receipt counts.
- [ ] Merge only reviewed exact successful head; verify remote main parents/tree and main CI. Root may then use the reviewed CLI to import the known local Bybit file once, privately, preserving source and recording no key values.
- [ ] Actual DEMO identity/permissions/UI and AI key/model/price/low-cost test remain external gates. Report results honestly and continue the next already-authorized 63-register phase; do not call managed credentials overall product completion.


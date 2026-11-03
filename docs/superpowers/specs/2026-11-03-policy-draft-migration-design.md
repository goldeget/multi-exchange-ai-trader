# Explicit policy draft migration — adopted Task1

ADOPTED for same-book V1-to-V2 STOPPED drafts only. Actual artifacts/publisher/candle source/plan issuer and non-null RiskV2 are subsequent tasks. This slice grants no execution authority.

Baseline main61ff7270100b0ad8fda42bb86e68b2f5bf8ed42b passed exact CI125. Origin https://github.com/goldeget/multi-exchange-ai-trader.git; branch gpt/bybit-policy-drafts-20261103; R=F:/자동매매프로그램/worktrees/policy-drafts-20261103. Root owns Git/docs/ledger; original worktrees/private state remain preserved. No applicable AGENTS found in workspace ancestry/repository search.

Root read the complete external A04_CURRENT_STRATEGY_POLICY_PIN_PREPARATION_20261103.md (218lines,SHA f854c3f4c2d1b1afe0fa52f5c51acefc6ea07832bafb32e2cb2c0547616175be), A04_STRATEGY_POLICY_FINAL_PREFLIGHT_20261103.md (181lines,d6db6be540f77d469828fe5a33f86e0d54c84d8bf918954c1984ca9e53075bd8) and actual V1 book code. Narrow Task1 requirements below bind; other proposed tasks are not silently adopted.

## Exact data contract

Use SAME RuntimePolicyBook SQLite path/connection owner. Preserve V1 KEYS/validate_draft/Decimal lexical values and original record reading. No sidecar/new DB/writer, money auto-mapping, default capital/product/universe/strategy/TTL or approved seed.

Schema2 policy exact keys: legacy_v1,scope,strategy,risk_v2. legacy_v1 retains ALL existing KEYS with current string/null/list/count/choice validation and representation. New three sections are exact None in this slice. Missing/unknown/partial/object/list/bool replacements fail. expected_strategy_generation is exact None. No unknown-section pass-through.

Read response keeps schema_version,revision,policy,missing_fields,desired_state,live_order_enabled,worker_running,blockers. STOPPED/False/False persist. V2 missing_fields exactly sorted ['risk_v2','scope','strategy']; blockers include POLICY_V2_ADMISSION_REQUIRED and existing NATIVE_RUNTIME_NOT_QUALIFIED/WORKER_NOT_CONNECTED. V1 missing_fields retains old meaning with fixed migration-required blocker.

Revision exact nonnegative int<=2**53-1, rejecting bool/float. Increment overflow fails BEFORE mutation. Unsupported pre-existing revision stays intact, never rounded/reset. Internal errors and HTTP reasons must be finite/safe, without raw input/path/parser details.

Truly empty schema0 DB initializes schema2 revision0 with legacy_v1=dict.fromkeys(KEYS), new sections None. Existing schema1 stays1 until explicit migration; constructor/read/GET never auto-migrate it. Validate version/layout/singleton/STOPPED; contradictory storage fails rather than repair. Fresh V2 must not invent a historical V1 archive. Migrated retained original/archive agree.

## Atomic migration and older writers

migrate_v1(expected_revision:int)->dict[str,Any]: BEGIN IMMEDIATE; require actual schema1/layout/revision/STOPPED; SELECT original payload TEXT/revision directly; validate separately; preserve original runtime_policy row VERBATIM; archive same TEXT/revision/STOPPED in immutable runtime_policy_v1_archive; create runtime_policy_v2 singleton revision=old+1 with same decoded lexical legacy values+three nulls; install fences; set user_version2; commit all together. Avoid executescript/autocommit splitting DDL. Every stage failure/CAS conflict rolls back everything.

Original/archive singleton INSERT/REPLACE/UPDATE/DELETE fenced with BEFORE INSERT/UPDATE/DELETE triggers. Do not rely on recursive_triggers-sensitive REPLACE deletion. Actual old constructor checks user_version0|1 and rejects2. An already-created old object reconnects and must fail at retained V1 UPDATE. Test both race orderings, repeated migration conflict, no second increment, original raw whitespace/key order/Decimal strings/null/lists/mode/environment/revision.

save_v2(revision:object,draft:object,*,expected_strategy_generation:object)->dict[str,Any]: same-owner BEGIN IMMEDIATE, verify schema2/layout and BOTH persisted current full policy and submitted full policy using this writer's CLOSED null-only codec; then revision CAS/UPDATE/commit. A later writer may store non-null pins/Risk. This older writer MUST reject before UPDATE even with a fresh matching revision. Submitted full keys+CAS alone do not prevent erasure. V1 save cannot downgrade V2. V2 risk_policy raises POLICY_V2_ADMISSION_REQUIRED before legacy key lookup/fallback.

Use real frozen original class fixture, not an imitation with V2 guards: preserve BASE runtime_policy.py bytes under tests/execution/fixtures/runtime_policy_v1.py with source hash, loaded in a test-only proper package namespace. No production dynamic import/eval. Temporary fixture DBs only. Preserve Task1 writer reference/fixture for later future-writer regression.

## HTTP

GET /api/v1/automatic/settings reads1|2. POST /api/v1/automatic/settings/migrate exact {schema_version:1,revision}. PUT exact {schema_version:2,revision,policy,expected_strategy_generation}. Old {revision,policy} or schema1 PUT returns409 even before migration. Preserve Host/Origin403,content-type415,streamed64KiB413,duplicate nested JSON rejection for BOTH writes. Safe409 automatic_settings_conflict_or_invalid,503 automatic_storage_unavailable. No overlimit partial parse/mutation. Uncertain result resolves by GET, never automatic retry with a newly fetched revision.

## UI

Keep collapsed default, labels/layout where unchanged, Decimal lexical inputs, delayed-read ordering, edits/conflict/uncertain-save/unmount-abort behavior. Add exact schema1|2 parsers and explicit migration action, with no hidden auto-migration/start/defaults. V2 shows legacy values and plain Korean missing trading-target/strategy/risk sections, not SQL/schema/class jargon. Legacy edits may use complete V2 envelope with three nulls.

Migration acts on STORED V1 revision, not unsaved edits. Disable while dirty, preserve edits, offer explicit reload/discard; late reads never overwrite edits. Success consumes returned schema2/revision;409 preserves edits; timeout requires GET/reload before another write. No approval/start endpoint. Update named browser fixture. Its attachments may record legitimate states; golden screenshot tolerances/update policy remain unchanged.

## Scope and verification

Production: execution/runtime_policy.py, execution/settings_api.py, apps/web/src/app/live/AutomaticSettings.tsx; contracts.ts only if actual shared type use requires it. Tests: tests/execution/test_runtime_policy.py,test_settings_api.py,new tests/execution/fixtures/runtime_policy_v1.py,AutomaticSettings.test.tsx,apps/web/tests/ui-browser/automatic-settings.spec.ts. No worker/native/risk/journal/account/key/lock changes. Root owns adopted docs/ledger/Git.

Meaningful RED, smallest implementation, temporary DB stage-failure/CAS/real-old-writer/rawTEXT/Decimal/future-stored-pins tests; safe API body/Host/Origin/uncertainty and zero start/native/reservation effects. Focused Python2files, component tests/typecheck/touched ESLint and named mock browser flow; separate commands/stdout/stderr/exits/freeze, fresh independent task/whole review and exact hosted/main gates. Fixed Python C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe; PYTHONPATH=thisR/src; five source env variables literaloff. No broad native/PAPER repeats.

No agent private/default store/env/key/vault/note/actual DB/provider/venue/nativeSDK/WSL/order/paidAI/server effects. Public LOCKED npm setup is permitted only in this isolated worktree if needed: scripts disabled, own cache, explicit public registry, empty user/global configs and sanitized OS-only child environment. No original checkout dependency modification, package/lock version change or global install. Local mock/headless tests allowed, no visible window or authenticated Bybit browser connection.

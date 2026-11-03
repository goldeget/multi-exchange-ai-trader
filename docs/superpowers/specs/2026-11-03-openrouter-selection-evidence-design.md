# OpenRouter selection contract and rejection evidence

## Evidence and purpose

Accepted baseline main aab53709abf52b90887c7c973a5fc078a0d5e621 (PR26, whole review clean, CI117/main118 passed). One root-only actual anthropic/claude-haiku-5.5 request returned HTTP200 and exact cost USD0.0000846. The generation envelope was accepted; selection validation raised SelectionRejected, which was collapsed to INVALID_SELECTION. Cost settled, no outstanding reserve/uncertainty, fresh-process readback matched and duplicate activation was blocked. The raw selection and specific code are unavailable. This change must NEVER claim to identify or repair the historical cause.

Independent offline investigations established two source gaps: the prompt omits the actual per-request output bounds, and dispatch discards the validator's fixed code. Correct synthetic SELECT/ABSTAIN pass. Long explanation, extra keys, wrong bindings/candidates, invalid JSON and expired evidence all cause different rejections. No model, token or lifetime change is justified by the current evidence.

## Scope

1. Generation prompt states the validated request's max_reason_chars in Unicode characters and max_response_bytes in UTF-8 bytes, with no control characters in reason. Preserve the exact seven-field response schema, SELECT/ABSTAIN semantics and public request JSON. Derive bounds from the same request used by validation; never invent or loosen them. No private fields or candidate text interpolation in system instructions.
2. Append nullable selection_rejection_code after existing DispatchResult fields, keeping every old positional meaning/default and failure=INVALID_SELECTION unchanged. Persist only an exact fixed code, never exception detail, raw code outside the allowlist, output, reason, key, account identifier or provider prose. It is diagnostic evidence, never selection or execution authority.

The fixed allowed codes are INVALID_INPUT, FUTURE_EVIDENCE, STALE_EVIDENCE, EXPIRED, INVALID_TTL, SCOPE_MISMATCH, RESOURCE_LIMIT, DUPLICATE_ID, REVISION_MISMATCH, DISABLED_CANDIDATE, NO_CANDIDATE, INVALID_JSON, INVALID_PRIORITY, AMBIGUOUS, INVALID_SCHEMA, BINDING_MISMATCH, UNKNOWN_CANDIDATE, UNCLASSIFIED. Exception capture maps an unknown/non-exact-str code to UNCLASSIFIED without formatting it. Explicit DTO/write/storage inputs accept only None or an exact str in this set, otherwise a fixed safe ValueError. Known cost/accounting must not be lost because the exception code was untrusted.

## Durable storage

Schema3 is the existing exact nine-column schema2 plus nullable TEXT selection_rejection_code at ordinal9 with no default/PK/hidden flag. Preserve the existing eight columns and http_status exactly. Under one BEGIN IMMEDIATE acquired BEFORE any version/layout decision: empty v0 creates schema3; exact legacy eight-column v0/v1 migrates to3; exact nine-column v2 migrates to3; exact ten-column v3 is idempotent. v0 permits only the same empty/exact-legacy object sets as before; v0 nine/ten-column tables are not newly repaired. Unknown versions, table/view/column/type/null/default/PK/hidden mismatches and corrupt diagnostic values fail closed and roll back completely. Validate layout, diagnostic SQLite typeof/value and quick_check before user_version3/commit. No executescript or out-of-lock migration decision. Concurrent openers serialize safely.

Historical rows receive NULL, including the actual failed Haiku and old Qwen rows if explicitly migrated in future. NEVER infer or backfill their rejection codes. This implementation/test task never opens those actual stores. Old binaries refusing schema3 is expected; an operator must take a quiescent paired budget/dispatch backup before actual migration.

Capture code when select_all_in_one raises SelectionRejected and atomically persist it with final failure, returning it in the once-only result. Later records preserve it with COALESCE. status and reconcile_charge return it with no selection reconstruction; validate persisted type/value before any accounting repair. Preserve HTTP status, exact cost, unknown liabilities, no retry and budget-overrun behavior. A code may remain as underlying evidence when an overrun overrides the high-level failure.

## Exclusions and gates

No actual provider call, operator/default store, key/note/.env/trial DB, orders, browser, native activation, server spending or model fallback in this feature. No dependency/workflow/UI/worker/risk/budget-policy/parser/TTL/max-output/model change. Existing failing output stays rejected. No paid retry of the consumed trial, including from restart or code migration. Root alone may later handle separately authorized actual acceptance.

User delegated implementation, independent review and passing-main merge. Existing linked worktree only, root owns all Git mutations; workers use synthetic fixtures/fake transports/private defaults off and no subagents. Each task has behavioral RED/GREEN, separate static receipts and fresh dual review. Whole review and exact hosted CI with native36/restart6/restored12 unskipped, main/tree readback and main CI remain required. Preserve original and ignored evidence.

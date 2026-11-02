# Explicit OpenRouter connection implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for independent implementation and reviews.

**Goal:** Connect the user's explicitly confirmed OpenRouter credential to correct configuration/read-only verification and truthful connection display, with paid generation still disabled.

**Architecture:** Add a key-free provider configuration wrapper alongside the existing Anthropic loader. An independent explicit OpenRouter GET-only client verifies current-key metadata and a model slug. Startup/status reads never contact a provider. The existing offline budget and execution modules remain independent.

**Tech Stack:** Existing Python3.12/httpx/FastAPI/pytest and React/TypeScript/Vitest/Playwright; no new dependency.

**Spec:** User confirmed on2026-11-02: OpenRouter is the funded service and should be used to access Claude. Existing docs/REFERENCE_ARCHITECTURE.md, docs/ROADMAP_FINAL_PRODUCT.md4.2 and current controls/budget safety boundaries apply. This plan includes the short design: explicit provider selection, no automatic fallback, GET-only verification, truthful registration display, generation off. User's prior autonomous implementation/review/main-merge direction and opt-out of repeated design questions persist.

## Global constraints

- Reuse clean linked native-provider-integration worktree, branch gpt/openrouter-connection-20261102, base4b68598296e76792605c49b5e0610cca5a6bd013. Workers edit disjoint assigned files only, never stage/commit. Root integrates/publishes after review.
- Actual external credential file and inherited secret values are ROOT ONLY. Workers/reviewers must not read them. Only fake keys/temp settings in tests. No key/label/account IDs/provider bodies in public logs/docs/commits.
- Root may do bounded authenticated GETs to OpenRouter after explicit confirmation. No paid generation, Messages/chat POST, real orders, account changes, new servers or budget increase. Existing OFFLINE_TEST/llm_enabled=false/external_calls=false remain.
- No new provider SDK or URL overrides; transport fixed https://openrouter.ai. No redirect/retry/proxy fallback. No network at module import, app creation, startup, status reads or rendering.
- Current official docs: https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key ; models/get-a-model-by-its-slug ; credits/get-remaining-credits . GET /api/v1/key proves key auth; key limit_remaining is NOT account balance. Credits now requires management key; do not demand broader access or infer10USD balance.
- Direct Anthropic model ID claude-haiku-5-5 differs from OpenRouter anthropic/claude-haiku-5.5. Model metadata existence is not account model access, actual generation, billable price authorization or spending permission.
- Preserve D4/D5/D6 and current offline execution/core activation boundaries. Do not wire these clients into trading or generation.

## Review focus

1. Explicit OPENROUTER selection with missing/wrong key must not fall back to Anthropic or guessed environment values.
2. Malformed model/unknown selector, duplicate file settings and secret echoed as model/header/error must remain safe and key-free.
3. Key remaining-limit/null must not become provider balance or permission to call; metadata200 must not enable generation.
4. Invalid/oversized/deep/duplicate JSON and errors must be bounded and never copied to output.
5. Registered OpenRouter display must distinguish registration from verification while default demo screenshots remain stable.

### Task1: Provider configuration and controls registration

Own new ai/provider_config.py, tests/ai/test_provider_config.py, new tests/test_provider_connections.py, controls.py, docs/implementation/AI_PROVIDER_CONFIGURATION_KO.md. Do not change existing ai/config.py or budget.

Interfaces: frozen key-free AIProviderConfig(provider: Literal['ANTHROPIC','OPENROUTER'], registered:bool, status: registration status, model_id:str|None); load_ai_provider_config(environ:Mapping[str,str]|None=None)->AIProviderConfig. Invalid selector fails closed with a safe default provider label and INVALID_CONFIGURATION, never auto fallback.

- [ ] RED tests for explicit AI_PROVIDER=OPENROUTER and OPENROUTER_API_KEY/MODEL; explicit AI_ENV_FILE shared file can also contain AI_PROVIDER. Nonblank explicit environ overrides file; disabled file means no implicit search. Default absent selector/file preserves legacy load_anthropic_config behavior including ANTHROPIC_ENV_FILE. An explicitly selected OPENROUTER never reads ANTHROPIC key as its credential.
- [ ] Safe bounded file parsing like existing loader (16KiB/256lines/regular/nonlinkcomponents, duplicate recognized keys rejected). Recognize AI_PROVIDER, both provider key/model namespaces only. No env export or key retention/repr/path/error echo. Scan all candidate credentials against selected public model. OpenRouter key has OpenRouter prefix; model has bounded author/slug syntax, rejects URL/query/control/path escape.
- [ ] controls startup captures safe AIProviderConfig only; retain legacy Anthropic state compatibility for tests/callers as needed. /connections/status ai.provider/model_id describe selected provider; registered remains REGISTERED_UNVERIFIED, verified_at null, external_calls false, mode OFFLINE_TEST. No auth at startup/status.
- [ ] Focused compatibility tests, Ruff/scoped mypy, docs of explicit operator fields and restart requirement. Freeze/report outside repo, no stage/commit.

### Task2: OpenRouter read-only metadata client

Own new ai/openrouter_metadata.py, tests/ai/test_openrouter_metadata.py, docs/implementation/OPENROUTER_METADATA_KO.md. No task1/3 edits.

Interface: OpenRouterMetadataClient(api_key:str, model_id:str, *, transport:httpx.BaseTransport|None=None, timeout_seconds:float=5.0, max_body_bytes:int=65536); verify_key() and retrieve_model() return distinct frozen safe result types. Expose exact typed result API early. Explicit credential/model; no file/env loader.

- [ ] Read current official single-model slug and current-key docs to pin exact URL/schema. Each method at most one GET; retrieve_model may be unauthenticated if public documented endpoint, but must not imply key access from public response. No broad credits endpoint, paid dispatch or node startup.
- [ ] Validate inputs, fixed origin/path and exact requested slug/model identity. Bound body/time/depth/count/text; reject duplicate/nonfinite JSON. Copy only required relevant typed fields. Unknown future fields bounded/discarded; provider label, raw key, workspace/account IDs not returned. Secret reflection anywhere fails safe.
- [ ] Key result: verified authentication timestamp, finite nonnegative numeric usage/limit/limit_remaining with null preserved where documented, never float-round down. provider_balance null and generation_enabled false. Treat management key/restricted/expired details truthfully if schema provides; no new management key creation or account changes. Key-limit evidence != spend authorization.
- [ ] Model result: exact safe model ID/name/context/pricing strings supported by current docs; no inference model access or affordability from catalogue metadata. Return safe fixed HTTP/TLS/timeout/body/schema errors, no exceptions/headers/bodies/key in repr/results.
- [ ] Meaningful RED/GREEN MockTransport tests: GET-only, auth, no retries/redirects, known errors, malformed fields, money/null, model mismatch, duplicate/oversize/deep/secret-echo. Ruff/strict mypy. Freeze/report outside repo, no real calls/stage/commit.

### Task3: Truthful provider display

Own apps/web/src/app/live/ConnectionsPage.tsx, contracts.ts, new ConnectionsPage.provider.test.tsx and optional existing focused tests only if needed. Root owns integration docs/plan. No backend edits or baseline screenshots.

Backend contract ai remains registered/status/mode/external_calls plus optional provider:'ANTHROPIC'|'OPENROUTER', model_id:string|null and verified_at:null. Existing demo fixture lacking optional fields must keep old default appearance.

- [ ] RED UI tests: explicit registered OPENROUTER shows registered/unverified/model and calls off; missing/invalid provider never shows success; Anthropic selection not falsely labels OpenRouter registered. Unknown malformed optional fields rejected by validConnection. No key field/body in browser state or UI.
- [ ] Extend typed connection contract and validation safely; OpenRouter card reflects supplied selected provider registration and errors, retain simple user language and false external calls. Existing generic unconfigured rows/default fixture remain stable. Selected Anthropic registered may get appropriate extra provider row without relabeling OpenRouter. Do not invent verified timestamp or balance.
- [ ] Run targeted UI tests, lint/typecheck/build as appropriate; no npm install/new dependencies/baseline changes. Freeze/report outside repo.

### Task4: Root integration and real verification

- [ ] Root alone checks copied key against GET/key and explicit model metadata, no paid calls. Migrate private file from mistaken Anthropic namespace to AI_PROVIDER=OPENROUTER plus OPENROUTER_API_KEY and OPENROUTER_MODEL=anthropic/claude-haiku-5.5, preserving Bybit bytes/private ACL and not overwriting unexpected existing provider values.
- [ ] Independent task reviews from immutable scoped diff packages, whole-branch review, targeted combined tests and exact final-head CI before approved main merge. Root owns snapshot export/publication; no actual credentials/private connection evidence in repo.
- [ ] Verify UI using browser/CI with safe fake registered configuration; original local5173 fixture server preserved. Do not start actual backend with copied Bybit DEMO key just to display AI registration (legacy demo order-gate startup exists).
- [ ] Report authentication separately from account balance/key limit, model catalogue, internal budget, paid generation and actual automatic trading readiness. Preserve ignored scratch/no destructive cleanup retry after earlier policy rejection.

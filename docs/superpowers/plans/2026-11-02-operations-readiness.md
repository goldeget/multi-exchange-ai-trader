# Operations readiness Implementation Plan

> For agentic workers: use parallel implementers for the independent tasks below, then independent review and full CI before integration.

**Goal:** Prepare a repeatable loopback Linux deployment and truthful runtime/connection status without creating paid resources or making external account/model calls.

**Architecture:** Keep FastAPI/React/Nautilus/SQLite and current localhost security. Add deployment assets and snapshot tooling; fix scheduler status presentation; add server-only Anthropic credential readiness with external calls disabled. The user's .env is on another PC, so actual credentials and verified provider access remain external prerequisites.

**Tech Stack:** Existing pinned Python3.12, uv, React/TypeScript/Vite, SQLite, systemd/nginx. No new runtime dependency or host installation.

**Spec:** docs/UI_ARCHITECTURE.md sections10–12 and Review Focus3, plus the user's request for no-cost deployment preparation and explicit Anthropic provider preference. Earlier instructions to finish/merge remain authorized; no paid server or model call is authorized by this plan.

## Global Constraints
- Preserve all original workspaces, ledgers and unresolved commands; operate on gpt/operations-ready-20261102 in this isolated checkout.
- No actual key values in tests/logs/responses/Git, no .env copied from another PC by the agent, no account queries/orders/model calls.
- Keep LIVE off, OFFLINE_TEST and llm_enabled=false; no misleading connected/verified or automation recovered claims.
- Do not rewrite proxy Host/Origin, bind publicly, touch provider resources, or weaken existing access boundaries.
- Task owners edit only their named paths; root owns documentation integration, CI, commits and merge.

## Review Focus
- RUNNING persisted state with scheduler STOPPED/FAIL_CLOSED, stale responses or read errors must not show a healthy running badge.
- SQLite/WAL and dataset copies from mixed moments are not a valid multi-ledger snapshot; backup requires confirmed stopped writers, integrity/hash checks and never restores over the original.
- Path traversal, symlinks, incomplete manifests and state inside source must fail closed before install/restore touches anything.
- Presence of an Anthropic key/model must not imply valid credentials, provider balance, paid authorization or actual LLM analysis.
- Real Bybit DEMO approval can create orders; documentation and default deployment must not activate it as a health check.

## Task1 — Runtime status UI regression
Owner paths: apps/web/src/app/live/OverviewPage.tsx and dedicated corresponding tests only.
- [ ] Reproduce RUNNING + unavailable scheduler and stale/error states with failing user-visible assertions.
- [ ] Show healthy running only with current successful read, scheduler READY/task_alive=true and both block/mismatch flags exactly false; preserve existing paused/finished semantics and demo visual baseline. Refresh every5s after completion with15s request deadline and30s age expiry; other hook consumers retain one-shot default. Stabilize preexisting dated recommendation test fixtures with an explicit test clock and preserve expiry-boundary coverage.
- [ ] Run focused tests, frontend full tests/typecheck/lint/build and report precise evidence.

## Task2 — Linux deployment and consistent recovery artifacts
Owner paths: deploy/linux/**, scripts/operations/**, tests/operations/**, docs/operations/LINUX_DEPLOYMENT_KO.md only.
- [ ] Test actual temporary SQLite files and manifests first: stopped-writer precondition, integrity/hashes, symlink/traversal rejection and no overwrite restore.
- [ ] Provide fixed loopback systemd/nginx/env templates with no actual secrets and explicit disabled trading/demo/default offline settings.
- [ ] Implement bounded standard-library snapshot/verify/restore-to-new-directory tools preserving all six ledgers and datasets; require all writers stopped and document operational limits.
- [ ] Provide reproducible source/build/install verification instructions, preserve Git provenance, separate code/state/secrets, and avoid automatic downloads/public exposure.
- [ ] Run portable unit tests locally and Linux-only meaningful boundary checks in existing Linux CI; do not create a VPS.

## Task3 — Server-only Anthropic readiness and correct connection documentation
Owner paths: src/multi_exchange_trader/ai/config.py (new), controls.py, tests/ai/test_config.py (new), related focused controls tests, docs/account/BYBIT_READONLY.md, docs/operations/CONNECTIONS_KO.md only.
- [ ] Add failing tests for absent/blank/malformed env, disabled env-file loading, precedence, redaction, and no transport activity.
- [ ] Read only allowlisted Anthropic server environment/file configuration. Support an explicit ANTHROPIC_ENV_FILE shared with the future user-provided credential file; preserve existing environment precedence and safe errors.
- [ ] Extend connections/status.ai additively to report key presence and REGISTERED_UNVERIFIED separately from actual verification. Keep OFFLINE_TEST/external_calls=false/verified_at=null and existing storage-unavailable behavior.
- [ ] Do not infer a model ID from '5.5 하이쿠', hardcode a provider balance, or add a paid-generation endpoint.
- [ ] Correct current Bybit demo/live docs against code and explain safe future credential-file setup; preserve explicit approval for DEMO order effects.
- [ ] Run focused tests and all applicable portable checks; root runs combined Linux CI.

## Integration
- [ ] Independent spec and security/code review, fix important findings, inspect Git diff for secrets and unrelated changes.
- [ ] Run the full Linux/backend, frontend and browser CI on the exact PR head. Do not claim local Windows native qualification.
- [ ] Merge only when required checks and review pass, then verify main and record remaining physical-PC/credential/server prerequisites.

# Automatic mode selection implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for implementation and independent review.

**Goal:** Enforce the user's confirmed distinction between FULL_AUTO and ALL_IN_ONE without giving AI price, sizing or risk-setting authority.

**Architecture:** Both modes return an immutable server-issued candidate for the same deterministic execution core. FULL_AUTO follows explicit user-selected symbol/strategy scope; ALL_IN_ONE accepts only a strict AI response selecting a candidate from a bounded server-generated allowed list. No provider call or production order path is added.

**Tech Stack:** Python3.12, frozen dataclasses, strict JSON, pytest; existing execution risk types where useful. No new dependencies.

**Spec:** User confirmed on 2026-11-02: FULL_AUTO executes user-selected symbols/strategies; ALL_IN_ONE permits AI to select symbols and strategies from user allowlists; both share user capital/leverage/loss limits. `docs/ROADMAP_FINAL_PRODUCT.md` D4/D5/D6 apply; this new user decision expands AI authority only to allowed candidate selection.

## Global constraints

- Work only automatic-modes branch gpt/automatic-modes-20261102, based on core remote51ea8c750549720ca06e01c3a625280c5088a1bb. No main edits, staged changes or commits by implementer.
- Own execution/modes.py, tests/execution/test_modes.py and docs/implementation/AUTOMATIC_MODES_KO.md only. Existing core files unchanged.
- No credentials, authenticated requests, orders, paid AI/provider calls, new servers or capability/API/UI activation. Actual LIVE remains unsupported and real native qualification is pending.
- Candidate universe and policy are trusted server records, never supplied by AI. Candidate IDs alone are not sufficient authority: account, environment, strategy revision, public snapshot, policy revision, expiry and allowlist must agree.
- AI selects candidate ID or abstains. Never read numeric values, code, tools, policy, leverage, prices, SL/TP or sizing from AI output. Reject unexpected JSON fields and duplicate keys, do not silently discard them.
- Both modes consume the same fixed risk policy revision. A selection is NOT order authorization and must pass fresh risk/ownership/protection/native gates later.
- No hidden numeric financial defaults. UTC times, explicit size/input/resource caps, finite representations, no auto fallback from AI/provider failure to unapproved strategy.

## Review focus

1. An AI-selected ID outside the supplied candidate set, stale snapshot or wrong policy revision is rejected without altering the approved server candidate.
2. Two strategies with same name but different revision or different accounts/environments cannot be confused.
3. AI JSON contains duplicate keys, bool schema version, extra numeric/tool/code fields, unknown decision, oversized reason or injected text: fail closed, no eval/exec.
4. FULL_AUTO cannot be expanded to a symbol or strategy outside explicit user selections by a candidate or model response.
5. ALL_IN_ONE absence/failure/abstention blocks new selection, but it must not produce any command affecting existing protection or exits.

## Task 1: Pure automatic candidate selection

Files: create execution/modes.py, tests/execution/test_modes.py and docs/implementation/AUTOMATIC_MODES_KO.md.

- [ ] Define frozen explicit mode/scope/policy/candidate/snapshot/AI-selection/result types. Choose simple narrow API, send exact signature to controller early. Avoid duplicating full risk model or calculating market numbers here; include only references to immutable server plan/candidate evidence.
- [ ] FULL_AUTO selects only a user-selected, server-issued, enabled, unexpired candidate with matching symbol/strategy revision and signal/policy/snapshot scope. Deterministic ranking/tie-break policy must be explicit server input or reject ambiguous selection; no hidden preference or numeric sizing defaults.
- [ ] ALL_IN_ONE builds a bounded public candidate request without private account data; strict parser accepts schema_version integer1, matching snapshot_id, decision SELECT_OR_ABSTAIN or equivalent explicit SELECT/ABSTAIN contract, candidate_id, bounded explanation only. Bind model decision to server request ID, policy revision and selected immutable candidate. If abstain/failure, return no candidate; do not silently select a rule candidate. No provider transport or model availability claim.
- [ ] Enforce allowlists, exact revisions, account/environment, explicit candidate bound, reason/input size limit, UTC freshness/expiry/future rejection. Reject duplicate candidate IDs and conflicting records, no prefix ownership or generated strategy code. Selection result includes original fixed policy revision and candidate metadata, never new risk numbers from AI.
- [ ] Meaningful RED->GREEN tests for all above failure modes plus correct two-mode behavior. Run focused pytest, Ruff, strict mypy; do not run whole repository tests. Root integrates with already-tested core in CI.
- [ ] Document exact confirmed semantics and missing provider/native/UI activation clearly. Save implementation report outside repo ../server-setup-evidence-20261102/next-phase/AUTOMATIC_MODES_IMPL_KO.md. No staging/commit.

## Task 2: Independent review and exact-head integration

- [ ] Independent review tests ID/scoping/policy/JSON boundaries and honest claim limits. Fix actual findings and re-review.
- [ ] Full applicable checks; reviewed exact-head CI passes before authorized main merge. Keep real LIVE gate disabled. The common execution core PR remains an independent predecessor.

# Failure-only screenshot observations Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development for this single independently reviewed diagnostic task. This is the root's implementation choice under the user's existing autonomous continuation instruction, not a newly user-approved visual change.

**Goal:** Preserve a fixed sequence of additional same-option images after an original strict screenshot assertion has already failed, so a new failure can distinguish later same-option observations from the runner's different automatic capture.

**Architecture:** Add one test-only sidecar and one guarded catch at the original comparison boundary. Preserve the original candidate generation, first consecutive pair selection, strict pixel comparison, exact PNG SHA assertion, original thrown value and finally diagnostics. The additional samples never select a candidate or baseline and cannot turn a failure into success. This is observation preparation, not a claimed fix.

**Tech Stack:** Existing TypeScript, Node24 and pinned Playwright1.64.1. No dependencies, browser flags or workflow changes.

**Spec:** CI146_MOBILE_PIXEL_INDEPENDENT_DIAGNOSIS_20261104.md, read fully by root. Its conclusion remains ROOT_CAUSE_UNDETERMINED. Root chooses failure-only observations in the current context rather than a new discovered sidecar test, preserving the original strict execution as the first and only acceptance decision.

## Global constraints

- Own only apps/web/tests/ui-browser/helpers.ts and NEW apps/web/tests/ui-browser/failure-samples.ts in a new isolated worktree. No app, CSS, fixture, baseline, threshold, retry, config, package, workflow or original spec changes.
- Preserve every original successful capture operation and its order. Run new observation only after entering the two strict assertions and catching their error. No sampling for missing baseline, capture-only mode or earlier setup/capture/metadata failures.
- Exactly four ordinal observation slots, without matching-based early exit or retry. Unavailable/closed/deadline cases are explicitly recorded rather than relabeled captures. Never compare an observation with the baseline or feed it to the original assertions.
- Same original fullPage/caret=hide/animations=disabled/scale=css settings. A finite screenshot timeout may be added solely as an observation resource bound and must be recorded. Font-ready plus two-rAF preparation is retained with a bounded wait.
- Fixed total elapsed budget and finite per-operation deadlines; stop scheduling work after expiry. No detached future screenshots. An outstanding read-only page evaluation is not capture permission. Attach all obtained PNGs in ordinal order and one sanitized bounded manifest with hashes, options, completeness and timing caveat. Never attach general protocol/network logs, secrets, screenshot base64 in JSON or raw error text.
- Catch every optional observer failure and rethrow the EXACT original caught value. The original finally diagnostic still runs. No new pass condition, baseline regeneration, tolerance, mask, skip or acceptance retry.
- Use existing readonly fixed Node. All new writable temporary files stay in F. No installs, application/browser execution locally, SDK, provider/order, paid AI, privateenv/vault/operator DB, server or remote action by author. Root alone Git and CI.

## Review focus

1. Original assertion error identity and non-Error thrown values survive observer errors.
2. Stable first-pair acceptance code and success path are byte-identical except a side-effect-free strict-boundary flag.
3. Four slots never become a retry-until-match loop; every captured image remains independent evidence.
4. Deadline expiry or closed page schedules no later screenshot; unavailable records remain distinct.
5. Options observed are the same render options as the failed candidate; later automatic screenshots remain a separate arm.

## Task 1: bounded failure-only sidecar

- [ ] Freeze the supplied baseline, root plan, original helper bytes and all unowned tracked tree entries before editing.
- [ ] Implement the new sidecar with fixed four slots, ordinal PNG attachments, SHA256 and bounded sanitized manifest. Never read the reviewed baseline in it. Record native timeout, capture options and incomplete observations.
- [ ] Add a strict-boundary flag and guarded catch in capturePair. Existing original try operations and finally remain in their original order; capture-only and pre-comparison failures do not invoke the sidecar. Catch the sidecar's error and throw the exact original value.
- [ ] Run bounded local TypeScript syntax/type/lint checks using already-installed tools only. Direct injected diagnostic probes may verify all-four/nonmatching behavior, closed/deadline/no-late-screenshot and original-error preservation; do not count them as actual browser validation.
- [ ] Freeze exact owned bytes, baseline/unowned custody, actual commands/results/PIDs/limits and report/source pins/receipt. Different independent CODE review, root final composition and new exact full Linux CI remain required. Root alone commits after review; no main authority is granted by this plan.

Self-review: all five focus items map to the single diagnostic slice. No product fix or actual browser success is implied; original strict acceptance stays mandatory.

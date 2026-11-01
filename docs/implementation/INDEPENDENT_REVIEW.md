# Independent backend and integration review

Review date: 2026-11-01. Baseline 7336c973. Reviewed additive controls/capabilities/read models, AI budget, durable manual review, recommendation list/detail, PAPER-store changes, research V2 configuration/automation/storage/native/evaluation/scheduler and API integration. Spot additions were also checked for qualification gating. Read-only review; no product changes or account/provider calls performed.

## Important finding requiring correction

**[Important] The 24 visual routes render separate demonstration implementations, so they do not verify the working program.** `apps/web/src/App.tsx:13` selects `visual/ReferencePages` when `route.demo`, while line 17 selects old functional Paper/Recommend/Research/Account implementations for actual routes. `apps/web/src/visual/ReferencePages.tsx` also owns independent hardcoded forms, costs and histories. Consequently a 24-screen fixture screenshot can pass while actual order forms, research configuration and account layouts remain different. This conflicts with plan 13.2 (same components with fixture responses), and cannot support completion of Tasks 2,3,5,6,7,9. Refactor common page presentation to receive actual or fixture view models/controllers; capture both fixture and real empty/unsupported states. A demo badge and a warning make the example honest but do not resolve this architecture/verification gap. Parent and frontend owner notified.

## Backend verdict and task compliance

No new Critical or Important backend correctness defect was confirmed in the inspected additive contracts. Backend integration is **conditionally acceptable pending Linux native tests and final API/full checks**. This is not an engine qualification claim.

| Task | Observed implementation | Review verdict |
|---|---|---|
|4 capabilities/overview/diagnostics|Server-owned live-order=false; native runtime prerequisite separated from supported contract; independent ledgers preserved; overview reads only local PAPER/research/spot stores and never account APIs; scheduler liveness distinguished from progress|Consistent in inspected paths. Diagnose partial storage failures without fabricating zero balances.|
|5 manual review|Review persists stable token and confirmation key before applying `review-<preview_id>` through existing immutable ledger command transaction; prior result recovers before revision/expiry checks; duplicate key conflict and changed revision fail closed|Durable uncertain-result pattern is sound on inspection. Native no-effect preview, concurrency, expiry and restart tests must pass on Linux.|
|5 spot qualification|Separate versioned cash ledger/native adapter; placement only via review/confirm; V1 direct order payload still rejected; QUALIFIED=False; server capability remains false|Correctly gated. Not delivered as usable spot feature until the full Linux suite qualifies and flag changes through review.|
|6 recommendation read contract|List/detail return RULE source and expire saved records without new market/provider calls|Additive compatibility preserved. Frontend response-selection races require separate browser/unit evidence.|
|7 entry pause/leverage|V2 exact config field validation/hash; leverage emitted in native entry commands; ENTRY_PAUSED continues ticks/risk exits but suppresses entry; PAUSED keeps old meaning; restart moves active states to PAUSED_RECOVERY|Consistent in inspected paths. Native replay and restart regression remain qualification requirements.|
|7 evaluation V2|Versioned exact config; dataset/range/fee/slippage/leverage validated; request identity includes config; native fees/leverage/slippage threaded into execution; original V1 metadata and report branch retained|Conditional. Root reports subsequent initial-equity/split additions with 13 pure checks/28 subtests; these later edits were not fully re-reviewed here and require latest native CI. Percentage-to-tick slippage is a documented UI difference, not equivalent semantics.|
|8 AI settings/budget|OFFLINE_TEST only; no external provider calls; Decimal strings; BEGIN IMMEDIATE reservations/configuration/settlement; unknown usage retains exposure; provider balance remains null|Consistent with fail-closed offline scope. Actual provider/model/permission/paid usage not implemented or verified.|
|9 visual production parity|Separate demo and live page implementations in App|Important gap above; screenshot success for demos cannot establish production compliance.|

## Evidence and limitations

This independent pass inspected source directly. Parent-reported evidence at handoff was 11 pure research contract checks and 7 dependency-free backend smoke checks; root subsequently reported 13 pure checks/28 subtests for extended evaluation. Those are reported evidence, not independently rerun by this reviewer. Native replay integration, complete ASGI/error-boundary checks, full regression and latest-SHA Linux CI must be recorded by the coordinator. Existing V1 ledger metadata/schema/checksum assumptions were preserved by the inspected default paths; no database reset was introduced.

Do not merge or describe the full plan as complete solely on this review: resolve common-view parity and inspect actual routes, then attach final native CI and exact final source SHA.


## Follow-up independent source review after integration fixes

The original Important demonstration/production split is **resolved structurally in current source**: App now routes overview/connections/diagnostics/market/account/paper/recommend/research through the same page controllers and screen components in both modes. The separate `ReferencePages` routing branch is removed. This does not establish visual agreement: final actual/live states and fixed fixture captures still require browser inspection.

Two additional Important findings were raised during integration and checked again:

- **Spot/recommend fixture-mode state reuse — resolved in App source.** Mode-initialized state previously persisted across live↔fixture navigation. App now keys SpotPaper and Recommend by `route.demo`; Research was already keyed by page/ID/mode. This forces fresh pending-command restoration in actual mode and prevents a fixture banner displaying retained actual ledger state. Targeted regression passes were reported by the coordinator/implementer; this reviewer did not rerun them.
- **Saved recommendation overwritten by preview — resolved in source, final tests reported.** Selecting a historical record previously changed filters and triggered a replacement preview, or allowed an already-pending preview to overwrite the selected saved card. Recommend now has explicit `selectionMode` preview/saved, suppresses automatic preview in saved mode and verifies the preview response against `selectionGeneration`. The coordinator reports the targeted history-race check passed. Final full-suite evidence must match the final SHA.

**Current verdict:** no confirmed unresolved Critical/Important defect from this independent source pass. Conditional acceptance remains dependent on the latest combined-source regression/CI and final 24-screen inspection. Earlier native CI success (492 Python checks plus 28 subtests, Ruff, mypy) is coordinator-reported evidence for its recorded SHA and must not be treated as coverage of later frontend edits.

Reference assets were preserved and inspected individually: all 24 original PNGs plus README, supplied ZIP SHA-256 verified, per-file SHA-256 recorded. All 24 PNGs were additionally uploaded as GitHub blobs without branch/tree updates; every returned blob SHA matched the locally computed Git blob hash. `ORIGINAL_BLOB_MAP.json` contains the complete verified mapping. The initial screenshots remain preliminary; monetary-unit wrap and header/control layout were reported for correction, and stale screenshots are not completion evidence.

The read-only comparison helper in `scripts/ui-qa` is now owned by the coordinator. CUA image APIs were found to produce resampled/cropped raw image dimensions on some classic-scrollbar pages; record raw and CSS sizes, and do not stretch those images or change product scrollbar behavior to accommodate the capture API. Exact-size Linux Playwright PNGs should be the primary normalized original-overlay and later regression evidence. The plan's 2 CSS px boundary target and pixel-perfect agreement have **not** been independently proven by this review.

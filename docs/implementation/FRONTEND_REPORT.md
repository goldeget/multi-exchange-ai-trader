# Frontend implementation report — 2026-11-01

Owned product scope: apps/web (parent owns integration, package dependencies, Git, browser captures; account/market delegated to backend agent; parent implemented app/live shared read pages).

## Implemented
- Twelve distinct URL pages, legacy query views, pushState/popstate/back-forward navigation, explicit selected run/report IDs, and safe missing IDs.
- Common 200px seven-menu desktop shell and five-tab mobile shell, Korean 16/14px typography, tabular numbers, SVG icons, red-up/blue-down candles and volume, responsive layouts and local Pretendard v1.3.9 WOFF2/OFL.
- Every App route now renders the same controller/presentation for real and fixture data. Deleted the obsolete separate ReferencePages renderer. Fixture activation is gated by import.meta.env.DEV; real production ignores demo=1. No fixture actions call APIs or modify operator pending-command storage.
- Preserved account polling/deadline/generation safeguards through delegated AccountScreen; shared MarketScreen retains four quote polls plus one selected candle poll and venue isolation.
- Existing durable research command/intent path drives five shared polished screens. V2 leverage, entry-only pause versus full pause, exact fee conversion, UTC-aligned evaluation periods, capital and chronological split controls follow capabilities and native availability. Run/report deep links never choose unrelated records.
- New shared spot-paper UI performs server preview then durable same-token/same-key confirmation, shows native accepted versus filled state, separate CASH balances/reservations, local synthetic quote input and cancellation; unsupported/unavailable operations fail closed. Synthetic-perpetual V1 remains available as a separate explicitly labeled ledger with reviewed commands.
- Recommendation selection generation prevents old approval/preview responses replacing a new card. Saved selection explicitly stops preview creation and consumes validated GET list/detail through RecommendationHistory. Expiry advances without a fetch. RULE/source and optional fixed DEMO connectivity effects are explained separately, including acknowledgement versus actual close verification.
- Common dialogs trap keyboard focus, support Escape and focus return, orient initial focus at the title without scrolling, and disable dismissal while research commands are submitting/uncertain.

## Verification observed in this agent session
- Router contract: four tests passed.
- Original full frontend suite at initial checkpoint: 58 passed / 14 failed; failures were recorded, then addressed through intended route/navigation updates and recommendation generation guards. This was not reported as a passing final suite.
- Latest focused command: SpotPaper.test.tsx + Recommend.saved.test.tsx + evaluationConfig.test.ts — seven tests passed (4 + 1 + 2). These cover native object shape, incomplete-state rejection, definitive 409 expiry recovery, same preview/key after lost response/remount, saved different-pair selection with stale preview, exact 0.04% to 0.0004 ratio and all V2 fields, and invalid inputs.
- Recommend.test.tsx + evaluationConfig.test.ts — five tests passed earlier after race and conversion fixes.
- TypeScript passed after all twelve routes were wired, before the final focused controller tests. Parent is running the final full test/lint/typecheck/build gates against the source freeze.
- Delegated reports: account 18 tests passed, market shared/controller tests passed; parent owns final consolidated evidence.

## Review findings handed to parent at source freeze
- ResearchScreen decimal grouping should group only the integral part; parent explicitly took ownership of this final display-only fix.
- Confirm server native spot orders include instrument for cancellation; frontend currently uses returned instrument and falls back to selected instrument. Backend validation prevents mismatched cancellation from mutating another instrument. Parent is inspecting the contract.
- Native engine qualification, final browser baselines/overlays and final CI/commit status belong to the parent. This report does not claim pixel identity or completion of all visual acceptance checks.

Source edits frozen at parent request; no commits or real-account/order calls were made by this agent.

## Final scoped correction after first freeze
Parent reopened only Research.tsx and a new regression file. The inherited dataset loader now aborts previous requests, checks a generation before state/error/finally updates, and aborts on unmount. Evaluation requires dataset.dataset_id to match the selected datasetId. Research.dataset.test.tsx was observed failing before this change (no AbortSignal), then the new test plus original Research tests and exact configuration tests all passed: 19/19. Source frozen again immediately afterward.

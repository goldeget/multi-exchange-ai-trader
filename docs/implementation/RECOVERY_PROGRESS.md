# Implementation ledger — UI_ARCHITECTURE.md

Baseline: 7336c9739644e2cf7df1e87e53dd676ca3ccb7e0 (origin/main).
Local branch: gpt/ui-recovery-20261101. No original uncommitted source was recovered; preserve original supplied design and reconstruct against the baseline.

## Scope and rulings

- User explicitly requests parallel GPT agents and actual browser/plugin verification. This overrides the skill's sequential implementer preference. File ownership: frontend agent apps/web; backend agent src/tests except research; controller research integration, tooling/Git; reference/QA agent docs/ui-reference and scripts/ui-qa. No shared file mutations without coordination.
- Existing detailed architecture and mockups are the accepted design; user requests execution, so do not repeat design approval.
- Existing main and real accounts/positions are untouched. No live/demo orders, paid inference, deployment or auto-merge. Keep actual providers disabled until separately configured/authorized.
- Windows has no qualified Linux native environment; run pure backend and UI checks locally, native engine checks on isolated Linux CI. Do not substitute another engine or loosen sandbox.
- Full clone was slow due to historical binary QA evidence. Use a sparse partial checkout with code/tests/contracts and preserved Phase4 dataset snapshots. Keep current reference images separately with their original hashes.

## Preflight interface scan

| Tasks | Shared interface | Ruling |
|---|---|---|
| 1 | baseline/assets | Archive hash verified; preserve all24 originals and per-image mapping |
| 2,3 | navigation/design | One frontend owner; query routes retain existing URLs/popstate |
| 3,4 | read contracts | Backend publishes additive versioned capabilities/overview/diagnostics, frontend validates unknown/failed capabilities closed |
| 5,6 | command approval | Existing durable intent/idempotency preserved; previews don't execute; confirmations revalidate |
| 5,7 | native engine | One native execution path; no new simulator; product support only enabled with integration evidence |
| 7 | version migration | Preserve old run hashes/event metadata; V2 config separate and immutable |
| 8 | AI budget | No inference until actual provider setup/authorization; transactional reservations independent of PAPER worker |
| 9 | reference fixtures | Same components with explicitly labeled fixed demo data; production sources never silently replaced |
| 10 | phone access | Responsive UI is in scope; actual remote phone session requires configured authenticated access; retain loopback boundary |

## Tasks

1. Baseline/reference preservation — all24 originals and README preserved; archive and per-image hashes verified. No previous operator database was found in the supplied workspace (excluding disposable QA data and tool caches).
2. Shared design/app shell — implemented; seven desktop sections, five mobile tabs, local Korean font, shared fields/dialog and mobile reflow.
3. Routing/read screens — implemented with common actual/fixture components, popstate/direct routes, source isolation, retained-value/error distinctions and native chart lifecycle preservation.
4. Capabilities/overview/diagnostics — backend qualified; frontend rejects incomplete capabilities and retains independent ledger scopes.
5. Manual preview/confirmation — qualified SPOT-PAPER-v2 on Linux; frontend object contract, definitive rejection and lost-response same-key recovery tested. Windows runtime remains unavailable.
6. Recommendation list/detail/races — implemented; source/target/effect validation, saved-selection generation guard, expiry and durable global uncertain-DEMO protection. No DEMO request was exercised.
7. Automation/evaluation V2 — all native contract tests passed in CI50, including capital/splits. Dataset selection now aborts/ignores late responses and binds review to the selected dataset.
8. AI settings/budget — OFFLINE_TEST and transactional budget implemented; external provider unconfigured and unverified.
9. Browser/visual/integration verification — direct IAB interaction checks and24 current screen pairs captured. Pinned Playwright1.64.1 suite added:114 registered,90 expected executions after24 PC-only narrow duplicates skip. Initial Linux screenshot candidates must be reviewed before a strict regression baseline is committed; final source CI is pending.
10. Authenticated phone access — not configured; local boundary preserved

No task is complete until its actual checks and independent review are recorded here.

## Checkpoints and observed evidence

- Draft PR: https://github.com/goldeget/multi-exchange-ai-trader/pull/9
- Concurrent main update b626501 (practice approval round-trip) was integrated without changing main. A separate temporary worktree isolated that merge from ongoing UI edits.
- CI48, head ab26618:478 Python tests passed,2 failed in338.79s. Failures: exact API inventory lacked14 intentional endpoints; spot buy limit filled one tick above its limit. Both causes were identified and corrected; do not qualify spot until the corrected native check passes.
- CI48 frontend, secret scan and dependency review passed on the backend checkpoint. This does not validate the later UI implementation.
- Native Automation V2 leverage/entry-pause/restart and original Evaluation V2 tests passed within CI48. Capital/split extensions require the next native run.
- Local current root contract checks:24 tests and28 subtests passed; API transport/route contract suite40 passed; full Ruff passed before the next checkpoint.
- Browser initial evidence:12PC captures at1440x1024 and12mobile captures at390x844, saved outside the source tree under ../browser-evidence/. All had no body horizontal overflow. Header overlap was corrected and rechecked. Two small mobile targets were sent for correction.
- Separate ReferencePages and production legacy controllers were identified as a material fixture/production parity gap. Frontend refactoring is in progress; initial demo screenshots are not final production acceptance evidence.
- Inherited DEMO approval sends a fixed connectivity round trip, not the recommendation's direction. Submission acknowledgements do not verify a flat position. Durable global uncertain-attempt blocking and explicit metadata were added; no DEMO call was exercised.
- Second CI checkpoint acb22f4: corrected spot policy, uncertain-DEMO protection, evaluation capital/splits, and exact route inventory submitted for qualification. All support flags remain off pending evidence.

- CI50 source1e3ba764: all four jobs passed. Python492 tests/28 subtests, Ruff src/tests/scripts, mypy62 files, previous frontend, secret scan and dependency review succeeded. Support flags now describe qualified contracts; runtime availability is checked separately.
- Current frontend checkpoint passed117 tests locally before the final dataset-generation and page-failure guards. Their additional targeted checks passed; latest complete-source CI remains mandatory.
- The duplicated ReferencePages implementation was deleted. Actual and fixture modes share page components; Spot/Recommend source-mode changes remount their controllers, and read models key state by source. Saved recommendation selection cannot trigger or be overwritten by a new preview.
- Direct IAB checks: spot review costs/reservations, focus trapping, Escape dismissal, focus return, saved ETH expiry, mobile menu/back navigation, and production ?demo=1 rejection. No actual order/DEMO/provider action was invoked.
- IAB screenshot APIs may resize viewport images when classic scrollbars are present; raw dimensions are recorded. They are manual review evidence, not canonical pixel baselines. Linux pinned-browser PNGs verify font readiness and canonical viewport sizes before screenshot comparison. Physical phone access, the OS virtual keyboard and actual browser200% zoom remain unverified; narrow viewport reflow is tested separately.

## Latest integrated review

- Combined source `feda6c25` passed all five jobs in run41272013569: Python492/28 subtests, Ruff, mypy63; frontend120/lint/types/build; browser90 with24 intentional desktop duplicate skips; secret scan and dependency review. Browser mode was capture-only, so it did not establish strict pixel regression acceptance.
- Independent offline-image review found P2 unknown-versus-empty/unsupported wording defects. Overview, recommendations/history and research now distinguish a failed read from a successful empty list. Failed capabilities no longer imply unsupported spot orders, fixed V1 leverage or V1 evaluation fallback. New unit and browser assertions protect these cases; commands remain blocked while capabilities are unknown.
- Direct normalized reference comparison exposed excessive mobile header/context height. The shared mobile header now keeps title, KST time/example label and environment badges compact; desktop description is omitted on mobile. Manual ledger selection is compact, creation remains a labelled48px button, and the V1 link follows the order form. The fixture's current candle now agrees with the canonical65,210.40 quote and forming-hour timestamp; volume labels no longer appear on the price-axis region.
- IAB1280×800 and768×1024: all12 routes at each width had one h1, no replacement characters and no body horizontal overflow. Six additional viewport images are preserved locally. This is layout evidence, not actual browser zoom or physical-device proof.
- Earlier mobile/narrow screenshot candidates are stale after header changes. Capture the current source, review every candidate and commit explicit baselines before removing capture-only mode. Original-image2 CSS px agreement remains unproven; do not equate an approved implementation regression baseline with pixel identity to the generated references.
- User operating instructions: [USER_HANDOFF_KO.md](USER_HANDOFF_KO.md). Earlier investigation/review checkpoints remain historical evidence and are superseded by the final report's source and CI result.

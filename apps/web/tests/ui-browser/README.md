# Fixture browser checks

This suite uses only the local Vite development fixture routes. Every test blocks and records API attempts and off-origin HTTP requests, and fails if either occurs. The browser date is fixed at `2026-11-01T00:10:03Z`; timers and chart animation frames remain usable.

The `pc` and `mobile` projects cover all 12 routes. The mobile project additionally covers every route at widths 320 and 360. Each route records viewport and full-page PNGs after the bundled Pretendard font and visible chart canvases are ready. Interaction checks cover dialog keyboard focus, Escape/return focus, fixture confirmations, storage preservation, browser history and safe direct IDs.

All 12 actual routes also run against controlled HTTP 503 read responses. No request reaches the API: GET reads and the existing recommendation-preview POST read are fulfilled by the test; every other non-GET command is blocked and fails the audit. These cases check failure text, absence of fixture substitution, disabled confirmation paths and unknown balances/budgets instead of fabricated zeroes.

`UI_CAPTURE_BASELINE=1` captures candidates only. Each PNG is written under the test output directory as `baseline-<name>.png`, attached to the report, and accompanied by a JSON manifest containing its project and intended baseline path. Capture mode does not approve or write baselines.

Without that environment variable, every comparison requires an existing reviewed image at `baselines/<project>/<name>.png`. Missing images fail with instructions; snapshot updates must remain `none`. Comparisons use zero differing pixels and a zero color threshold. Do not use automatic snapshot updates. Review the Linux CI candidates before copying and committing any baseline.

The suite was authored and type-checked locally. Browser execution and baseline qualification run in the authorized Linux CI environment; unit-test success is not evidence that these browser checks passed.

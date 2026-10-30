# Work log

## 2026-10-30 UTC — Phase 1 research and documentation milestone

The repository began as a README and Python `.gitignore` skeleton, without application code, dependency manifests, CI or a test suite. Work is on `codex/initial-platform`; `main` is preserved. Specification/plan filenames use 2026-10-31; the source-review date is **2026-10-30 UTC**.

Reviewed official project repositories, release/package metadata, licenses, docs and visible test/CI/security sources. Research agents supplied draft engine, exchange-SDK and AI/UI notes; this milestone reconciles those notes against the [design](superpowers/specs/2026-10-31-multi-exchange-trader-design.md) and [plan](superpowers/plans/2026-10-31-public-market-dashboard.md). No project install, upstream test execution, benchmark, exchange request, credential access, order or account operation was performed for this documentation milestone.

Decisions recorded:

- [Open-source audit](OPEN_SOURCE_AUDIT.md): NautilusTrader is the one future execution/backtesting core. v1.231.0 is the current Python 3.12-compatible candidate, titled “Beta”; wheel/API/LGPL-3.0 qualification is required before Phase 3. Hummingbot's no-key Binance example remains a valid one-venue alternative, not a second selected engine. All requested engines, SDK groups, FreqAI, Condor, Qlib and Lightweight Charts are included, with evidence-linked ordinal role-fit scores.
- [Exchange capabilities](EXCHANGE_CAPABILITIES.md): Phase 2 uses narrow HTTPX public-read adapters. CCXT MIT is a future breadth option; Pro WS terms are separate. OKX's official docs host was not re-fetched, so unverified routes/limits remain disabled pending official verification. SDK/private/order capabilities are research only.
- [Architecture](ARCHITECTURE.md): documented local FastAPI/React data flow, validation/freshness/UI requirements, no persistence or account access in Phase 2, and separate future execution/risk/AI boundaries.
- [AI design](AI_DESIGN.md), [security](SECURITY.md) and [QA plan](QA_PLAN.md): preserved their detailed policies, cross-linked delivery documents and aligned phase names. Deterministic advisory analysis starts in future Phase 5, not the current dashboard.

| Check | Scope / environment | Status | Evidence and limitation |
|---|---|---|---|
| Source/citation reconciliation | Documentation; supplied review notes and cached official GitHub/doc content, 2026-10-30 UTC | PASS | Audit/capability citations retain release dates and unknowns as sourced. No fresh remote link-liveness crawl or endpoint verification is claimed. |
| Scope/phase self-review | Documentation on `codex/initial-platform` | PASS | Phase 1–2 current; Phase 3 PAPER, 4 backtesting, 5 AI, 6 Demo, 7 approval, 8 AUTO, 9 QA/security expansion, 10 live readiness future. No future capability enabled. |
| `git diff --check` | Documentation working tree before task commit | PASS | Exit 0, no output; recorded again in the task report. |
| Local Markdown target existence | Relative file links in task documentation | PASS | All linked local files exist; fragment text manually reviewed. |
| Application/upstream tests and benchmarks | No application suite exists in this docs milestone | UNRUN | No install/build/runtime/CI pass is claimed by research. |
| Exchange connectivity / official OKX contracts | Managed cloud network | UNRUN | No exchange endpoints called; OKX verification disabled pending official docs access. Source review and host drafts do not prove connectivity. |

Known source limitations: logged-out advisory pages without visible IDs mean unknown status, not absence of vulnerabilities. SDK package streams differ by language/product; unverified publication dates remain unknown. Qlib's config-driven execution hardening commit needs a separate version/exposure review. The unofficial OKX wrapper's license was not verified. CI definitions and test trees were inspected, not executed. GitHub non-prerelease metadata does not remove NautilusTrader's Beta title.

The Phase 1 documentation milestone was committed and pushed to `codex/initial-platform` in `a5d2c45`, `40eb6f5`, and `fe4014f`. Later milestones and current evidence follow.

## 2026-10-30 UTC — Phase 2 public market dashboard and cloud setup

Implemented the public-only local dashboard and read-only API on `codex/initial-platform`. The overview shows BTC and ETH public tickers from Binance and Bybit for spot or perpetual markets; the chart follows the selected venue/instrument. REST polling is bounded and displays source/fetch state. OKX remains explicitly disabled and no OKX upstream request is made because authoritative V5 route/schema/limit/mapping/time/candle verification is still unavailable. The opt-in `?preview=1` mode uses deterministic local fixtures, labels them `샘플 데이터 · 실제 시세 아님`, and its UI test asserts zero API requests.

No exchange credentials, private account routes, manual Bybit position data, order transport, or future PAPER/backtest/AI/Demo/approval/AUTO/live capabilities were added. No exchange endpoint was contacted. Live provider connectivity is **unverified**: the Cloud Environment settings draft contains the approved public host allowlist but the current runtime still enforces the old network policy until the draft is reviewed and published.

| Check | Scope / environment | Status | Evidence and limitation |
|---|---|---|---|
| Backend unit/API tests | Python 3.12; local fixtures and mock HTTP transports | PASS | 241 tests passed. No exchange calls. |
| Python lint and typecheck | `uv run ruff check src tests`; `uv run mypy src` | PASS | Both exit 0. |
| Frontend UI tests | Vitest with jsdom and mocked fetch/chart | PASS | 8 tests passed, including both-asset overview, disabled OKX, preview zero-fetch, stale/error states and polling cleanup. |
| Frontend lint/typecheck/build | ESLint, TypeScript, Vite production build | PASS | All commands exit 0. |
| Secret scan | Gitleaks v8.30.1, verified Linux archive SHA-256 | PASS | Redacted full-history scan reported no leaks; no API key values were read or added. |
| Dependency and action review | Locked Python/npm dependencies; GitHub workflow | PASS (local review) | Action release tags were matched to full commit SHAs; dependency-review is read-only. A hosted GitHub Actions run has not occurred. |
| Local health endpoint | FastAPI at `127.0.0.1:8000` | PASS | `GET /api/v1/health` returned `{"status":"ok"}`. |
| Synthetic browser preview | Vite at `127.0.0.1:5173/?preview=1` | PASS (HTTP) | Preview URL returned HTTP 200 and the Codex browser tab was queued. No screenshot-based responsive audit was performed. |
| Exchange connectivity / official OKX contracts | Current managed cloud runtime | UNVERIFIED / DEFERRED | Current egress policy does not include draft custom hosts. Do not infer live health from local fixtures or health endpoint. OKX stays disabled until official contract evidence is recorded. |

Reviewed implementation commits on the work branch: `ca01d13` (market-data contracts/adapters), `79cbfc8` (API), `d046386` (dashboard), `261bd34` (BTC/ETH overview review fix), `6882747` (CI and setup docs), and `7532564` (Node 24-compatible lint tooling), in addition to the Phase 1 documentation commits above. Each reviewed slice was pushed to `origin/codex/initial-platform`.

Cloud setup uses the existing repository checkout and writable `/tmp` caches for uv/npm. The environment draft has been saved with the `multi-exchange-ai-trader` mount path, tested install/start instructions and the four public hostname rules; draft saving does not apply them. User review and publication are still required before real public market data can connect. No custom credentials or runtime secrets are required.

Maintenance note: final `npm ci` reports one upstream deprecated transitive package, `whatwg-encoding@3.1.1`, pulled by the jsdom test environment. The direct unsupported ESLint 9 warning was resolved by pinning the Node 24-compatible ESLint 10 toolchain. The remaining deprecation notice did not fail install, tests, lint, typecheck or build.

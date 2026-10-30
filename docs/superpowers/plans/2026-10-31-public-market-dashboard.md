# Public Market Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Tasks use checkbox syntax; implement in order and review each task before integration.

**Goal:** Deliver the requested first milestone: research and architecture documents plus a Korean-first local dashboard for public BTC/ETH spot and perpetual market data on Binance, Bybit, and OKX.

**Architecture:** A Python 3.12 FastAPI service normalizes read-only HTTPS market data behind an injectable adapter interface. A React/TypeScript/Vite app polls the local API and renders candles with Lightweight Charts. No database, account access, AI call, order endpoint, or trading engine integration is included in this milestone; NautilusTrader is selected for the later PAPER engine phase.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic, HTTPX, pytest, React, TypeScript, Vite, Lightweight Charts, npm lockfile, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-31-multi-exchange-trader-design.md`

## Global Constraints

- Work only on `codex/initial-platform`; never commit to or push `main`.
- Never add exchange credentials, private endpoints, or real order transport.
- Never read, modify, close, hedge, or claim to protect the user's manual Bybit positions.
- Use only public HTTPS market data; the initial UI does not submit even simulated orders.
- Use REST polling in the first milestone; do not claim WebSocket support without proxy verification.
- Keep OKX disabled with zero upstream requests and an unavailable/unverified UI/API state until authoritative V5 routes, schemas, limits, mappings, timestamps and candle semantics are verified and recorded. The cloud draft includes `www.okx.com` but needs user save/publish; allowlisting alone never satisfies this verification gate. Do not implement or call guessed endpoints.
- Use fixtures in tests and CI. CI must not call exchange APIs or require exchange secrets.
- Use HTTPX adapters limited to the documented public read endpoints; do not add CCXT's broader authenticated/order surface to the first process.
- Select NautilusTrader as the one future execution/backtesting core, but qualify the version labeled Beta before Phase 3; do not install it for the public-only dashboard.
- Keep synthetic preview data visibly labeled as synthetic and keep it off by default.
- Preserve TLS and certificate validation. Respect provider limits and bounded request timeouts.
- Commit and push each reviewed milestone on the work branch; never force-push.

## Review Focus

- A malformed or non-finite exchange number must be rejected before it reaches a chart; pin this in adapter parsing tests.
- Spot and perpetual symbols differ by venue; pin exact instrument mapping in each adapter contract test.
- Exchange candles may arrive newest-first and may include a still-open candle; pin normalized ascending order and closed/open state in fixtures.
- A failed or stale provider must not be displayed as a current quote; pin fetch timestamps, error states, and UI stale labels.
- Sample preview data must never resemble a verified live quote; pin the opt-in flag and visible synthetic-data banner in a frontend test.

---

### Task 1: Research and policy documents

**Files:**
- Create: `docs/OPEN_SOURCE_AUDIT.md`
- Create: `docs/EXCHANGE_CAPABILITIES.md`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/WORK_LOG.md`
- Existing: `docs/AI_DESIGN.md`, `docs/SECURITY.md`, `docs/QA_PLAN.md`
- Existing: `docs/superpowers/specs/2026-10-31-multi-exchange-trader-design.md`

**Interfaces:**
- Consumes: the user directive and the cited research outputs.
- Produces: source-linked engine recommendation, exchange capability matrix with unknowns, application architecture, AI/security/QA policies, and timestamped work log.

- [ ] Review the three open-source audit files from the research agents; remove unsupported factual claims and ensure all requested projects appear.
- [ ] Define and document a transparent 0–5 role-fit rubric for each reviewed project group and cite evidence for every score; label scores as this project's decision aid rather than external measurements.
- [ ] Reconcile engine conclusions: select NautilusTrader for the full multi-venue event-driven product; record Hummingbot's no-key Binance paper example as a faster one-venue alternative, not a second core engine. Note that the inspected NautilusTrader v1.231.0 GitHub release title says “Beta” and require package/API qualification before Phase 3.
- [ ] Reconcile exchange-library conclusions: use narrow HTTPX public-read adapters in the initial dashboard to avoid importing private/order methods; record CCXT MIT as an alternative if exchange coverage later expands, and note that CCXT Pro WebSockets have separate terms.
- [ ] Align phase names with the user directive: Phase 1 audit, Phase 2 dashboard, Phase 3 PAPER, Phase 4 backtesting, Phase 5 AI, Phase 6 Demo, Phase 7 approval workflow, Phase 8 AUTO, Phase 9 QA/security, Phase 10 live readiness.
- [ ] Run `git diff --check` and review all links and unverified items.
- [ ] Commit and push the reviewed documents.

### Task 2: Backend data contracts and exchange adapters

**Files:**
- Create: `pyproject.toml`, `uv.lock`
- Create: `src/multi_exchange_trader/market_data/models.py`
- Create: `src/multi_exchange_trader/market_data/adapters/base.py`
- Create: `src/multi_exchange_trader/market_data/adapters/binance.py`
- Create: `src/multi_exchange_trader/market_data/adapters/bybit.py`
- Create: `src/multi_exchange_trader/market_data/adapters/okx.py`
- Test: `tests/market_data/test_binance.py`
- Test: `tests/market_data/test_bybit.py`
- Test: `tests/market_data/test_okx.py`
- Test: `tests/market_data/test_contract.py`

**Interfaces:**
- `MarketType = Literal["spot", "perpetual"]`.
- `Ticker`: `exchange`, `symbol`, `market_type`, `last_price`, `observed_at`, `fetched_at`, `source`.
- `Candle`: UTC `timestamp`, decimal-safe OHLCV values, and `is_closed`.
- `MarketAdapter.get_ticker(symbol: str, market_type: MarketType) -> Ticker` (async).
- `MarketAdapter.get_candles(symbol: str, market_type: MarketType, interval: str, limit: int) -> list[Candle]` (async).
- Each enabled adapter accepts an injected `httpx.AsyncClient`; it owns no credentials and cannot submit or query private account/order APIs.
- Until the official-source gate above is met, `okx.py` is a disabled adapter: both read methods return a typed unavailable/unverified failure before any transport call. No guessed routes or normalization code is needed for this state.

- [ ] Write fixture-based failing Binance/Bybit contract tests for BTCUSDT spot and perpetual mapping, numeric validation, timestamps, and candle ordering.
- [ ] Write fixture-based OKX disabled-state tests for ticker/candle reads, spot/perpetual and BTC/ETH selections. Inject a transport that fails on any call; assert a typed unavailable/unverified result and zero upstream requests.
- [ ] Before replacing the disabled OKX adapter, record authoritative V5 route/schema/limit/mapping/time/candle evidence and add corresponding source-backed fixtures. If verification remains unavailable, deliver the disabled state and keep parsing/enablement deferred.
- [ ] Run each new test and confirm the expected missing-adapter or parsing failure.
- [ ] Implement only the minimal Pydantic models, protocol, verified Binance/Bybit response normalizers and disabled OKX behavior needed to pass. Do not infer OKX routes from unofficial SDKs.
- [ ] Add malformed payload, negative/non-finite price, unsupported symbol/interval, and open-candle tests.
- [ ] Run `uv run pytest tests/market_data -q` and `uv run ruff check src tests`.
- [ ] Commit and push the reviewed adapter slice.

### Task 3: FastAPI market-data service

**Files:**
- Create: `src/multi_exchange_trader/market_data/service.py`
- Create: `src/multi_exchange_trader/api.py`
- Create: `src/multi_exchange_trader/main.py`
- Test: `tests/api/test_market_routes.py`
- Modify: `pyproject.toml`, `uv.lock`

**Interfaces:**
- `GET /api/v1/health` returns `{"status":"ok"}`.
- `GET /api/v1/market/ticker?exchange=<id>&symbol=<symbol>&market_type=<spot|perpetual>` returns one normalized `Ticker`.
- `GET /api/v1/market/candles?...&interval=1m&limit=200` returns normalized ascending `Candle[]`; validate `1 <= limit <= 500`.
- Exchange/client registry is injected into the service for fixture-backed route tests; external network is never a test requirement.

- [ ] Write route tests for success, invalid query input, unsupported venue, provider timeout, and malformed provider payload.
- [ ] Add fixture-backed OKX ticker/candle route tests that assert a typed unavailable/unverified API error and zero upstream calls while its verification gate is unmet; configured host access must not enable it.
- [ ] Run the tests and confirm the missing route/service behavior fails as expected.
- [ ] Implement the async service and route handlers with request timeouts, bounded response handling, and safe error mapping.
- [ ] Add an explicit no-order-route assertion to API tests or route inventory check.
- [ ] Run `uv run pytest -q`, `uv run ruff check src tests`, and `uv run mypy src`.
- [ ] Commit and push the reviewed API slice.

### Task 4: Korean dashboard and synthetic preview mode

**Files:**
- Create: `apps/web/package.json`, `apps/web/package-lock.json`, `apps/web/tsconfig.json`, `apps/web/vite.config.ts`
- Create: `apps/web/src/main.tsx`, `apps/web/src/App.tsx`, `apps/web/src/api.ts`, `apps/web/src/chart/CandleChart.tsx`, `apps/web/src/styles.css`
- Create: `apps/web/src/App.test.tsx`, `apps/web/vitest.config.ts`
- Modify: `.gitignore`

**Interfaces:**
- Vite proxies `/api` to `http://127.0.0.1:8000` in development.
- App requests the Task 3 ticker/candle routes for `BTCUSDT` and `ETHUSDT`.
- Poll ticker data no faster than every 5 seconds; refresh candles no faster than every 30 seconds; prevent overlapping requests and show last source time, loading, error, and stale status.
- Query parameter `preview=1` enables a deterministic synthetic fixture with a persistent visible `샘플 데이터 · 실제 시세 아님` banner. Preview is disabled otherwise.

- [ ] Add frontend tests first for the synthetic banner, default live-data state, and stale/error labels; run them to confirm expected failures.
- [ ] Add a fixture-backed UI test showing an explicit OKX unavailable/unverified state while the gate is unmet; selecting OKX must not initiate provider polling or show synthetic data as verified market data.
- [ ] Implement the responsive Korean dark dashboard with exchange/market selectors, BTC/ETH overview, candle chart, and clear `공개 시세 전용 · PAPER 엔진 미구현` safety label.
- [ ] Add Lightweight Charts with visible TradingView attribution; do not load CDN assets.
- [ ] Run `npm ci`, `npm test -- --run`, `npm run typecheck`, and `npm run build` from `apps/web`.
- [ ] Start the backend and frontend, call the local health endpoint, and open the requested preview tab using `?preview=1` so synthetic values are explicitly labeled.
- [ ] Commit and push the reviewed frontend slice.

### Task 5: CI and reproducible cloud setup

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`, `.gitignore`
- Cloud draft: `install_script`, `start_skill`, and custom network hosts `data-api.binance.vision`, `fapi.binance.com`, `api.bybit.com`, `www.okx.com`.

**Interfaces:**
- CI uses standard GitHub-hosted runners, locked Python/npm dependencies, local fixtures, and no exchange credentials.
- Install script runs `uv sync --locked` and `npm ci` from the documented directories.
- Start instructions include health checks and say to use the existing checkout; do not create or propose a worktree unless explicitly requested.
- No environment secrets or variables are required for the dashboard.

- [ ] Add CI jobs for Python tests/lint/typecheck and frontend tests/typecheck/build; include a secret scan using public-repository-safe configuration.
- [ ] Run every configured CI command locally from the same directories and fix setup defects before committing.
- [ ] Document install/start/preview commands and explain that real exchange connectivity requires the user to save/publish the network draft.
- [ ] Save the tested install/start instructions and only the required exchange hostnames in the environment draft; do not overwrite unknown custom hosts.
- [ ] Commit and push CI and README changes.

### Task 6: Final integration and report

**Files:** all files created in Tasks 1–5.

- [ ] Re-run the complete configured local CI command set and inspect `git status`, `git diff --check`, and the final branch history.
- [ ] Check no credentials, private endpoints, live-order routes, user account data, or unlabelled synthetic data entered the branch.
- [ ] Verify each required document exists and distinguish implemented, deferred, unverified, failed, skipped, and unrun items.
- [ ] Push the final work-branch commit and verify its remote SHA with a read-only Git command.
- [ ] Report current-runtime validation separately from draft persistence and future environment publication.

## Coverage gaps tracked for later phases

- PAPER order lifecycle, exact amount/contract conversion, idempotency, partial fills, fees, funding, leverage, margin, liquidation, restart recovery, and reconciliation.
- Historical and walk-forward backtesting, look-ahead checks, and strategy metrics.
- Deterministic rules and optional AI provider workflow with provenance and post-result evaluation.
- Authenticated Demo integrations and full exchange capability checks.
- Approval workflow, kill switch, risk limits, and AUTO mode.
- Production authentication, deployment, privacy/retention, and any live-trading readiness review.

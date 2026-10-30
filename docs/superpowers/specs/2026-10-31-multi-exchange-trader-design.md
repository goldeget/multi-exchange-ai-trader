# Multi-Exchange AI Trader — Initial Platform Design

## Intent

Build a Korean-first, local-development crypto research and trading platform with auditable data, deterministic safety controls, and no implied profitability. The user supplied the product scope and has delegated technology selection. The repository began with only a short README and a Python `.gitignore`; it contained no application code, dependency manifests, run commands, or CI.

This design covers the first delivery requested in the directive: public-project research, architecture and safety documentation, a runnable public-market-data dashboard for BTC and ETH, and CI for the implemented scope. Later PAPER execution, backtesting, AI, exchange Demo, approval workflows, and AUTO remain distinct phases.

## Goals

- Compare the requested public projects from official sources and select one future execution/backtesting engine.
- Provide a Korean, responsive dashboard with public spot and perpetual market data and candle charts for Binance, Bybit, and OKX.
- Run without exchange credentials. Keep every exchange order, private account endpoint, transfer, and live trading path absent from the first delivery.
- Show venue, instrument, market type, observation time, loading/error state, and stale data state. Clearly label any local UI sample as synthetic.
- Make setup reproducible from lockfiles and support standard GitHub Actions runners.
- Record the selected architecture, exchange capabilities, AI design, security policy, QA plan, and work log in the required documents.

## Non-goals for this delivery

- Implementing the PAPER order/accounting engine, historical backtest engine, AI/ML analysis, AI provider calls, authenticated exchange access, Demo accounts, approval queue, or AUTO mode.
- Reading or changing the user's accounts, balances, or existing manual Bybit positions.
- Claiming that any strategy, model, or simulation is profitable or production-ready.
- Enabling WebSocket transport before its support through the managed environment proxy is verified; the current environment has no TCP grants.

## Anti-goals

- Any API key entry or persistence, signed exchange request, private account polling, or order submission in the initial app.
- Any behavior where a UI mode, AI response, configuration edit, timeout, or retry can activate a live order route.
- Synthetic prices or returns presented as current market data or measured performance.
- Installing and running several competing trading engines in the same product.

## Constraints and user decisions

- Preserve the existing public repository and `main`; do all changes on `codex/initial-platform`.
- Do not create a new GitHub repository, force-push, or reset history. Commit reviewed milestones and push only this work branch.
- Existing Bybit manual positions are unmanaged and outside the app. Main-account private access is out of scope.
- No production exchange credentials, live orders, paid AI calls, paid hosting, or server purchases.
- Initial runtime is Python 3.12.14, Node 24.19.0, npm 11.9.0, and uv 0.12.23. GitHub package-manager and source hosts are available; exchange API hosts are not yet allowlisted.
- Any exchange endpoints in the implementation must be public, HTTPS, rate-limited, bounded by timeouts, and reviewed against authoritative documentation. The first UI may poll over REST. CI must use local fixtures and must not call production exchanges.

## Approaches considered

| Approach | Trade-off | Decision |
| --- | --- | --- |
| NautilusTrader as the single future execution/backtesting core, behind a custom API and UI | Best fit for a custom multi-venue event-driven system and local sandbox execution. It requires a custom Korean UI and careful version pinning; LGPL-3.0 obligations need review if the library is modified or redistributed. | **Selected for the full product.** Source review found v1.231.0 supports the available Python 3.12 runtime and has Binance, Bybit, and OKX Demo configuration plus a local sandbox client. The GitHub release title labels v1.231.0 “Beta,” so qualify the exact package/API before Phase 3. Do not add the engine to the dashboard dependency set before the PAPER task needs it. |
| CCXT plus a bespoke execution and backtest engine | Broad exchange coverage and convenient REST wrappers, but order state, portfolio risk, accounting, reconciliation, and backtest fidelity would still be custom work. WebSocket support through CCXT Pro has separate commercial terms. | Not selected as the core engine. The initial read-only adapters may call small public REST endpoints directly to keep data behavior explicit. |
| Embed Freqtrade or Hummingbot as the complete product | Mature strategy/execution workflows, but Freqtrade's GPL-3.0 license and bot-centered model or Hummingbot's market-making focus and service footprint do not fit the custom cross-exchange product as closely. | Not selected as the core. Keep both as research references and reconsider only if product scope changes. |

The one selected execution/backtesting engine is **NautilusTrader**. The inspected v1.231.0 package metadata supports Python `>=3.12,<3.15`, and its tagged adapter sources define Binance, Bybit, and OKX Demo configs plus a local sandbox execution client. However, the GitHub release title labels v1.231.0 “Beta”; treat that package as the current candidate version and complete package/license/runtime qualification before Phase 3. The current master v2.0.0rc8 API has different names and a higher Python floor; do not mix v2 examples into v1 code. This is a full-product engine selection, not a claim that the first dashboard uses or validates the engine.

## Architecture for the first delivery

- **Backend:** Python 3.12, FastAPI, Pydantic, and async HTTPX. Define a common read-only market-data adapter contract and separate venue response normalizers. Implement Binance and Bybit against verified sources; keep an OKX disabled adapter until its official contracts are verified. No private endpoints or exchange order interfaces exist in the first app.
- **Market data:** Use small, read-only HTTPX adapters over HTTPS REST polling. Binance spot uses `data-api.binance.vision`, Binance USD-M perpetual uses `fapi.binance.com`, Bybit uses `api.bybit.com`, and `www.okx.com` is only a candidate OKX host. All hosts require effective runtime allowlisting. The cloud draft includes the OKX host but requires user save/publish; a draft or published allowlist does not verify an endpoint contract. Binance/Bybit endpoint details are source-verified. **OKX must remain disabled, issue no upstream requests, and display an unavailable/unverified state until authoritative OKX V5 documentation verifies the exact public routes, schemas, limits, instrument mappings, timestamps and candle semantics.** Do not implement or call guessed OKX endpoints. Record official-source evidence and complete fixture-based acceptance before enabling OKX. This deliberately avoids importing a broad exchange client with private/order methods into the initial public-only process; the trade-off is more endpoint normalization and maintenance than CCXT. If a provider is unavailable or a response is invalid, show an error/stale state rather than silently substituting a different venue.
- **Frontend:** TypeScript, React, Vite, and TradingView Lightweight Charts 5.2.1. Keep chart data in the browser, use the library's required TradingView attribution, and include no runtime CDN dependency.
- **Persistence:** none in the first dashboard. It holds no accounts, keys, positions, orders, or AI results. Any sample data used to preview the interface must be clearly identified as synthetic and must not look like current quotes.
- **Run model:** one API process and one frontend dev/build process. Local REST calls are proxied by Vite in development. No database, queue, container, background worker, or paid hosted service is required for this scope.
- **Future engine boundary:** when PAPER execution begins, qualify and integrate the pinned NautilusTrader v1 API Sandbox client behind an application service. Keep strategy/AI suggestions separate from deterministic mode, risk, accounting, and execution controls. All exchange adapters must pass acceptance checks before a venue capability is enabled.

## Safety and data handling

- First release has no order endpoint and no authenticated exchange client. Display a persistent Korean `PAPER · 공개 시세 전용` or equivalent safety label, while making clear that the dashboard itself does not place simulated orders yet.
- Public data needs a visible source timestamp and data age. Reject malformed, non-finite, negative, unsupported, or out-of-order records. Respect exchange rate-limit responses and use bounded retry/backoff.
- Do not show account or portfolio values, returns, win rate, or backtest results because no such data exists.
- Keep credentials out of source, environment draft, logs, test fixtures, and browser code. No exchange secrets are needed or requested.
- AI remains a future advisory-only component. It receives bounded public snapshots, cannot access secrets or execution tools, and cannot change risk policy or mode.

## Verification and delivery

- Unit and adapter-contract checks use deterministic fixture payloads; no CI request reaches an exchange. For unverified OKX, stub transports must fail on any attempted upstream request; ticker/candle adapter and API checks must assert a typed unavailable/unverified response with zero transport calls, and UI checks must assert a visible unavailable/unverified state. Verified OKX parsing tests may be added only after authoritative contracts are recorded.
- Frontend verification includes typecheck and production build; backend includes tests and lint/type checks supported by selected dependencies.
- Standard GitHub Actions runners only. Add secret scanning and inspect dependency and chart-library licenses. Record CI state truthfully.
- A manual live public-data request can be reported only after the cloud allowlist draft is applied and the provider responds. Draft persistence alone does not prove current runtime access.
- Commit the reviewed documents and code to `codex/initial-platform`, push that branch, and report the final commit SHA. Do not push to `main` or create a PR unless requested.

## Deferred decisions

- PAPER fill model, account ledger schema, persistence/restart semantics, order idempotency, per-venue risk limits, and whether spot/perpetual paper positions share one portfolio.
- Exact REST polling intervals and per-symbol universe after rate-limit and UI measurements; defaults should be conservative and visible.
- WebSocket transport after testing the actual managed proxy/TCP capabilities.
- AI provider, model license, external-data terms, user approval flow, and cost cap; no provider is selected or enabled now.
- Exchange Demo adapter acceptance and regional eligibility checks; source-level Demo configuration is not runtime validation.
- Deployment, authentication, and multi-user storage. These are outside the local first delivery.

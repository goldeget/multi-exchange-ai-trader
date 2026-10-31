# Security requirements

This document records the intended security boundaries and acceptance criteria for the public market-data dashboard and later phases. Requirements below are not evidence that controls have been implemented or tested. This file is project documentation; it is not a repository-wide scanner policy.

## Current scope: public data and a read-only Bybit account view

The public market pages display public crypto market information. They do not submit orders.

A separate read-only Bybit linear (USDT perpetual) view may read `BYBIT_API_KEY` and `BYBIT_API_SECRET` only from the process environment or the allowlisted keys in a `.env` file. It must not accept keys from the browser, a query string, a request body, logs, or git. It may call only an allowlist of GET endpoints: server time, instrument leverage, wallet balance, positions, and open orders. It must not place orders, set leverage, or change positions. `LIVE_TRADING_ENABLED` defaults to false. Order and leverage endpoints stay refused even if that flag is true, because this client is not an order sender. Future orders belong to one NautilusTrader execution path, which this view does not start. Nautilus Bybit execution can change leverage on connect and can submit orders, so this view does not open that client.

Existing manual positions and open orders are labeled external and are not adopted or modified. If Bybit denies the network location, the view shows BLOCKED and no invented balances. `MARKET_DATA_BLOCKED_HOSTS` stays in place for the public market worker. The read-only account client is separate and does not change VPN or proxy settings.

The user's existing manual Bybit positions remain external and unmanaged: the program must not hedge them, close them, or imply that its risk controls protect them. PAPER labels must stay distinct from this mainnet or demo read.

## Threat model and trust boundaries

- Public exchange responses, symbols, timestamps, prices, URLs, imported files, and browser input are untrusted. Validate them before storage, rendering, calculations, or downstream requests.
- Data-provider responses cross a network boundary. A compromised provider, connection failure, stale feed, or malformed payload must not produce executable content or silently credible trading signals.
- The browser is untrusted. Future server-side validation and execution restrictions must hold even when requests bypass the interface.
- AI output and retrieved text are untrusted suggestions. They cannot authorize actions, override policy, or supply executable instructions.
- Dependencies, build scripts, CI configuration, and deployment configuration can introduce execution or credential exposure. Review changes to these boundaries explicitly.
- Protect application integrity, user privacy, the accuracy of displayed data, future simulated account records, and audit history. The read-only Bybit view may display balances and positions on the local machine. It must not move funds, submit orders, or change leverage.

## Source and market-data validation

- Use documented, explicitly allowlisted public HTTPS endpoints. Do not allow user input, redirects, or AI output to select arbitrary server-side destinations or private network addresses.
- Bound response sizes, request duration, retries, concurrency, cache growth, and rate of requests. Respect provider limits and fail visibly when a source is unavailable.
- Validate schema, allowed symbols, finite numeric values, price/size ranges, timestamps, freshness, and ordering. Preserve source and observation time with derived values.
- Identify stale, delayed, historical, and synthetic data. Missing data must not silently become zero, an executable price, or a fresh observation.
- Render source text as data, with safe escaping. Do not render provider HTML or execute strings from providers, imports, or AI responses.

## Future PAPER orders

PAPER execution requires its own approved implementation phase. It must remain structurally unable to reach a real exchange order endpoint.

- Enforce simulation at the execution boundary. A mode flag, environment variable, browser request, malformed configuration, or model response must not enable live transport.
- Display PAPER/SIMULATED beside balances, positions, orders, and performance. Keep any manually entered real-position references distinct from simulated holdings and identify them as unmanaged and potentially stale.
- Validate symbols, sizes, precision, finite values, simulated balances, position limits, and allowed order transitions. Reject invalid or stale market data before accepting or filling orders.
- Define reproducible fill rules and disclose assumptions for fees, spread, slippage, partial fills, and supported order types. Never present unsupported execution behavior as modeled.
- Prevent duplicate execution with idempotent requests and consistent accounting. Record proposals, validation decisions, rejections, accepted orders, fills, cancellations, and balance changes.
- Audit events must include correlation identifiers, timestamps, reasons, market-data references, and strategy/model versions where applicable. Protect audit writes and make records append-only through application controls. Do not claim tamper-proof records without supporting controls.

## Future AI and performance analysis

- AI may advise only within an approved phase. Validate structured outputs against a bounded schema and configured risk limits. AI must not invoke arbitrary tools, run shell commands, read secrets, change limits, or execute orders.
- Keep external AI integration disabled until approved. Approval must cover providers, transmitted data, credentials, retention expectations, and costs. Never send exchange credentials or private account information to a provider.
- Apply timeouts, request limits, error handling, and cost bounds. Provider failure must not weaken execution or validation rules.
- Label backtests as historical simulations. Record dataset provenance, date range, market universe, strategy/model version, fees, slippage, missing-data treatment, benchmarks, and drawdown.
- Separate training, tuning, and evaluation data in time; address look-ahead, survivorship, and train/test leakage. Report out-of-sample methodology and limitations with results.
- Do not claim guaranteed returns, validated predictive advantage, live readiness, or protection of real positions. ML and profitability claims require reproducible evidence appropriate to the claim; a successful backtest alone does not establish future performance.

## Logs, secrets, and CI

- Read Bybit credentials only inside the read-only account client, from the environment or the allowlisted `.env` keys. Do not log them, return them, commit them, or put them in browser bundles, fixtures, screenshots, or documentation. Redact them from errors. Future separately approved AI credentials must remain server-side and outside source control.
- Redact credentials and sensitive values from logs, errors, telemetry, and generated artifacts. Avoid logging complete prompts or responses that may contain private data. Set bounded retention and access appropriate to the actual deployment before enabling persistent logging.
- Never place secrets in browser bundles, public environment variables, fixtures, screenshots, or documentation. If a secret is exposed, remove access to it and rotate it; deleting the visible copy alone is insufficient.
- CI must use locked dependencies, build/type checks appropriate to the stack, dependency and secret checks, and public or synthetic fixtures. Production secrets and exchange credentials must not be available to CI or untrusted pull-request jobs.
- Before future PAPER acceptance, CI must check that order validation, accounting, idempotency, audit creation, and simulation-only execution hold. Treat dependency/build-script changes and CI permission changes as reviewable security boundaries.

## Disabled pending separate approval

- Browser entry or storage of exchange API keys, and any private endpoint that is not on the read-only GET allowlist.
- Live orders, demo orders, autonomous trading, scheduled execution, or automatic management of the user's manual Bybit positions.
- Transfers, deposits, withdrawals, custody, borrowing, leverage changes, or liquidation-management actions.
- Starting the Nautilus Bybit execution client, or a second order sender.
- PAPER order functionality, external AI calls, and model training beyond the approved current dashboard scope.
- AI authority to execute orders, change execution policy or risk limits, access secrets, or run arbitrary commands.
- Sending private positions, account data, credentials, or proprietary datasets to third parties.
- Public deployment, paid infrastructure/services, and outbound notifications unless separately included in the user's authorization.

Approval to investigate or design a future phase does not enable its runtime capabilities. Live trading requires an explicit future scope and a new review of its account, authorization, execution, and recovery boundaries.

## Acceptance criteria and review guidance

For the current dashboard, acceptance requires evidence that all requests use approved public data sources, inputs are validated and safely rendered, stale/error states are visible, and no credential or order pathway exists. Verify that the user's manual positions remain unmanaged and that the interface makes no contrary claims.

For a later PAPER phase, acceptance additionally requires evidence that no UI action, configuration change, malformed request, imported data, or AI response can submit a real exchange order. Validate accounting, duplicate-request handling, invalid/stale input rejection, and complete audit events using isolated public or synthetic fixtures.

Report reachable violations of these boundaries, including private endpoint access, live-order capability, secret exposure, arbitrary destination requests, unsafe rendering, AI privilege escalation, corrupted simulated accounting, missing audit events, and materially misleading performance or position-management claims. Assess severity from demonstrated reachability and impact; absence of a live account does not make an exposed secret or arbitrary-code path harmless.

This document creates no blanket exclusions or accepted risks. Deployment exposure, authentication, retention periods, and future integrations require concrete decisions when those phases are scoped. Disabled future capabilities must remain disabled until authorized and reviewed.

## Related delivery documents

See the [architecture and phase map](ARCHITECTURE.md#phase-map-and-gates), [research audit](OPEN_SOURCE_AUDIT.md), [exchange capabilities](EXCHANGE_CAPABILITIES.md), [AI design](AI_DESIGN.md), [design specification](superpowers/specs/2026-10-31-multi-exchange-trader-design.md), [implementation plan](superpowers/plans/2026-10-31-public-market-dashboard.md), and [work log](WORK_LOG.md). Current delivery is Phase 1 research/docs and Phase 2 public dashboard. Phase 3 PAPER, Phase 4 backtesting, Phase 5 AI, Phase 6 Demo, Phase 7 approval, Phase 8 AUTO, Phase 9 QA/security expansion and Phase 10 live readiness are future phases; none is enabled by this document. QA and security checks for the public dashboard begin in Phase 2 rather than waiting for Phase 9.

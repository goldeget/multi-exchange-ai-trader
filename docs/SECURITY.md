# Security requirements

This document records the intended security boundaries and acceptance criteria for the public market-data dashboard and later phases. Requirements below are not evidence that controls have been implemented or tested. This file is project documentation; it is not a repository-wide scanner policy.

## Current scope: public-data dashboard

The current slice displays public crypto market information. It must not access an exchange account, hold funds, or submit orders. The user's existing manual Bybit positions remain external and unmanaged: the dashboard must not discover them through authenticated access, change them, hedge them, close them, or imply that its risk controls protect them.

No exchange API-key entry, credential storage, authenticated exchange client, private endpoint, or live order transport is permitted. PAPER features described below are future requirements, not authorization to implement them in this slice. Labels and documentation must accurately distinguish public observations, simulations, and real account information.

## Threat model and trust boundaries

- Public exchange responses, symbols, timestamps, prices, URLs, imported files, and browser input are untrusted. Validate them before storage, rendering, calculations, or downstream requests.
- Data-provider responses cross a network boundary. A compromised provider, connection failure, stale feed, or malformed payload must not produce executable content or silently credible trading signals.
- The browser is untrusted. Future server-side validation and execution restrictions must hold even when requests bypass the interface.
- AI output and retrieved text are untrusted suggestions. They cannot authorize actions, override policy, or supply executable instructions.
- Dependencies, build scripts, CI configuration, and deployment configuration can introduce execution or credential exposure. Review changes to these boundaries explicitly.
- Protect application integrity, user privacy, the accuracy of displayed data, future simulated account records, and audit history. Real exchange accounts and funds must remain unreachable from this application.

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

- Do not request, read, or accept exchange credentials, including credentials already present in a developer's environment. Commit only placeholder configuration. Future separately approved AI credentials must remain server-side and outside source control.
- Redact credentials and sensitive values from logs, errors, telemetry, and generated artifacts. Avoid logging complete prompts or responses that may contain private data. Set bounded retention and access appropriate to the actual deployment before enabling persistent logging.
- Never place secrets in browser bundles, public environment variables, fixtures, screenshots, or documentation. If a secret is exposed, remove access to it and rotate it; deleting the visible copy alone is insufficient.
- CI must use locked dependencies, build/type checks appropriate to the stack, dependency and secret checks, and public or synthetic fixtures. Production secrets and exchange credentials must not be available to CI or untrusted pull-request jobs.
- Before future PAPER acceptance, CI must check that order validation, accounting, idempotency, audit creation, and simulation-only execution hold. Treat dependency/build-script changes and CI permission changes as reviewable security boundaries.

## Disabled pending separate approval

- Exchange authentication, exchange API-key entry or storage, private endpoints, and testnet account integration.
- Live orders, autonomous trading, scheduled execution, or automatic management of the user's manual Bybit positions.
- Transfers, deposits, withdrawals, custody, borrowing, leverage changes, or liquidation-management actions.
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

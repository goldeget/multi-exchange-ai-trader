# Native contract qualification

Qualified engine: NautilusTrader1.231.0, unmodified wheel; Linux x86_64 isolated worker with socket denial. Windows execution remains unavailable. Supported contracts and runtime availability are separate capability fields.

Evidence: [CI50](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41267872828), immutable backend source 1e3ba76422b65fa5920ca1fb7423c6ed97379572. All four jobs passed: Python, frontend, secret scan, dependency review. Later UI changes require their own final checkpoint.

- 492 Python tests and 28 subtests passed in380.52s. Ruff passed for src/tests/scripts; mypy passed for62 source files. The earlier source-boundary failure from CI49 was corrected by removing the network-library dependency from the recommendation domain, while retaining uncertain-result handling. No guard was weakened.
- SPOT-PAPER-v2: native CASH balances, fee currency, partial sale, oversell refusal, cash/inventory reservations, resting buy/sell limits, crossed fills within the limit, cancellation, restart, distinct metadata, review/confirm, replay after expiry/dequalification, direct placement bypass refusal.
- Spot cost policy: one native tick from midpoint to each side; no additional FillModel slippage. This preserves adverse market execution without pushing limit fills outside the requested bound. Full top-of-book liquidity is a synthetic assumption; no partial fills or real exchange execution claim.
- Automation V2: configured leverage reaches native position/margin, entry pause continues market/risk processing, restart requires recovery, explicit resume and reduce-only stop, V1 controls/default hashes preserved.
- Evaluation V2: actual native configured fee/slippage/leverage/capital, requested range and split boundaries, holdout isolation, persistent configuration hash, restart/idempotency, zero-cost comparison.

No real account, DEMO account, paid AI, or external order service was called by qualification. The worker cannot open network sockets. Runtime prerequisite checks are not a guarantee that every host can install the sandbox; worker failures remain fail-closed.

## Combined-source repeat qualification

[CI run 41272013569](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41272013569), source `feda6c25ab6ec52f6a56715a585359d06b4ed14a`, passed all five jobs. Python: 492 tests and 28 subtests in 307.76s; Ruff passed; mypy passed for 63 files. Frontend: 120 unit tests, ESLint, typecheck and build passed. Browser: 90 passed, 24 intentional desktop duplicates skipped; capture-only review evidence, not yet strict screenshot acceptance. Secret scan and dependency review passed. Subsequent offline wording corrections and reviewed baseline installation require the final source CI.

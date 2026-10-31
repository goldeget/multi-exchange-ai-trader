# Advisory analysis design

Status: future Phase 5 advisory architecture following the Phase 2 public-market-data dashboard and Phase 3–4 PAPER/backtesting work. This document specifies intended behavior; it does not claim an implemented or validated trading system. No model or machine learning efficacy has been demonstrated.

## Phase 5 starting point: free deterministic analysis

Begin with versioned, deterministic rules over normalized public market data. Given the same input snapshot and rule version, analysis must return the same result. Use explicitly configured calculations such as moving-average trend, recent volatility, spread, and volume conditions. Each rule must identify its required history, calculation, threshold, and reason for passing or abstaining. Thresholds are hypotheses to evaluate, not evidence of profitability.

The future deterministic baseline requires no language model, paid account, model credentials, or external inference calls. Phase 2 displays public data only; even this analysis baseline is not implemented or enabled in that dashboard. Missing history, stale observations, crossed or invalid quotes, inconsistent instrument metadata, or an unavailable venue must produce an explicit abstention. Report data quality and assumptions alongside any directional hypothesis.

Public data collection, simulation, portfolio accounting, and order execution belong to deterministic services. The analysis component consumes immutable snapshots and produces advisory records. It cannot submit, amend, cancel, or size an order in the execution engine. A separate deterministic strategy may use an advisory record as one input under a documented policy, but every proposed action still passes independent risk and mode checks.

## Optional provider interface

Keep one analysis interface with these conceptual operations:

- Describe provider capabilities, identifier, version, and whether it requires network access or can incur cost.
- Analyze a bounded public-data snapshot under a versioned analysis policy.
- Return a validated advisory result or a typed abstention/failure.

Start with a rules provider. Future adapters may target an OpenAI-compatible endpoint or a local model. OpenAI compatibility describes an API shape; it does not establish a provider's privacy, price, availability, or model quality. A local endpoint may still need network access, and a remote endpoint may incur costs even when its API is compatible.

Model providers are disabled by default. Do not make paid inference calls until the user explicitly approves the provider, model, intended use, and spend limits. Record that approval and check it before each paid request. Merely configuring an endpoint or possessing credentials does not authorize paid calls. Explicitly opt in before sending even public inputs to a remote inference service. Downloading or running a local model is separate future setup work; review its resource needs and license first.

Use schema validation, request deadlines, bounded input/output sizes, and a bounded concurrency policy. A failed, refused, malformed, timed-out, or unavailable model response produces an abstention. A deterministic baseline can remain available with its own clearly labeled provenance. Do not silently substitute providers or models.

## Authority and data boundaries

The advisory worker receives only the public data and derived features necessary for its analysis. It has no exchange API secrets, account credentials, signing capability, private wallet material, or access to secret stores. If an optional remote provider needs an inference credential, keep that credential in the provider transport boundary; never place it in prompts, advisory records, or logs, and never expose exchange credentials there.

The worker has no execution tools or privileged application endpoints. Provider text cannot become executable code, shell commands, configuration edits, or tool instructions. Treat model outputs and any external text as untrusted data.

Risk settings are owned by a separate deterministic service. Analysis cannot change position or leverage limits, per-order or aggregate exposure, loss/drawdown thresholds, kill switches, allowed symbols, allowed venues, execution mode, or approval requirements. It cannot authorize LIVE trading. PAPER, exchange Demo, and LIVE are distinct modes; an advisory result cannot cause a transition between them.

The risk service must independently reject stale or duplicate proposals, unavailable market data, prohibited modes, invalid quantities/prices, and breached limits. The execution engine remains the source of truth for order state and reconciliation. An advisory timeout or model recommendation never justifies retrying an order with an ambiguous venue outcome.

## Advisory record and provenance

Persist an append-only record for each analysis attempt, including abstentions. Corrections and later evaluations reference the original record rather than overwriting it.

| Field | Required content |
| --- | --- |
| Identity | Unique analysis ID, request/correlation ID, schema version, creation timestamp, and execution mode context. |
| Inputs | Venue, instrument, market type, timeframe, snapshot/window identifiers, public source references, input hash, and derived-feature values. |
| Data timestamp | Source event timestamps, collection timestamp, analysis cutoff, observed age, and data-quality/gap flags. Use UTC. |
| Provider/version | Ruleset and configuration version; for models, provider ID, exact model identifier, exposed model version/revision, prompt version, and relevant generation settings. Mark an undisclosed model revision as unknown. |
| Evidence | Machine-readable rule outcomes or source-linked observations supporting the hypothesis, with relevant values, units, and calculation versions. Distinguish observed facts from interpretation. |
| Recommendation | Advisory direction or abstention, rationale, intended horizon, and uncertainty. Do not present an uncalibrated confidence score as a probability of profit. |
| Entry | Proposed entry condition or price range, relevant units, validity period, and assumptions. This is a hypothesis, not an order or permission to trade. |
| Invalidation | Explicit price, time, or evidence condition that would invalidate the hypothesis. Advisory invalidation is separate from an enforced stop or risk limit. |
| Processing outcome | Success, abstention, timeout, validation failure, or provider failure, with a safe error classification; optional remote usage/cost metadata. |
| Post-result | Linked later observations and evaluation timestamp, whether entry/invalidation conditions occurred, evaluation horizon and method, and any simulated fills, fees, slippage, and resulting paper PnL. Mark unresolved outcomes explicitly. |

Retain enough permitted input data or immutable references to replay deterministic analysis. A model response may not be reproducible even with matching settings; preserve the actual validated response and input/prompt hashes. Evidence means inspectable market observations and calculations, not hidden chain-of-thought. Exclude credentials and personal account data from stored payloads.

Post-result evaluation must use only observations available after the original analysis cutoff and a prespecified evaluation method. Preserve losing and abstained examples. Keep advisory forecast outcomes separate from execution outcomes: an accurate directional call can still lose money after costs, timing, and fills.

## Evaluation and future work

Model and ML efficacy is unvalidated. Provider integration, model selection, training, fine-tuning, feature optimization, confidence calibration, and autonomous strategy generation are future work, not MVP capabilities or expected profit sources.

Before treating a model as useful, compare it against the frozen deterministic baseline using chronological holdouts and forward PAPER observations. Record fees, spread, latency, slippage, failed data fetches, abstentions, and selection effects. Prevent future-data leakage; separate development from evaluation periods; document model/prompt changes and repeated trials. Evaluate incremental value, stability, cost, and operational reliability rather than treating fluent explanations or isolated profitable examples as evidence.

Any later proposal to expand model authority, process private data, incur new costs, or enable LIVE execution requires a separate explicit decision. This design grants none of those permissions.

## Related delivery documents

The [architecture and phase map](ARCHITECTURE.md#phase-map-and-gates), [research audit](OPEN_SOURCE_AUDIT.md), [exchange capabilities](EXCHANGE_CAPABILITIES.md), [security requirements](SECURITY.md), [QA plan](QA_PLAN.md), [design](superpowers/specs/2026-10-31-multi-exchange-trader-design.md), [implementation plan](superpowers/plans/2026-10-31-public-market-dashboard.md), and [work log](WORK_LOG.md) define the current public-only scope. Phase 6 Demo, Phase 7 approval, Phase 8 AUTO, Phase 9 QA/security expansion and Phase 10 live readiness remain future work. No phase is enabled by this policy.

# Native execution and provider contracts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for independent implementation and review.

**Goal:** Replace guessed native integration assumptions with pinned-wheel command/event tests, preserve known AI liabilities, and add bounded metadata verification without generation or trading activation.

**Architecture:** Existing owned execution primitives remain independent from a new asynchronous native command/event bridge. Existing GET-only account reads may supply raw risk/protection readback later; all order/configuration writes remain Nautilus-only. AI metadata GET and budget accounting are separate from future paid Messages dispatch.

**Tech Stack:** Python3.12, pinned NautilusTrader1.231.0, existing httpx/SQLite/pytest, Linux OS network namespace for real-wheel tests; no new dependencies or vendor fork.

**Spec:** `docs/ROADMAP_FINAL_PRODUCT.md` D4/D5/D6 and4.1.1, `docs/REFERENCE_ARCHITECTURE.md`, `docs/implementation/AUTOMATIC_EXECUTION_CORE_KO.md`, `docs/implementation/AUTOMATIC_MODES_KO.md`. User approved autonomous parallel work, reviewed main merge and automatic-mode distinction. A copied external credential file permits bounded connection checks; no actual order or paid generation is part of this patch.

## Global constraints

- Workspace native-provider-integration, branch gpt/native-provider-20261102, base main9ec68158153b830e1b2413ef31db643f52dfd4a7. Agents edit only assigned files, never stage/commit. Root integrates.
- Actual credentials live outside the repo. Implementers/reviewers must not read them or inherited secret values. Root alone handles live metadata probes; none are part of tests or CI.
- No real orders, native node connect/run, leverage/margin/position changes, paid Messages calls, new servers, network bypass, or public API/UI activation. Existing LIVE=false/analysis OFFLINE_TEST remains.
- All actual write operations must eventually use Nautilus. GET-only account data via the existing separate read client is allowed by the reference architecture and is not a second order path. Do not infer that a Rust/PyO3 fork is necessary just to preserve raw read-only data.
- Native submit returns before acceptance; no synchronous fake ACK. General native position reports omit leverage/TP/SL and are not sufficient evidence of applied leverage, protection or complete flat. Empty report after an error cannot prove no position.
- Known higher provider charges must never disappear because they exceeded a reservation or limit. Existing caller's maximum is not authorization to spend more; record liability and block new paid dispatch. No default budget increase.
- API keys, provider bodies, exceptions, headers, account values and credential paths never enter public logs/reports/commits. Safe fixed codes only. No SDK defaults/retries guessed from older docs.
- Use finite bounded Decimal and UTC instants; avoid ambient-precision undercounting. Money reservations/observed liabilities round upward where unit conversion is needed, never downward.

## Review focus

1. Pinned native factory/Strategy parameter placement differs from published examples: exercise actual installed wheel, not an import-only or fake adapter assertion.
2. Submission/acceptance/fill/cancel are asynchronous; timeout and empty reports cannot be converted to terminal or protection-ready state.
3. Actual liability exceeds reserved/max100: preserve it across restart and hold new spending, including same-ID replays and other periods.
4. Metadata HTTP errors/oversized/duplicate/secret-bearing fields must not leak key text or silently permit generation.
5. Python socket monkeypatch does not block Rust transport: Linux native qualification must prove an OS network boundary and fail if required tests are skipped.

### Task 1: Real native command and event bridge

Own: `execution/bybit_native.py`, `execution/native_events.py`, `tests/execution/test_bybit_native.py`, `tests/execution/test_bybit_native_linux.py`, `docs/implementation/BYBIT_NATIVE_BRIDGE_KO.md`.

- [ ] Implement narrow explicit DEMO/LINEAR/one-way command building using actual locked native OrderFactory and `Strategy.submit_order(order, params=...)` behind lazy imports. No environment fallback credentials or actual connect/run. Account settings futures_leverages/margin_mode/position_mode=None initially; changes need a future journaled sequence/readback.
- [ ] Preserve exact durable client ID (existing35chars) and instrument/side/quantity/price. Validate stop/take-profit and trigger source; only tested Market+Full native attached semantics. Reject forbidden DEMO custom trigger params, wrong position index, reduce-only exit plus attached TP/SL.
- [ ] Bridge is asynchronous: submission invocation produces SUBMITTING, real native events/reports produce distinct submitted/accepted/rejected/filled/canceled evidence; mapping exact clientID/environment/account correlation required. No prefix ownership and no invented authenticated UID from BYBIT-UNIFIED label. Preserve fills/execution IDs/commission currency and receive/event timestamps distinctly. Unknown/error/partial report remains incomplete.
- [ ] Reuse pure owned projection types where appropriate but do not plug this into OfflineExecutionCoordinator or mark production supported. Define concrete typed interfaces and report them early. No broad cancel/flatten and no HTTPX order calls.
- [ ] Portable tests use explicit fakes only. Linux tests import exact real wheel, actual native order/config/event objects and prove actual Strategy command params/native TP/SL forwarding at injected fake transport/event boundaries. No production credentials and no actual node connect. No native-only skip when REQUIRE_NATIVE_CONTRACTS=1; Windows portable tests can skip native case explicitly.
- [ ] Tests require OS namespace under Linux CI; controller adds job. Verify native version and actual config acceptance/retry types, rather than assuming max_retries=0 works. No generated reports are full position/leverage/protection qualification.
- [ ] Meaningful RED->GREEN, focused portable tests/Ruff/mypy; Linux proof via exact-head CI. Document actual gaps and raw read-only readback requirement. No false claim of real DEMO fill or SL/TP qualification.

### Task 2: Preserve observed AI overrun liabilities

Own: `ai/budget.py`, `tests/ai/test_budget_liability.py`, only the outdated overrun assertion in `tests/test_control_contracts.py`, `docs/implementation/AI_BUDGET_LIABILITY_KO.md`.

- [ ] RED tests: reserve0.80 limit1.00, known charge0.95 or1.20 persisted; restart keeps full actual exposure; no new reservation/dispatch while overrun unreconciled; identical settlement idempotent and changed settlement conflicts; actual charge>100 accepted as liability without increasing configurable spending cap; unknown retains maximum; precision rounding conservative; SQLite failure no false successful settlement.
- [ ] Separate configurable limit parsing (existing<=100 unchanged) from observed actual cost parsing. Accept bounded finite nonnegative numeric evidence without applying user budget cap to liability. Avoid arbitrary rounding down or default zero; document supported representation and explicit upward unit rounding.
- [ ] Persist known actual even if above reservation/limit and expose used/remaining/overrun hold truthfully. New paid reservations must fail closed after any overrun until a future explicit verified reconciliation path; do not add automatic unblock or relax existing settings cap. No generation transport or dispatch idempotency claim from reserve() replay.
- [ ] Existing DB schema/storage data preserved; migration, if any, is additive/transactional and tested. Current API mode/llm flags unchanged. Update old test that expects higher liability to be discarded; do not weaken other contracts. Full API/budget-focused compatibility checks and static checks.

### Task 3: Anthropic metadata client only

Own: `ai/anthropic_metadata.py`, `tests/ai/test_anthropic_metadata.py`, `docs/implementation/ANTHROPIC_METADATA_KO.md`.

- [ ] Check current official primary Models retrieve/versioning docs. Public docs currently list claude-haiku-5-5 and API version2026-10-21; account access remains unverified. Pin and test exact documented authentication/header/schema contract. Do not blindly copy old2023/x-api-key snippets or infer price/balance.
- [ ] Narrow client explicitly receives key/model and only performs one bounded GET https://api.anthropic.com/v1/models/{validated_model_id}; no environment/file loader, POST, Messages, redirects, retries, automatic startup/status-page network call or provider fallback. Existing httpx with injectable transport and finite timeout/body bound.
- [ ] Return safe frozen metadata/result types: requested/resolved IDs, display/name/date and relevant documented fields only; status/verified_at, generation_enabled=false, provider_balance=null. Validate exact required types, integer-vs-bool, duplicate JSON keys, malformed/oversized/secret-bearing values. Unknown optional future fields bounded and not returned wholesale.
- [ ] Auth401/403/404/429/5xx/timeouts/TLS/errors become fixed safe failure enums; never raw response, exception, headers, credential paths or key in repr/result. Source key not retained in public config/result objects.
- [ ] RED->GREEN mocked tests verify oneGET/noPOST/noautomaticretry/redirect, exact schema/header/alias handling and all safety boundaries. No real authenticated call by worker. Metadata is not generation permission, provider balance/pricing proof, trading permission or production analysis activation.

### Task 4: Root integration, CI and review

- [ ] Add dedicated Ubuntu job for real native contract tests under OS network namespace, exact dependencies, no secrets. Preflight unshare, then run .venv Python directly; require native tests and exact version, fail on mandatory skip. Existing native PAPER isolation stays intact.
- [ ] Combine portable backend/AI/native tests, Ruff and mypy. Review each component, then broad immutable patch; fix actual findings and re-review before exact-head CI merge. Root never treats mock/native no-network pass as actual DEMO or LIVE qualification.
- [ ] No actual keys/Bybit connection results committed. Root connection evidence retained outside repo; user informed live/demo authentication succeeded, not order permissions. Anthropic credential location question pending independently.

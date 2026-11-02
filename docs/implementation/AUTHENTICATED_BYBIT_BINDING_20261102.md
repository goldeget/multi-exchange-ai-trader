# Authenticated Bybit DEMO account binding

`execution/account_binding.py` now owns a single explicit credential pair, the
existing GET-only client, and construction of the pinned native execution config.
It does not load environment files, start a worker, connect a native client, or
submit any venue mutation.

## Integration contract

1. Construct `BybitDemoAccountSession` from explicitly supplied DEMO credentials.
2. Await `authenticate(expected_uid=..., expected_parent_uid=...,
   expected_is_master=...)`. Attest all three fields independently against trusted
   expected account metadata: the actual positive UID, exact parent UID string
   (including `"0"`), and an explicit boolean master flag. DEMO may return
   `isMaster=False` with `parentUid="0"`; neither field determines the other.
   Do not use the response being verified as its own expectation. A native adapter
   label is never accepted as the UID. Existing callers must supply the new
   required `expected_is_master` keyword; it has no inferred/default value.
3. Keep the returned opaque handle with its issuing session. The handle cannot
   be reconstructed from a public identity, serialized state, or another session.
4. `require_binding(handle, scope=...)` validates exact DEMO/UID scope, unchanged
   credentials/host, server timestamp, wall-clock age, and monotonic age. Default
   TTL is 30 seconds, with a maximum configurable TTL of five minutes. Every
   refresh attempt and close revokes the previous handle, including failed refresh.
5. `read_client(handle, scope=...)` returns the same authenticated GET client for
   venue readback. Revalidate for every readback cycle and native dispatch.
6. `require_execution_binding` additionally requires a real transport, a writable
   key, explicit Wallet permission information, ContractTrade Order and Position,
   and no grants outside the narrowly allowed LINEAR permission set. Complete
   validated permission groups remain available through the safe identity; every
   transfer/withdraw grant is prohibited, as are unrelated product grants. Unknown
   permission names fail closed rather than being silently dropped.
7. `configure_native_execution(handle, scope=..., install=...)` constructs the
   existing pinned DEMO/LINEAR config with that same credential pair and gives it
   to a trusted native-worker installer callback. It returns no credential-bearing
   value. The callback must not log or persist the native config; it contains the
   credentials required by Nautilus. Leverage, margin and position-mode changes on
   connect remain unset in the existing builder.

The public identity is descriptive, not an independently accepted capability.
Permission queries do not override the writable-key and transport checks.
Python monkeypatching or reflection within the trusted worker process is not a
security boundary. Installer errors and provider errors expose only fixed codes.
Close revokes the capability before cleanup; an ordinary cleanup failure exposes
only `ACCOUNT_SESSION_CLOSE_FAILED`, without retaining a provider exception in
its cause or context. Cancellation still propagates after revocation.

## Verified upstream contract

The [Bybit API-key information documentation](https://bybit-exchange.github.io/docs/v5/user/apikey-info)
defines a parameterless signed `GET /v5/user/query-api`, including `apiKey`,
`userID`, `isMaster`, `parentUid`, `readOnly`, and the permission groups. Returned
key material is compared privately and never included in the public identity.

The [Demo Trading documentation](https://bybit-exchange.github.io/docs/v5/demo)
explicitly links this endpoint under “Get Demo Account API Key Info,” using the
demo key at `api-demo.bybit.com`. This explicit section matters because the
earlier generic available-endpoints table does not list the user endpoint.
Both primary pages were checked on 2026-11-02. The general key-info page describes
`isMaster` and `parentUid` individually; its main-account parent rule must not be
treated as a DEMO equivalence between those fields. The explicit DEMO endpoint
and the sanitized actual GET observation below support independent checks.

## Evidence boundary

Tests use fake HTTP and fake native import boundaries only. They verify request
signatures, scope, permission checks, revocation, secret suppression, and config
construction. Injected transport sessions cannot authorize execution. Tests that
replace the HTTP library boundary exercise the real-transport branch without
claiming a real network authentication or deployed DEMO qualification.

The root session separately performed a real DEMO GET-only metadata probe and
reported sanitized observations: `isMaster=False`, `parentUid == "0"`, parent
different from account UID, expected field types, matching key echo, empty secret,
no unknown permission groups/grants, and approximately -647 ms server delta.
No actual UID, credentials, or raw response were provided to this fix worker.
This exposed the old master/parent equivalence defect. The regression fixture
uses the same boolean/zero shape with fake credentials and a fake account UID.
That metadata probe alone does not attest operator-confirmed expected identity or
prove a successful binding with independently trusted expectations.

Native channel isolation, runtime integration, Linux worker construction, and
trading/protection qualification remain separate evidence requirements. Existing
runtime blockers must not be cleared solely because these checks pass. This fix
worker made no actual venue requests, native configuration, or orders.

## Review fix report (2026-11-02)

The P2 close-disclosure finding is addressed by revoking first and replacing
ordinary close exceptions outside their handler, so the public failure retains
neither provider message nor exception context. The actual DEMO metadata shape
is addressed by the mandatory independent master attestation, retaining exact
UID/parent checks, key comparison, strict types and DEMO credential ownership.

Covering tests: `tests/execution/test_account_binding.py`. New cases verify all
boolean/parent-zero combinations with independent exact expectations, reject
either expected-field mismatch and nonboolean expectations, revoke previous
handles on mismatch, suppress both fake credentials and close exception context,
and preserve close cancellation. Existing permission, credential, freshness,
scope and native configuration cases remain included.

Commands below ran from `native-provider-integration` using
`..\multi-exchange-ai-trader\.venv\Scripts\python.exe`:

```text
python -m pytest tests/execution/test_account_binding.py -q
Before implementation: 41 failed (required expected_is_master API absent).
After final implementation: 41 passed in 0.95s.

python -m ruff check src/multi_exchange_trader/execution/account_binding.py tests/execution/test_account_binding.py
All checks passed!

python -m mypy --strict --follow-imports=silent src/multi_exchange_trader/execution/account_binding.py tests/execution/test_account_binding.py
Success: no issues found in 2 source files
```

Official-page retrieval through Firecrawl was unavailable (`Unknown tool`);
direct web retrieval of the two linked primary Bybit pages succeeded. No private
evidence files, environment files, or credential-bearing raw metadata were read.

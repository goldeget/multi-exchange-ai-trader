# One-time selected Bybit DEMO account enrollment

Date: 2026-11-03 KST. Controller design under the user's delegated implementation authority, after a separate independent trust-policy review. Dependency: reviewed credential candidate e27f3add3ba707c676dae3f044da025797894882, PR24, exact-head CI112 pending when this design was seeded. Remote main was f629711aea357919347479ecde80e9788754e756. The parent must pass CI and merge; record the resulting main identity before claiming integration complete.

## Purpose and trust policy

Remember one operator-selected DEMO account across restarts, without repeatedly entering UID/parent/master. The user independently selected the DEMO UID before the next enrollment API observation; actual values stay in private operator records, never in source, tests, logs or this document. First enrollment may explicitly adopt authenticated provider relationship claims under `uid-anchored-adoption-v1`. This is `PROVIDER_OBSERVED_OPERATOR_ADOPTED`, not independently confirmed parent/master. It cannot independently detect an incorrect first provider relationship claim. Future fresh authentication pins all three fields and rejects drift.

Preserve the existing low-level authenticate signature, strict equality, session/key/host/time checks, revocation, permission/native/runtime gates. Update its source-policy documentation to distinguish independently confirmed expectations from an explicitly enrolled expectation context. Store no binding/receipt/native authority. Registration or adoption never starts monitoring, sends an order, enables AI or changes permission grants. SpotTrade remains intact; SPOT/LINEAR semantics belong to E16/E02.

## Global constraints

- Keep Python3.12, NautilusTrader1.231.0 and existing transports; add no dependency, remote service or cryptographic authority framework.
- First slice supports one selected BYBIT DEMO profile only. No multi-profile CRUD, account/relationship replacement, deletion, automatic migration, UI input endpoint or service activation.
- No secrets, key hashes/prefixes, permission/balance snapshots, host/file paths or execution flags in the account-profile payload.
- Root alone handles actual UID/keys/operator files and external tests. Workers use synthetic data and temporary paths, with both default credential/profile stores disabled.
- LIVE orders and additional server spending remain prohibited. Root's separately authorized actual DEMO/minimum-cost AI tests are not worker authority.
- Explicit startup always performs fresh three-field authentication. Stored expectations do not imply current authentication, qualification or mode activation.
- Default import/create_app/DRY_CHECK performs no profile/vault I/O. A complete legacy identity tuple bypasses profile I/O; partial or mixed identity sources fail closed.
- Root serializes Git operations, independent reviews, exact-head CI and merge/readback. Preserve original worktrees, sources and ignored evidence.

## Profile persistence

Use a separate private nonsecret JSON file: Windows `%LOCALAPPDATA%/MultiExchangeTrader/private/account-profiles.json`; Linux `$XDG_CONFIG_HOME/multi-exchange-trader/private/account-profiles.json` or `$HOME/.config/multi-exchange-trader/private/account-profiles.json`. `TRADER_ACCOUNT_PROFILE_FILE` accepts absolute path or `off`; explicit CLI `--profile-store` chooses a path for that call. Selection performs no stat/open. Reads create nothing.

Schema1 has exactly `schema`, `revision`, `selected_profile_id`, `profiles`. Revision is exact int1..2^63-1, never bool; schema exact int1. The persisted first slice contains exactly one profile and selected ID must equal its canonical UUIDv4. File absence means NO_PROFILE. Maximum UTF-8 file64KiB; reject duplicates, unknown fields/types/versions, NaN/Infinity, controls, malformed times/UIDs and extra profiles.

Each profile has exactly `profile_id`, `exchange`, `environment`, `uid`, `uid_evidence`, `relationship`, `last_observation`. Exchange/environment are BYBIT/DEMO. UID is the existing positive ASCII1..20-digit canonical string. `uid_evidence` has exactly basis OPERATOR_SELECTED, UUIDv4 evidence_id and canonical UTC-Z confirmed_at. Relationship is null while pending. last_observation is null initially and may be recorded with adoption; it is never an expectation source.

Complete relationship fields: parent_uid (same canonical UID rule, also allowing0; not equal to own UID), exact bool is_master, basis, source, evidence_id, confirmed_at, provider_observed_at, policy. Valid provenance combinations are INDEPENDENT_CONFIRMED with independent-v1 and source BYBIT_UI/BYBIT_SUPPORT/PRIOR_VERIFIED_RECORD, provider_observed_at=null; or PROVIDER_OBSERVED_OPERATOR_ADOPTED with uid-anchored-adoption-v1 and source BYBIT_DEMO_QUERY_API, original provider_observed_at retained. This slice creates only pending or adopted records; complete legacy flags remain available for independent expectations. Do not infer master from parent or vice versa. last_observation, when present, has source BYBIT_DEMO_QUERY_API, parent_uid, is_master, observed_at and server_observed_at.

Reuse reviewed path/file helpers rather than duplicate secret encryption code. Absolute private paths only; reject links/reparse/hardlinks/nonregular files and unsafe replacement. Linux requires owner plus0700/0600. Windows profile data is not encrypted and chmod is not an ACL guarantee. Use a separate fixed account-profile lock, bounded5s, exact revision/target checks, exclusive same-directory temp, fsync/atomic replace/confirming read and POSIX directory fsync. Retain the existing credential-store guarantees if minimally factoring a shared publication helper; no generic framework or broad rewrite. Unknown/failed publication returns fixed safe error and never false success. No automatic rollback/retry after ambiguous replace.

Registration creates a selected pending UID only when absent. Ordinary adoption only changes the exact pending UID/profile/revision; complete relationships cannot be overwritten even with a matching revision. Rotation/replacement is unsupported. Snapshots/expectations/repr/default status hide raw UID/parent/path; safe registration output is schema/revision/profile_id/state/basis only. Parsed local configuration is trusted operator expectation data, not external cryptographic proof.

## Enrollment candidate and explicit adoption

Use a separate credential-owning enrollment session from one reviewed CredentialResolver snapshot. Freeze UID/profile/revision before GET. Use real fixed `api-demo.bybit.com` server-time and key-info GETs, bounded observation30s and bounded existing retries. Reuse strict existing metadata validation where feasible: exact key echo, empty secret, selected UID, raw field types, parent/master independently parsed, permission schema, wall/monotonic/server-time validity. Observer must not call authenticate with its own response or set attestation/binding/identity/receipts/native configuration.

Candidate is an opaque exact session/generation-owned, single-use in-memory handle. Bind original client/transport/host/pair, selected profile/revision and observed times privately; no serialized candidate authority. Maximum TTL30s from observation start, using wall and monotonic checks plus server time; reject clock reversal/future/nonfinite values/slow request. New attempt, failure/cancellation, close or client/host/pair change revokes old candidates. Concurrent operations reject or serialize; no two successful adoptions.

Explicit adopt is a separate named operation in the same live process. Delegated root may invoke it without a new routine user prompt. No default observe/status/start acceptance. Before and after acquiring the CAS lock, and immediately before publication, recheck exact owner/generation/target/revision/freshness. Consume before possible publication. Guarded private store publication must only permit pending-to-adopted transition; failure/ambiguous write cannot reuse candidate. The file is local trusted configuration; Python reflection/same-user adversary isolation or rollback prevention is not claimed.

Repeated guards have no external or authority-changing effects. They may retain private nondecreasing wall/monotonic sample watermarks solely to reject an observed reversal between checks. Such bookkeeping never renews the original30sTTL, changes identity/target/generation, consumes again, persists data or activates anything. VALID-to-ADOPTING consumption and final retirement remain outside the repeated guard.

Final publication and close must have an explicit atomic commit order. If close wins before publication, the pending profile cannot be published afterward; if publication wins, close cannot report completion before that commit boundary. A minimal opt-in owner-held publication section may cover final validation and checked atomic replacement after the store lock is held and the temporary file is prepared. It must not hold the session mutex during a GET/await or filesystem-lock wait, turn a repeated guard into a publishing callback, renew freshness, weaken checked publication, or roll back/retry an ambiguous committed result. Credential publication defaults remain unchanged.

## Startup and compatibility

Retain one private immutable expectation/provenance context for the worker session and refresh closures. Resolve profile before credentials/network. Unknown policy/pending/corrupt source fails without fallback. Strict-only consumers reject adopted provenance; the reviewed monitor consumer explicitly supports the named policy. A new session compares fresh metadata to the saved tuple using existing authenticate; drift revokes and blocks without modifying profile. Key rotation with identical UID/tuple may work only after new fresh authentication; file/env changes never hot-switch a running context.

Worker --run-monitor supports either the complete existing --uid/--parent-uid/--is-master tuple, or saved selected profile (optional exact --account-profile UUID and --profile-store path). Mixed/partial identity overrides fail. Keep Linux/native/DEMO flags, symbol/directory/policy-db/lease and all other gates. Default CLI and invalid unsupported-platform entry must not read stores. Explicit profile status may read its existing file but never vault/HTTP/create; register-uid performs no credentials/HTTP; the named enroll-observed operation requires explicit policy and adoption intent. No raw input or private path in parser/error output.

## Acceptance and limits

TDD and synthetic tests cover strict parser/path/CAS/concurrency/publication errors, pending startup before key access, source-policy validation, opaque candidate forgery/replay/session/host/pair/generation/cancellation/time limits, lock-wait expiry, no complete overwrite, ambiguous publication, privacy, no-effects defaults, legacy compatibility, stable snapshots, fresh-process reuse and three-field drift/revocation. Keep required native36/restart6/restored12 and previous credential/history behavior at final exact head.

After independent task/whole reviews and exact successful CI/main readback, root registers the already selected UID, performs real GET-only observe/adopt and separate fresh authentication. Report provider-adopted provenance honestly. Native monitoring, actual fills/protection/restart/AI/server acceptance are separate63-register gates; this slice closes none of P01/P06/U05 by itself.

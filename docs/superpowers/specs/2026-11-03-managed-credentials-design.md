# Managed program credentials design
Date: 2026-11-03 KST. Baseline main f629711aea357919347479ecde80e9788754e756.
Approved product scope: the user's 63-item architecture register; latest steering requests one-time program-managed registration and automatic reuse, actual Bybit DEMO testing and lowest-cost paid AI testing. LIVE orders and new server spending remain prohibited.

## Intent and decisions
A user registers credentials once. App restart and newly constructed clients reuse a private program store without looking for .env each time. Existing explicit environment/file setup remains compatible where a store namespace is absent. Registration, client construction and status reads never authenticate, generate AI, place orders or enable modes.

Keep Python3.12, NautilusTrader1.231.0, existing HTTP clients, OpenRouterGenerationClient/quote/AIDispatcher and API DTO shapes. Add no crypto dependency, remote vault service, SDK modification, public key-input endpoint or browser storage. This slice belongs to A13/P06/U05; it does not close their remaining product/runtime/external acceptance.

Windows stores ciphertext protected by current-user DPAPI with UI forbidden, never machine scope or plaintext fallback. Linux uses current service-user-owned directory0700/file0600 and is explicitly access-controlled plaintext, not encryption. Existing root-owned0600 systemd EnvironmentFile remains a different supported deployment mechanism. Runtime secret strings cannot be claimed securely zeroized.

## Store contract
- New credentials.py (split platform protection/file I/O into focused helpers if needed) defines CredentialStoreError with fixed safe codes; redacted CredentialSnapshot/RegistrationResult; CredentialStore(path), load(), register(values, expected_revision); CredentialResolver(store, environ), bybit_pair(environment), ai_values(); default_credential_store(environ); import_env(path,store,expected_revision).
- Store explicit override TRADER_CREDENTIAL_FILE accepts an absolute path or off. Default Windows %LOCALAPPDATA%/MultiExchangeTrader/private/credentials.bin; Linux $XDG_CONFIG_HOME/multi-exchange-trader/private/credentials.json or ~/.config/multi-exchange-trader/private/credentials.json, requiring absolute/nonlinked private root. Read never creates files/directories.
- Bounded canonical schema1/revision and exact allowlist: BYBIT_API_KEY/BYBIT_API_SECRET/BYBIT_DEMO_API_KEY/BYBIT_DEMO_API_SECRET/ANTHROPIC_API_KEY/ANTHROPIC_MODEL/OPENROUTER_API_KEY/OPENROUTER_MODEL/AI_PROVIDER. Import no gates, UID claims, endpoints, modes or DB paths. Missing namespaces allowed; partial pairs rejected; final LIVE/DEMO key or secret reuse rejected.
- Strict JSON duplicate/version/field/type/control/size checks. Plaintext decoded payload <=16KiB, disk envelope <=64KiB, import <=16KiB/256lines. Never interpolate env content or execute it. Use existing provider validators via explicit mappings.
- Reject relative/.. paths, symlinks/junction/reparse components, hardlinks and nonregular files. Linux verifies owner and private modes. Windows never claims chmod establishes an ACL; DPAPI confidentiality and ACL limitations are documented separately.
- A fixed private OS lock serializes register. Bounded lock wait and revision check; no silent overwrite or unlink of lock. Initial expected_revision=None means absent store; later update requires exact revision. Validate merged complete result before write.
- Atomic same-directory exclusive tempfile, correct permissions before secret write, flush/fsync/replace/directory fsync where supported. Detect target replacement under lock. Any failure has fixed redacted result and never reports successful persistence without confirmation.
- Snapshot repr/str/errors/CLI metadata contain no secrets, prefixes/suffixes, ciphertext or source paths. Secret-bearing mappings remain private, never generic encoded or returned as API DTO. No credentials in business SQLite/backup inventory.

## Startup and client integration
- CredentialResolver reads one snapshot per startup/client resolution context. Valid store namespace wins; only absent namespace falls back to existing explicit env/file behavior. Existing malformed/unreadable store fails closed, never falls back to another credential.
- Resolve pair namespaces atomically; no half-pair mixing. Validate collisions across the final selected LIVE/DEMO pairs.
- load_ai_provider_config(environ=explicit_mapping) and load_anthropic_config(environ=explicit_mapping) stay pure relative to the supplied mapping and never discover the user's default vault. Disable file fallback on mappings assembled from stored credentials.
- Existing from_environment/for_demo clients gain a keyword-only optional resolver, preserving callers. App lifespan creates one resolver context for account/AI metadata and private factory; explicit injection supports tests. No secret I/O at import/create_app construction.
- Worker CLI default DRY_CHECK remains credential/network/storage free. Only explicit --run-monitor resolves saved DEMO credentials, preserving independently attested UID/parent/master and all permission/native/qualification gates. No key arguments or automatic flags.
- Private OpenRouter factory constructs the existing metadata client, generation client and dispatcher from one provider/key/model snapshot. It performs no GET/POST, price quote, budget change or enabled dispatch. Unsupported direct Anthropic generation stays unsupported.
- Registration/config status remains registered/unverified; existing public response shapes, live_order_enabled=false and external_calls=false stay unchanged. Stored keys do not imply BYBIT_DEMO=true.
- Source .env is preserved. Explicit credentials_cli --import-env ABSOLUTE_PATH (optional --store ABSOLUTE_PATH and --expected-revision N) imports once. No default scanning. Safe CLI output only schema/revision/registered namespaces. No key command arguments.

## Verification
Use synthetic credentials and temporary stores for tests. Existing autouse conftest adds TRADER_CREDENTIAL_FILE=off; each dedicated test explicitly supplies a temporary store. Exercise actual Windows DPAPI locally with synthetic values and actual Linux POSIX permissions in existing CI; platform skips are labeled.
Meaningful acceptance: import then make original source unreadable; fresh process/resolver/client still uses saved pair; no repeated source lookup or env mutation; corrupt-store no fallback; partial/same pairs; duplicate/oversized/unsupported schemas; path/link/mode/owner; concurrent revision registration; publication failure; ciphertext redaction; app status, default CLI and factory no effects; immutable startup snapshot; old env/mapping compatibility. Run focused tests then one exact final full CI after independent task and whole-branch reviews.

## External boundary
Root alone may import the user's existing local Bybit keys after the code is reviewed/tested, with no key output. Current read-only DEMO metadata succeeded but independently attested identity and least-privilege execution permission remain unresolved; UI connection currently fails to initialize. These facts never justify weakening gates. AI key location remains unresolved. Actual authorized DEMO/paid AI tests use the final supported path and bounded purpose/limits, and their external evidence is separate from code/CI.


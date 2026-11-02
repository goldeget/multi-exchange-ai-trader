# Selected DEMO account enrollment implementation plan

> Required workflow: superpowers:subagent-driven-development; root serializes Git. Spec: docs/superpowers/specs/2026-11-03-account-enrollment-design.md.

Goal: remember one user-selected Bybit DEMO account and explicitly enrolled provider relationship, then freshly authenticate it after restart without repeated identity entry. Parent reviewed credential candidate e27f3add3ba707c676dae3f044da025797894882 is frozen for PR24 CI112; no deployment/operator use while parent CI is pending.

## Global Constraints

- Keep Python3.12, NautilusTrader1.231.0 and existing transports; add no dependency, remote service or cryptographic authority framework.
- First slice supports one selected BYBIT DEMO profile only. No multi-profile CRUD, account/relationship replacement, deletion, automatic migration, UI input endpoint or service activation.
- No secrets, key hashes/prefixes, permission/balance snapshots, host/file paths or execution flags in the account-profile payload.
- Root alone handles actual UID/keys/operator files and external tests. Workers use synthetic data and temporary paths, with both default credential/profile stores disabled.
- LIVE orders and additional server spending remain prohibited. Root's separately authorized actual DEMO/minimum-cost AI tests are not worker authority.
- Explicit startup always performs fresh three-field authentication. Stored expectations do not imply current authentication, qualification or mode activation.
- Default import/create_app/DRY_CHECK performs no profile/vault I/O. A complete legacy identity tuple bypasses profile I/O; partial or mixed identity sources fail closed.
- Root serializes Git operations, independent reviews, exact-head CI and merge/readback. Preserve original worktrees, sources and ignored evidence.

## Review Focus

1. Pending/unsupported/corrupt profiles must block before credential resolution; complete legacy flags must not probe the profile.
2. The observer never authenticates against its own response or issues runtime authority. UID is fixed before GET; adopted provenance remains distinct from independent confirmation.
3. Candidate must be exact session/generation/target, one-use and fresh after lock wait/before publication; normal adoption cannot replace a complete relationship.
4. Store/profile revision, unsafe filesystem changes and ambiguous publication must not produce success, rollback or reusable authority.
5. Raw UID/parent/key/path cannot enter default repr/status/error/public DTO. Existing credential persistence and no-effects/gates remain intact.

### Task1: one-profile private store and pending registration

Files: new account_profiles.py (focused helpers permitted), account_profiles_cli.py for register-uid/status only; tests/account_profiles package with __init__.py and meaningful store/CLI tests; add only TRADER_ACCOUNT_PROFILE_FILE=off to global test fixture; new operations account-profile guide. Existing _credential_files.py/credentials.py may receive only the minimal shared lock/publication factoring needed to reuse tested primitives; preserve old default signatures/guarantees and run their regressions. No enrollment/network/worker behavior in this task.

Interfaces: AccountProfileStore(path).load()->immutable snapshot|None; register_uid(uid,*,expected_revision) creates absent pending selected profile; default_account_profile_store(environ) chooses path without I/O. Immutable snapshot exposes safe metadata and private expected fields; pending cannot produce expectations. A private guarded initial-adoption publication boundary supports Task2 issuer, rechecks inside CAS lock before mutation, rejects already-complete state. ProfileResolver or focused equivalent accepts explicitly supported policies; no file/string becomes opaque authentication authority.

- [ ] Read spec, preflight/brief and current reviewed credential helper interfaces. Escalate genuine conflicting interfaces before edits.
- [ ] Meaningful RED for one-time pending registration/fresh-process reuse, strict schema/unknown/duplicate/type/UID/time/policy, absent-read-no-create, privacy, unsafe paths/links/owner/modes, revision/concurrent update/publication failure and forbidden relationship overwrite.
- [ ] Implement exact schema1/maxone64KiB store, separate fixed5s lock and guarded atomic publication, redacted result/CLI errors and zero-network register/status.
- [ ] Run focused tests plus existing credential-store/import regression when helpers change, actual Windows synthetic file tests and static checks; root handles actual Linux/hosted gates. Never relax tests or use an operator profile.
- [ ] Self-review complete diff, write task1 report with exact RED/GREEN/commands/skips/limits. Root commits exact owned files and obtains independent task spec/quality review before Task2.

### Task2: GET-only enrollment and saved startup selection

Files: new focused account_enrollment.py (location by responsibility), extend profile CLI with explicitly named policy/adoption operation, minimal account_binding.py metadata parsing/delegation and source-contract documentation, worker_cli.py saved identity selection, relevant operations/deployment/progress docs and synthetic enrollment/worker tests. Preserve existing low-level authenticate signature and checks; avoid duplicated strict metadata parser. No private key export, public setup endpoint, permission relaxation, service activation or actual provider call by worker.

- [ ] Read Task1 approved API/report. RED real public-boundary tests: UID fixed before GET; malformed/wrong host/key/UID/schema/time reject; observer never sets authority; opaque/copy/replay/crosssession/changed target/generation/cancel/close reject; stale/lock-expired candidate and ambiguous write cannot reuse; adopted provenance persists; complete relation cannot overwrite.
- [ ] Implement one credential snapshot, bounded real DEMO GET observer and session-owned single-use candidate, separate explicit same-process adopt, guarded pending-to-adopted CAS publication. Synthetic transports never qualify actual operator adoption; tests may isolate lowest real transport boundary with fake responses without claiming venue proof.
- [ ] RED then integrate profile-before-credentials startup, complete legacy flags no-profile-I/O, mixed/partial/unknown/pending rejection, one expectation/provenance context through refresh, fresh authentication after new process and independent three-field drift/revocation; no hot rotation/default effects or gate changes.
- [ ] Run focused store/enrollment/credential/account-binding/worker/metadata/operations regressions and static checks. Update docs/progress partial boundaries; never store actual UID or source private paths.
- [ ] Self-review/report exact commands/results/limitations. Root commits, packages diff, independent task review and scoped fixes.

## Final integration

- [ ] One whole-branch independent review, consolidated fix wave if needed, scoped re-review; record every ruling/deferred item and source identity.
- [ ] Exact-head full required CI, preserving native36/restart6/restored12 and old product gates. Review actual failures; merge only exact successful reviewed head and verify main parents/tree/mainCI.
- [ ] Root alone uses supported reviewed code to persist the already supplied UID, GET-only observe/adopt, then separate fresh authentication. No actual value in public report/Git. New server/LIVE forbidden; actual order/AI acceptance stays separately bounded by63-register qualification.
- [ ] Continue next already-authorized engine/product work; no claim whole program or any full register item is complete merely from identity setup.

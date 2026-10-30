# Phase 2-A final CI recheck — 2026-10-31 KST

Baseline: `2fd0b8f754c4d62ff5d8e26e33c2594ad64ee1b3`, confirmed local/remote. PR #1 is open, draft, unmerged; `main` remains `7ddcded673ab9b0168fccf1aac106d02ff5a0b6d`.

## Requested single retry: PASS

Called `github_rerun_workflow_job` **once**, targeting only failed Dependency review job `124337839752` in run `41135809249`. No full-workflow dispatch, additional retry or security-setting mutation was made.

[Run 41135809249, attempt 2](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41135809249/attempts/2) completed **SUCCESS**. Its actual `head_sha` is `6c19097a2f265a37db004bafc3c8f44f1266c192`, not the later documentation-only baseline or final report SHA.

| Required check | Latest-attempt job API ID | Result |
| --- | --- | --- |
| Dependency review | `124346188562` | PASS |
| Python checks | `124346190315` | PASS |
| Secret scan | `124346191057` | PASS |
| Frontend checks | `124346242375` | PASS |

Actual Dependency review job log:

```text
2026-10-30T22:28:25.8791786Z Dependency review did not detect any vulnerable packages with severity level "moderate" or higher.
2026-10-30T22:28:25.8793407Z Dependency review did not detect any denied packages
```

The action successfully accessed and evaluated the repository's dependency comparison. Dependency graph availability to this Actions token is now **operationally confirmed**. Direct reading of the administrator's ON/OFF toggle remains unavailable through the connector; no administration API response or exact HTTP 200 response was invented. The earlier generic HTTP403 blocker did not recur, so no further investigation/retry of that former failure is necessary.

The successful log also contains **31 non-failing OpenSSF Scorecard warnings** for npm packages below the action's score threshold. These are not vulnerability findings at the configured moderate threshold, nor a reason to disable the check. The comparison's recognized dependency coverage is not proof that every transitive entry of `uv.lock` was reviewed; local Python checks remain independent.

## Successful checks, backup preservation, and SHA scope

The successful Secret scan job log returned by the latest-attempt API retains original execution timestamps `2026-10-30T22:06:08`–`22:06:12Z`, rather than claiming a fresh backup execution. Artifact metadata still lists **one** artifact: `13015637577`, created `2026-10-30T22:06:12Z`, digest `ca1d2520228eec9132965adee272801aa2334312f0e542dda721e5eb12cc2ac0`. No backup was deleted, overwritten or newly requested in this recheck. The user reports having downloaded it separately.

`git diff --exit-code 6c19097a2f265a37db004bafc3c8f44f1266c192 -- .github src tests scripts apps pyproject.toml uv.lock` exited 0: workflow, application, verification script, tests, manifests and locks are unchanged. All new tracked changes are verification documentation. Thus no extra latest-SHA full CI run is needed; report commits use `[skip ci]` to avoid unnecessary runs and backup artifacts. Hosted CI PASS applies to the linked head; fresh local results are in [final-recheck-tests.md](final-recheck-tests.md).

No permissions, pinned actions, security gates or runner classes changed. No paid API, order, credential use, merge or Phase 3 implementation occurred.

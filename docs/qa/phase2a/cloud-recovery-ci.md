# Phase 2-A CI recovery diagnosis — 2026-10-30 UTC

## Observed state

Read-only GitHub API checks reconfirmed [CI run 41133846306, attempt 2](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41133846306/attempts/2): completed, failure; event `pull_request`; workflow head `2dbb6b4965936fe01189760cbddc8eb64b64ddf6`. The run's current PR metadata references documentation head `db9b4304114d16efeb50aa2f3b326d74b1eb50d4`; that does not change the run's actual `head_sha`. Python, frontend and secret-scan jobs succeeded. Dependency review job `124334405854` failed.

The dependency-review job log reports effective `Contents: read`, `Metadata: read`, `PullRequests: read`; the pinned action downloaded successfully. At `2026-10-30T21:57:30.8681671Z` it emitted:

```text
Dependency review is not supported on this repository. Please ensure that Dependency graph is enabled, see https://github.com/goldeget/multi-exchange-ai-trader/settings/security_analysis
```

The repository metadata is `visibility: public`, owner type `User`, and connected identity permission `admin: true`. This is not evidence that the connector can administer repository security settings, nor evidence of the Actions job token having administration permissions.

## What the pinned action establishes

The exact pinned [src/main.ts](https://github.com/actions/dependency-review-action/blob/a1d282b36b6f3519aa1f3fc636f609c47dddb294/src/main.ts) emits this message for a caught Octokit `RequestError` with HTTP status **403** when the event repository is public. It substitutes this generic message for the underlying response. Consequently the log proves a forbidden API request, but does **not** independently prove that Dependency graph is disabled or that indexing is pending.

The action's first data request, in [src/dependency-graph.ts](https://github.com/actions/dependency-review-action/blob/a1d282b36b6f3519aa1f3fc636f609c47dddb294/src/dependency-graph.ts), is `GET /repos/{owner}/{repo}/dependency-graph/compare/{basehead}`. `main.ts` invokes it before dependency filtering, vulnerability findings or comments. The logs contain no finding list. Snapshot-warning retries only happen after a successful comparison response with warnings; they do not repair this 403 path.

The pinned [README](https://github.com/actions/dependency-review-action/blob/a1d282b36b6f3519aa1f3fc636f609c47dddb294/README.md) supports public repositories and shows `contents: read`. The observed job already has that permission. No evidence supports increasing workflow permissions, changing application code, replacing locks, buying Advanced Security, or weakening the dependency gate to fix this failure.

## Connector boundary and recovery

Tool discovery found no callable Dependency graph setting read/write or dependency comparison tool. An attempted read of the exact comparison URL through GitHub Fetch was rejected by the connector with `INVALID_ARGUMENT`, HTTP 400, `GitHub Fetch URL is not an allowed public GitHub repository or search endpoint`. This is a connector allowlist rejection, not a response from the repository dependency endpoint. No alternate network path was attempted, no setting was mutated, and no unchanged failed job was rerun.

Recovery requires access to the GitHub security setting or an allowed authenticated dependency-graph API client:

1. At [this repository's security settings](https://github.com/goldeget/multi-exchange-ai-trader/settings/security_analysis), verify and save **Dependency graph** enabled; then reload to verify persistence. If the save fails, retain the actual save error rather than treating the toggle as enabled. This setting is separate from a Codex Cloud environment/network setting.
2. Verify [the dependency graph](https://github.com/goldeget/multi-exchange-ai-trader/network/dependencies), and obtain the comparison endpoint's actual HTTP status/body for base `7ddcded673ab9b0168fccf1aac106d02ff5a0b6d` and head `2dbb6b4965936fe01189760cbddc8eb64b64ddf6`. A repository administrator with an existing authorized GitHub CLI session can read it using the following command; it was **not executed** in this recovery session:

   ```sh
   gh api --include 'repos/goldeget/multi-exchange-ai-trader/dependency-graph/compare/7ddcded673ab9b0168fccf1aac106d02ff5a0b6d...2dbb6b4965936fe01189760cbddc8eb64b64ddf6'
   ```

3. If the graph setting is confirmed saved and that authenticated comparison still returns 403, use its underlying error and GitHub request ID to investigate with GitHub support. Do not infer a package vulnerability or an application bug from the action's generic message. If comparison succeeds for the administrator, the Actions token/service path still needs verification by the targeted job.
4. After evidence of a changed prerequisite, rerun only failed Dependency review job `124334405854` in run `41133846306`, and inspect the replacement job's conclusion and findings. Preserve the distinction between that historical run head and any later documentation SHA. No security PASS is established until the actual job succeeds.

These observations do not diagnose the user's Cloud settings save failure itself; they establish the independent CI service dependency and the precise information unavailable through the connected tools.

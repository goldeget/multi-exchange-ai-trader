# Phase 2A resumed automated verification

Verified on 2026-10-30 at approximately 21:48–21:49 UTC from branch
`codex/initial-platform`, commit `90efa83cbfcc4272dd3b1baeb5097001d44d766b`.
Commands ran from the repository root against the existing locked dependencies.
No application code or dependency manifests/lockfiles changed during these checks.

## Runtime and locked installation

| Command | Result |
| --- | --- |
| `uv --version` | `uv 0.12.23` (workflow-pinned version) |
| `python3 --version` | Python 3.12.14 |
| `node --version` | v24.19.0 |
| `npm --version` | 11.9.0 |
| `uv sync --locked` | Exit 0; resolved 30 packages, installed 29 packages in a fresh `.venv` |
| `npm ci --prefix apps/web` | Exit 0; installed 267 packages |
| `git diff --exit-code -- uv.lock apps/web/package-lock.json` | Exit 0; both lockfiles unchanged |

The npm install emitted a `whatwg-encoding@3.1.1` deprecation warning and an npm
update notice. Neither was a check failure; no dependency update was made.

## Checks matching `.github/workflows/ci.yml`

| Command | Exit | Observed result |
| --- | --- | --- |
| `uv run pytest -q` | 0 | **242 passed** in 2.23 seconds |
| `uv run ruff check src tests` | 0 | All checks passed |
| `uv run mypy src` | 0 | No issues found in 11 source files |
| `npm test --prefix apps/web -- --run` | 0 | **8 tests passed** in 1 test file; 3.72 seconds total |
| `npm run lint --prefix apps/web` | 0 | ESLint completed without diagnostics |
| `npm run typecheck --prefix apps/web` | 0 | TypeScript completed without diagnostics |
| `npm run build --prefix apps/web` | 0 | TypeScript and Vite 7.2.2 production build passed; 37 modules transformed |

The production build reported CSS 5.34 kB (gzip 1.76 kB), JavaScript 369.97 kB
(gzip 118.58 kB), and HTML 0.46 kB (gzip 0.32 kB). Build artifacts remain ignored.

## Checksum-verified Gitleaks

Downloaded the exact release and verified the exact SHA-256 configured in CI
before extracting or running the executable:

```sh
mkdir -p work/gitleaks
curl --fail --location --silent --show-error \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  --output work/gitleaks/gitleaks.tar.gz
printf '%s  %s\n' \
  '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' \
  'work/gitleaks/gitleaks.tar.gz' | sha256sum --check
tar -xzf work/gitleaks/gitleaks.tar.gz -C work/gitleaks gitleaks
work/gitleaks/gitleaks version
work/gitleaks/gitleaks git --redact --no-banner .
```

All commands exited 0. Checksum output: `work/gitleaks/gitleaks.tar.gz: OK`.
Executable version: `8.30.1`. History scan: **20 commits**, approximately
474,185 bytes, **no leaks found**. `git rev-parse --is-shallow-repository` returned
`false`. This is a scan of repository history at the stated commit, before these
new QA documents were committed; it is not a claim about later commits.

## Scope and limits

No automated check failures occurred. The workflow's GitHub dependency-review
action was not executed locally; its hosted pull-request result is separate.
These checks do not establish live exchange reachability or browser behavior;
those have separate QA records. No orders, API keys, paid services, or Phase 3
functionality were used.

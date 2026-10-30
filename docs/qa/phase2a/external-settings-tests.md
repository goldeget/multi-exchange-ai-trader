# External-settings verification: automated checks

Verified 2026-10-30 at 21:57 UTC on branch `codex/initial-platform`, commit
`24b625a6e18a6467624eff98921449615321869e`.

All checks below were run afresh from the repository root. Existing installed
dependencies were used; `git diff -- uv.lock apps/web/package-lock.json` was empty.
No dependency installation or source changes were needed.

| Command | Exit | Observed result |
| --- | --- | --- |
| `uv run pytest -q` | 0 | 242 passed in 2.11s |
| `uv run ruff check src tests` | 0 | All checks passed |
| `uv run mypy src` | 0 | No issues in 11 source files |
| `npm test --prefix apps/web -- --run` | 0 | 1 test file, 8 tests passed |
| `npm run lint --prefix apps/web` | 0 | ESLint passed |
| `npm run typecheck --prefix apps/web` | 0 | TypeScript passed |
| `npm run build --prefix apps/web` | 0 | TypeScript and Vite production build passed; 37 modules transformed |
| `printf '%s  %s\n' '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' 'work/gitleaks/gitleaks.tar.gz' \| sha256sum --check` | 0 | Archive checksum OK; expected digest matches `.github/workflows/ci.yml` |
| `tar -xzf work/gitleaks/gitleaks.tar.gz -C work/gitleaks gitleaks` | 0 | Binary extracted from the verified archive |
| `work/gitleaks/gitleaks git --redact --no-banner .` | 0 | 24 commits scanned, approximately 528.50 KB; no leaks found |

Runtime versions: Python 3.12.14, uv 0.12.23, Node v24.19.0, npm 11.9.0,
Gitleaks 8.30.1. These match CI's Python 3.12, Node 24, uv 0.12.23 and
Gitleaks 8.30.1 targets.

The automated checks cover the full configured Python and frontend suites.
Browser behavior and public exchange reachability are documented separately.
No API keys, authenticated exchange actions, orders, paid services, or Phase 3
features were used in this verification.

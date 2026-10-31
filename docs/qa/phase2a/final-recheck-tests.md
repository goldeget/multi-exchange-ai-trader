# Phase 2-A final recheck: automated regression

Fresh local checks ran on **2026-10-30 at 22:28 UTC** on branch
`codex/initial-platform`, commit
`2fd0b8f754c4d62ff5d8e26e33c2594ad64ee1b3`.

All 257 repository Python tests and all 8 frontend tests passed. Static checks,
frontend production build, and checksum-verified Gitleaks full-history scan also
passed. The initial bare pytest invocation failed because it also collected a
preexisting untracked recovery copy under `work/`. The parent preserved that
backup by renaming `work/recovery-workingtree-check` to
`work/.recovery-workingtree-check`; pytest skips the hidden directory. The final
bare pytest invocation then passed without changing source or configuration.

| Command | Exit | Actual result |
| --- | --- | --- |
| `uv run pytest -q` | 2 | Collection failed with 6 import-file mismatches between `tests/` and duplicate test modules under `work/recovery-workingtree-check/tests/`; no test failure was reported. |
| `uv run pytest tests -q` | 0 | **257 passed in 0.86s**; all repository tests collected from `tests/`. |
| `uv run pytest -q` (after scratch backup rename) | 0 | **257 passed in 0.96s**; final full default-discovery run. |
| `uv run ruff check src tests scripts` | 0 | All checks passed. |
| `uv run mypy src scripts` | 0 | Success: no issues found in 12 source files. |
| `npm test -- --run` | 0 | **8 passed**, 1 test file; Vitest 4.0.14, duration 3.49s. |
| `npm run lint` | 0 | ESLint passed. |
| `npm run typecheck` | 0 | TypeScript `tsc --noEmit` passed. |
| `npm run build` | 0 | TypeScript and Vite 7.2.2 production build passed; 37 modules transformed, built in 1.33s. |
| Archive SHA-256 check (below) | 0 | `work/gitleaks/gitleaks.tar.gz: OK`. |
| `tar -xzf work/gitleaks/gitleaks.tar.gz -C work/gitleaks gitleaks` | 0 | Extracted only after successful archive verification. |
| `work/gitleaks/gitleaks version` | 0 | `8.30.1`. |
| `work/gitleaks/gitleaks git --redact --no-banner .` | 0 | **32 commits**, approximately **591225 bytes (591.22 KB)** scanned; **no leaks found**. |

Python, Ruff, mypy and Gitleaks commands ran from the repository root. Frontend
commands ran from `apps/web`. The existing archive was verified against the
digest pinned in `.github/workflows/ci.yml` before extraction:

```bash
printf '%s  %s\n' \
  '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' \
  'work/gitleaks/gitleaks.tar.gz' | sha256sum --check
```

This recheck made no application, workflow, dependency, or Git history changes.
It started no servers and used no exchange/private/order/key or paid API.
These are local regression results; they do not change any hosted CI or
dependency-review status.

# Exact historical secret-scan false positives

CI run41271527823, source3e46477ce2c7d9db8f6eda2f5d32da7c98082b76, reported two `generic-api-key` findings:

- `apps/web/tests/ui-browser/routes.ts:17`: a public screen identifier (`12-diagnostics`) in the fixture route table, beside the property named `key`.
- `apps/web/tests/ui-browser/offline.spec.ts:39`: comparison of that same public screen identifier through `route.key`.

Both are static UI navigation/test names with no authentication capability, account binding or random credential material. The source was inspected at the reported immutable commit and lines. There is no credential to rotate. The field is now named `screenId`, retaining the same routes, screenshot names and assertions.

Only those two immutable historical fingerprints are documented in `.gitleaksignore`, because renaming current code does not remove the old Git commit from a history scan. No rule, path, file class or future occurrence is exempted. Gitleaks still scans all repository history with its existing redaction and failure policy. The next complete CI must verify both the historical exceptions and current source.

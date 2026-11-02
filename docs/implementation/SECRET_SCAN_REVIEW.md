# Exact historical secret-scan false positives

CI run41271527823, source3e46477ce2c7d9db8f6eda2f5d32da7c98082b76, reported two `generic-api-key` findings:

- `apps/web/tests/ui-browser/routes.ts:17`: a public screen identifier (`12-diagnostics`) in the fixture route table, beside the property named `key`.
- `apps/web/tests/ui-browser/offline.spec.ts:39`: comparison of that same public screen identifier through `route.key`.

Both are static UI navigation/test names with no authentication capability, account binding or random credential material. The source was inspected at the reported immutable commit and lines. There is no credential to rotate. The field is now named `screenId`, retaining the same routes, screenshot names and assertions.

Only those two immutable historical fingerprints are documented in `.gitleaksignore`, because renaming current code does not remove the old Git commit from a history scan. No rule, path, file class or future occurrence is exempted. Gitleaks still scans all repository history with its existing redaction and failure policy. The next complete CI must verify both the historical exceptions and current source.

## Managed credential validation fixtures

PR #24 CI run 111, secret-scan job `125579793349`, reported two `generic-api-key` findings with Gitleaks 8.30.1 in a redacted history scan of 157 commits. This CI metadata was supplied for triage; the exact committed source and immediate fixture use were inspected locally with `git show`:

- `a456c7ec24aaba3993d6c85d41c073591bd3e56d:tests/credentials/test_store.py:175`: a literal synthetic provider value in `test_anthropic_model_cannot_contain_other_provider_credential` (lines 168-178). The same synthetic marker is deliberately embedded in the model field to exercise cross-field validation. The store is constructed under pytest `tmp_path`; registration must raise `INVALID_VALUES`, and the test asserts no store exists.
- `1fd6e996988f02b0113b67b063122a0364f0f617:tests/credentials/test_import.py:80`: a literal synthetic provider value in `test_provider_validation_applies_to_both_namespaces` (lines 76-84). Its helper writes the inline payload to `synthetic.env` under pytest `tmp_path`; the other namespace has a deliberately invalid value. Import into a temporary store must raise `INVALID_VALUES`.

Both values originate in negative-test source literals, not operator files, environment values, or provider responses. They are intentionally synthetic fixtures, not issued credentials; the literals are omitted here. Only these two exact commit/path/rule/line fingerprints are added to `.gitleaksignore`. Existing scanner configuration and redaction remain unchanged; no path, rule, regex, or future occurrence is exempted. Updating current strings alone would not clear their historical findings. A complete hosted history scan at the new committed HEAD is still pending and must verify the exceptions.

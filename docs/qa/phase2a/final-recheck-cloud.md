# Phase 2A final Cloud environment recheck

Checked 2026-10-30 22:28 UTC (2026-10-31 07:28 KST), against task baseline `2fd0b8f` on `codex/initial-platform`. This is runtime/configuration evidence, not a live-market success record.

## Fresh attached-runtime evidence

`cloud_environment.environment_status({})` returned the following values during this recheck:

| Field | Observed value |
| --- | --- |
| Environment ID | `ccarenv_b64_Y2NhcmVudl8yNGJmOTNjY2IxYzA4MTkxODhiZjJmZDcyMDZiYjljNg` |
| Source configuration ID | `48bc58a4-94d3-4f4a-9be9-ae3e35def1df~cecfg_6ae4f2cc06308193a2fbe43d20278aa1` |
| Source configuration version ID | `48bc58a4-94d3-4f4a-9be9-ae3e35def1df~cecfgver_6ae4f35f6ae881939d2b90c28e381c20` |
| Spec / observed spec revision | `1` / `1` |
| Observations current | `true` |
| Provider / connectivity | `cloud` / `connected` |
| Desired / observed phase | `running` / `running` |
| Failure / network diagnostics | `null` / `null` |
| HTTP policy mode / state | `restricted` / `enforced` |
| Custom allowed hosts | `[]` |
| Presets | `["package_managers"]` |
| Capabilities / secrets / runtime variables / outbound identities | all `[]` |

The effective host list includes package-manager preset destinations, including `github.com` and `raw.githubusercontent.com`. It does **not** include any of these required exact hosts:

| Required exact host | Runtime effective list | Local startup policy |
| --- | --- | --- |
| `data-api.binance.vision` | absent | absent |
| `fapi.binance.com` | absent | absent |
| `api.bybit.com` | absent | absent |
| `api.github.com` | absent | absent |

The independently read `/etc/codex/network-policy.json` is version 1, HTTP policy `restricted`, `vpn_configured: false`, with TCP `domains: []` and `ip_ranges: []`. This file records startup policy; current status is the runtime HTTP observation. Editing the file cannot grant access. No policy file or proxy setting was changed.

## Configuration, draft, and publication are different evidence

The IDs above identify the configuration/version from which this attached runtime was created. The status tool explicitly does **not** describe the latest editable configuration draft. A source configuration version ID is not a draft ID or evidence that a later edit was published.

Earlier [work-log entries](../../WORK_LOG.md) describe a different environment at revision 7, policy state `unknown`, and a draft save returning `requires_publish: true`. They do not record a comparable prior source configuration/version ID. The fresh runtime is revision 1, matches the IDs in [the earlier attached-runtime record](external-settings-live.md), and still omits the required hosts. The current editable draft contents, its version ID, any subsequent publication, and whether the user edited another environment are **unobserved**. Do not reinterpret the current source version as the previous draft version or claim that a saved draft was lost.

## Supported settings/publication workflow and its limits

The installed [Cloud Environment runtime skill](skill://plugin_connector_1p_c5b7d5df5d7081918f2c4be5a633ed5d/cloud-environment-runtime/SKILL.md) states: “Configuration changes require the environment configuration workflow and its user review.” Its [networking reference](skill://plugin_connector_1p_c5b7d5df5d7081918f2c4be5a633ed5d/cloud-environment-runtime/references/networking.md) requires the supported configuration workflow for a destination-policy denial. These are the verified workflow requirements available to this task.

The required configuration result is a reviewed environment configuration for the attached task that permits the four exact hosts above. If that workflow reports `requires_publish: true`, publication must complete before testing the attached runtime. A save message alone is insufficient. Afterwards, reread status and require `observations_current: true`, matching spec/observed revision, policy state `enforced`, and all four exact hosts in `effective_allowed_hosts`. Reconcile the environment/source IDs as well, because changes to a different environment do not establish readiness here. Only then resume authorized exchange collection and the dependent real-data QA.

The current connector exposes exactly three Cloud tools: runtime status, retained diagnostics, and current diagnostic read. They provide no draft reader/editor, save, Publish, restart, environment selection, or settings UI access. No browser/settings interaction tool is available for this workflow. Exact current UI labels, navigation, the user's save-error message, and the precise Publish interaction could not be verified. A scoped GitHub code search of the official `openai/codex` repository for `cloud environment internet access` returned no results; it did not establish current Cloud settings instructions. No unverified click-by-click instructions or completed save/publication are claimed.

## Save-failure diagnostics

`cloud_environment.environment_diagnostics({})` returned:

```json
{
  "history_status": "not_found",
  "record_limit": 8,
  "omitted_records": 0,
  "legacy_snapshot_available": false,
  "records": []
}
```

The response identifies the same environment ID as above. No retained capture/attempt/runtime record exists in this response to correlate with the reported settings-save failure. Runtime `failure: null` and missing diagnostic records do not establish control-plane health, successful saving, or a root cause. Raw diagnostic logs were not requested or dumped. The save failure cannot be reproduced or repaired through the available read-only Cloud tools. Useful next evidence is the actual settings-save error and its time/request identifier, together with the configuration/version being edited; these are currently unavailable rather than assumed.

## Result matrix

| Check | Result | Evidence or limitation |
| --- | --- | --- |
| Read current attached environment identity/readiness | PASS | Fresh status above; observations current |
| Independently inspect local startup network policy | PASS | Restricted version-1 file; four required hosts absent |
| Required exchange/GitHub API allowlist applied to this runtime | FAIL | Exact hosts absent from enforced effective policy |
| Inspect retained diagnostic metadata | PASS | Metadata read succeeded; `not_found` is the observed result |
| Diagnose or repair settings-save root cause | BLOCKED | Save/UI error unavailable; no control-plane mutation tool |
| Inspect latest editable draft/version and verify Publish steps | BLOCKED | No draft/settings UI tool; exact UI procedure unverified |
| Save/publish/restart this environment | UNRUN | No corresponding Cloud tool; no claim of completion |
| Fresh live ticker/candle calls and real-data browser QA | BLOCKED | Prerequisite policy unchanged; zero new exchange probes |

No paid API, credential entry, order, proxy/VPN/alternate-host bypass, backup creation, merge, or Phase 3 work was performed by this recheck. Phase 2A remains incomplete.

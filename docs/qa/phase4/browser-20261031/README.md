# PHASE4 actual local browser evidence

UTC timestamps in JSON are original. [KST index](../evidence-index.json) provides Asia/Seoul display times.

- desktop.json/mobile.json: initial actual captured spot native reports and separate synthetic auto entry/pause, chart/layout/keyboard.
- process-restart.json: actual PID exit/new process, same SQLite, committed SQL before/after and no automatic resume.
- desktop-post-restart.json/mobile-post-restart.json: corrected fault harness PASS on explicitly fresh synthetic fault accounts, response-loss same-key retry and mobile loss halt. Original restart account is referenced, not reseeded.
- desktop-ui-contract.json/mobile-ui-contract.json: final read-only info smoke; native curve, actual scheduler health, immutable run configuration, layout.
- `*-post-restart-failure.png`: historical harness FAIL, expected dialog dismissal after local502 was wrong. Product retained uncertainty correctly. These are not PASS screenshots.
- `*-final-ui-contract.png`: final PC1440x1000/mobile390x844 screens. Other named PNGs show actual recorded stages; all prices/account performance remain labeled virtual.

Local abort/502 is induced browser failure after genuine local API commit, not an upstream outage. Keys are omitted from public JSON; digests/same-payload booleans prove retry continuity. Browser scripts used readonly chart references for range inspection, not price injection. No restricted-host request. Physical-device/exhaustive accessibility and Phase4 pinch revalidation UNRUN.

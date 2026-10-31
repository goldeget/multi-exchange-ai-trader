# Exact Nautilus v1 qualification — 2026-10-31 KST

**PASS for selected native API qualification, not full PAPER acceptance.** Installed exactly nautilus_trader1.231.0 in a separate throwaway Py3.12 venv, imported and executed the Cython/Rust-backed BacktestEngine/SimulatedExchange. Wheel tag cp312-cp312-manylinux_2_35_x86_64; host CPython3.12.14/Linux x86_64/glibc2.41. [Wheel metadata](qualification/wheel-metadata.json).

Official tag[v1.231.0](https://github.com/nautechsystems/nautilus_trader/tree/v1.231.0), peeled commit27a8e54e7ac3c57d6cbf8891f0283dfbaee97317. Official[release](qualification/release.json) says “NautilusTrader1.231.0 Beta” with prerelease=false. No v2 APIs or exchange live factory/client used. Source remains unmodified under ignored work/ for inspection.

[Native market/reduce-only output](qualification/native-market.json): buy0.100 at60000.1, reduce0.040 at60999.9, reduce0.060 at58999.9, all native fills. PricePnL=(60999.9−60000.1)*0.04+(58999.9−60000.1)*0.06=−20.02000000. Native fees2.40000400+0.97599840+1.41599760=4.79200000. Native margin account total9975.18800000 and flat native position exactly reconcile10000−20.02−4.792. Side slippage is adverse1 tick each.

[Native conditional OCO output](qualification/native-oco.json): entry60000.1, native MARKET_IF_TOUCHED TP sell61499.9 on synthetic61500 gap; fee2.40000400+2.45999600=4.86000000; gross149.98000000; cash10145.12000000. Native STOP_MARKET sibling canceled; later58000 tick does not create another exit. Thus this is actual native matching/position/accounting, not import-only or a custom fill simulator.

Inspected exact v1 sources: backtest/engine.pyx add_venue/use_reduce_only/use_message_queue=False, run(streaming=True), native L1 FillModel adverse ticks; common/factories.pyx and model/orders constructors for STOP_MARKET/MARKET_IF_TOUCHED/OrderList/OCO; accounting/accounts/margin.pyx permits negative total while free may be0. Isolated allocation/liquidation/restart remain application contracts requiring integration tests, not qualified by these probes alone. Native Position average fields use internal floats; the product must not store those as a float ledger: authoritative native Money/Price/Quantity as Decimal strings, independently reconciled actual fills.

The throwaway latest dependency resolution selected pandas3.1.0, which emits an upstream Pandas4Warning about Timestamp.utcnow. It did not prevent native fills; record it rather than claim warning-free upstream. Production will lock exact transitive artifacts and retain this compatibility observation. No library patch is made.

All prices/outcomes are synthetic local qualification scenarios, not exchange data or real earnings. Existing fapi/Bybit blocks and PHASE2A incomplete status are unchanged.

## Security-qualified dependency resolution (2026-10-31 KST)

The first source CI, [41150363112](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41150363112), passed Python, frontend and secret scan but failed dependency review job `124385186090`. This was a real **High** finding, `GHSA-27vj-qcqg-25rc`: fsspec SSTI can permit arbitrary code execution. Affected versions are `>=0.9.0,<2026.6.0`; the fixed version is `2026.6.0`. It was neither a license failure nor a transient API error.

NautilusTrader 1.231.0 wheel metadata pins `fsspec==2026.2.0`. The project therefore explicitly pins and applies a uv transitive override to **fsspec==2026.6.0**, while keeping the original Nautilus 1.231.0 wheel and library sources unmodified. This is a documented departure from its strict upstream dependency metadata, not an advisory exception. Dependency review severity policy and all CI security gates remain unchanged. Qualification is for this application's local simulated-exchange/replay paths; compatibility of every upstream adapter or fsspec integration is not claimed.

`uv lock` and `uv sync --locked` accepted this resolution. Repeating the actual native probe under the patched dependency produced the same three fills, `4.79200000` fees and `9975.18800000` native cash. Integrated tests and the next source CI must also pass before final acceptance. Official advisory: https://github.com/advisories/GHSA-27vj-qcqg-25rc .

## Frontend security toolchain qualification

The later whole-tree npm audit found14 existing dev-tool vulnerable package entries (12Moderate/1High/1Critical), despite change-focused Dependency review passing. High was direct Vite7.2.2 (including GHSA-v2wj-q39q-566r/GHSA-p9ff-h696-f583); Critical was direct Vitest4.0.14 (GHSA-5xrq-8626-4rwp, UI-server conditional exposure). Neither advisory establishes exploitation in this QA. Direct Moderate entries were @eslint/js, eslint and typescript-eslint; nine transitive entries propagated minimatch/mocker advisories. The baseline package manifests/lock had been unchanged from ee49820a.

The user explicitly authorized repair and a fourth necessary source CI. `npm audit fix --dry-run` left14 findings and suggested incompatible major downgrades; no `--force` or advisory ignore was used. Exact same-major Vite7.3.7 and Vitest4.1.12 resolve their advisories. Latest ESLint10.14.0/typescript-eslint8.72.1 still constrain minimatch10.x, with GHSA-g3hv-75wg-m249 affecting versions<11.0.1. A version-scoped override `minimatch@^10.2.2 ->11.0.1` is therefore explicit: it crosses a transitive major boundary and is not claimed to satisfy upstream semver metadata. Inspected consumers use the retained named `Minimatch`/`minimatch` APIs; package CJS/ESM exports support Node24. Compatibility is qualified for this repository using full tests/lint/types/build plus a real ESLint negative control proving TS source globs still select files and report unused-variable errors (valid exported source has0errors).

The resolved lock audit reports **0 total vulnerabilities**, including0Moderate/High/Critical. Registry advisories are time-scoped information, not a proof against all possible vulnerabilities. Final patched-toolchain browser/CI results and original/repaired audit evidence are recorded in the Phase3 report. Native qualification and the actual process-restart evidence are preserved unchanged.

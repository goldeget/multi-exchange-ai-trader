# Exact Nautilus v1 qualification — 2026-10-31 KST

**PASS for selected native API qualification, not full PAPER acceptance.** Installed exactly nautilus_trader1.231.0 in a separate throwaway Py3.12 venv, imported and executed the Cython/Rust-backed BacktestEngine/SimulatedExchange. Wheel tag cp312-cp312-manylinux_2_35_x86_64; host CPython3.12.14/Linux x86_64/glibc2.41. [Wheel metadata](qualification/wheel-metadata.json).

Official tag[v1.231.0](https://github.com/nautechsystems/nautilus_trader/tree/v1.231.0), peeled commit27a8e54e7ac3c57d6cbf8891f0283dfbaee97317. Official[release](qualification/release.json) says “NautilusTrader1.231.0 Beta” with prerelease=false. No v2 APIs or exchange live factory/client used. Source remains unmodified under ignored work/ for inspection.

[Native market/reduce-only output](qualification/native-market.json): buy0.100 at60000.1, reduce0.040 at60999.9, reduce0.060 at58999.9, all native fills. PricePnL=(60999.9−60000.1)*0.04+(58999.9−60000.1)*0.06=−20.02000000. Native fees2.40000400+0.97599840+1.41599760=4.79200000. Native margin account total9975.18800000 and flat native position exactly reconcile10000−20.02−4.792. Side slippage is adverse1 tick each.

[Native conditional OCO output](qualification/native-oco.json): entry60000.1, native MARKET_IF_TOUCHED TP sell61499.9 on synthetic61500 gap; fee2.40000400+2.45999600=4.86000000; gross149.98000000; cash10145.12000000. Native STOP_MARKET sibling canceled; later58000 tick does not create another exit. Thus this is actual native matching/position/accounting, not import-only or a custom fill simulator.

Inspected exact v1 sources: backtest/engine.pyx add_venue/use_reduce_only/use_message_queue=False, run(streaming=True), native L1 FillModel adverse ticks; common/factories.pyx and model/orders constructors for STOP_MARKET/MARKET_IF_TOUCHED/OrderList/OCO; accounting/accounts/margin.pyx permits negative total while free may be0. Isolated allocation/liquidation/restart remain application contracts requiring integration tests, not qualified by these probes alone. Native Position average fields use internal floats; the product must not store those as a float ledger: authoritative native Money/Price/Quantity as Decimal strings, independently reconciled actual fills.

The throwaway latest dependency resolution selected pandas3.1.0, which emits an upstream Pandas4Warning about Timestamp.utcnow. It did not prevent native fills; record it rather than claim warning-free upstream. Production will lock exact transitive artifacts and retain this compatibility observation. No library patch is made.

All prices/outcomes are synthetic local qualification scenarios, not exchange data or real earnings. Existing fapi/Bybit blocks and PHASE2A incomplete status are unchanged.

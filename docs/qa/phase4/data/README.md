# Actual Binance spot historical observations — short functional sample

**PASS: collection and structural quality only. Not profitability, live-readiness or perpetual-data validation.** BTCUSDT and ETHUSDT are independent research accounts, not a shared portfolio. Shared multi-instrument native portfolio replay is UNRUN. Six Phase2A restricted markets remain BLOCKED.

Approved fixed public endpoint `https://data-api.binance.vision/api/v3/klines`; exactly one bounded GET/body0 per symbol, no retry/redirect/pagination. Requested half-open period2026-09-30T00:00:00Z→2026-10-30T00:00:00Z,1h,720closed bars each. Existing proxy/CA/TLS unchanged, fapi/Bybit preblocks retained. No restricted-host requests. [Collection intent](collection-intent.json), [CLI result](collection-result.json).

| Symbol | Actual collected UTC (KST) | HTTP | Dataset SHA256 | Raw-body SHA256 |
|---|---|---|---|---|
| BTCUSDT |2026-10-31T01:13:20.641337Z (10:13:20KST)|200|1162539322edfac678c2bb5a2e9efe619b861aabfd109f72e091eebd1ef9682d|dfaaee862ffd27dc2026c6e0777100c0a85b932c12422e9b2dbe0f8624dec3a0|
| ETHUSDT |2026-10-31T01:13:21.990923Z (10:13:21KST)|200|3df4c61979b74f1585bf37710f7267b15ba208e8b529b0886fc8079ef843b476|788a560b2232e00ee98b82396f627f714cd0f424527c0292b978f2ff3ae2fc1f|

Hash-named JSON contains the original raw response text (with real public prices/times), normalized OHLCV, exact request/source/period/actual collection metadata, and canonical content ID. Quality requires expected count, strict order/no gaps/duplicates, aligned close boundaries, fully closed/nonfuture UTC period, OHLC/tick/Decimal consistency, and raw/normalized checksum equivalence. SHA validates content integrity against a retained ID, not cryptographic exchange authorship. Public access does not grant a redistribution license: retain this limited attributed research observation; no exchange endorsement or legal warranty is claimed.

Collection code checkpointd0ba99b2ed1b0d9bf785c9755c0ea041ca5814f3 passed offline19collector/data tests and full338Python regression before collection. These offline tests are not live connectivity evidence; HTTP200/raw price files above are the separate observed collection evidence. Synthetic PAPER fixtures never substitute for these observations or for unavailable real perpetual history.

# E1 constructor revalidation ruling

Root technical ruling, 2026-11-03. The adopted E1 report required ONE bounded A0 decode. Actual immutable BybitRiskLimitEnvelopeReadV1.__post_init__ unconditionally revalidates the original raw body using the existing A0 decoder. Pre-construction extraction of exact time and fixed provider/region errors also needs that decoder. No existing decoded-input constructor seam exists.

Adopt a maximum of TWO invocations of the SAME existing bounded A0 decoder on the SAME original bytes for accepted risk pages: one bounded preflight for exact time/provider errors, then the DTO's mandatory independent constructor validation. No legacy JSON decoder, second implementation, duplicate GET, body reconstruction, object.__new__ validation bypass, monkeypatch, parser caching/global state or A0 change is allowed. Rejected pages may stop after the first parse. The raw SHA/NUMBER spellings are unchanged; physical request remains one per attempt.

Reason: preserve the frozen A0 contract and its constructor invariant while implementing the real route. Cost is a second bounded parse of at most65,536 bytes, not a new source/authority. Tests assert the actual bounded two-call accepted path and absence of legacy parsing. The earlier ONE-invocation statement is superseded only here; all other E1 requirements remain.

This is an inspectable implementation adjustment subject to independent source review. E1 remains raw-only; E2/current registry and full product gates remain pending.

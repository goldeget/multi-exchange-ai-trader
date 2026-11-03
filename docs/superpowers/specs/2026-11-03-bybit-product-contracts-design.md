# Bybit product identity and permission metadata contracts

## Goal, current baseline and completion boundary

Baseline accepted main94779a440d0fdddd804a926b46838c12db2443f4, tree4ef1c8ff157f1f7d060809da297f9bfe300d0527, PR27 CI119/main120 passed. The completed Haiku boundary acceptance is not full trading qualification. This slice implements only E16 draft T01/T02: pure product/scope values and credential permission metadata. No existing execution/native/settings/worker/qualification caller changes. Existing LINEAR properties and account/environment single-writer ownership retain their meanings. SPOT trading, UI selection, catalog/risk/protection/history/restart integration and actual DEMO/server acceptance remain later work.

User authority: continue the canonical63 in order, preserve SpotTrade, explicit SPOT/LINEAR choice, independent implementation/review/verification and passing-main merge. Do not repeat completed PR20~27. No further paid AI, actual orders, private/operator data or new server operation in this slice. LIVE remains forbidden. Existing linked worktree only; root owns Git. Values/fixtures/reports are not source attestation or execution capability.

The external full E16 design/plan, bybit-product-pure-preflight.md and bybit-product-primary-contracts.md were read against current source. This is the narrowed executable spec, not adoption of all proposed future APIs. Source-only/new-contract completion must not increment E16 to complete.

## Public product values

New stdlib-only root `trading_product.py`:

- `TradingProduct = Literal["SPOT", "LINEAR"]`.
- Frozen `InstrumentKey(venue: Literal["BYBIT"], product: TradingProduct, symbol: str)`; every argument mandatory, exact types, no aliases/default/normalization.
- `bybit_category(product: TradingProduct) -> Literal["spot", "linear"]`: exact uppercase product to lowercase API category.
- `bybit_native_instrument_id(key: InstrumentKey) -> str`: exact key type, revalidate fields, form `<symbol>-<product>.BYBIT`. Same BTCUSDT has distinct SPOT/LINEAR IDs. This is string mapping, not listing, environment, support, margin or qualification evidence.

Symbol exact str `[A-Z0-9]{2,48}` fullmatch. This is a LOCAL technical bound matching the current app's LINEAR native-ID envelope, not a Bybit maximum or approved universe. Reject whitespace, suffix, separators, Unicode lookalikes, subclass/Enum and coercion. Do not impose a USDT suffix: Bybit category linear includes futures/USDC; the app's USDT-perpetual restriction must later be proven by catalog fields. Root product imports no execution/account/credentials/httpx/native modules and performs no I/O.

## Private product selection values

New `execution/product_scope.py` imports only root product and existing pure ProtectionScope. Export constant `BYBIT_PERMISSION_PROFILE_REVISION = "BYBIT_SPOT_LINEAR_NO_FUNDS_V1"` for policy/evaluator agreement without a dependency on identity/parser modules.

- Frozen `ProductScope(account: ProtectionScope, product: TradingProduct)` retains the same account object; exact type and revalidate nested fields. account_id exact str length1..256, no edge whitespace or C0/DEL controls. Environment exact str DEMO/LIVE. No UID inference, trimming or case folding. LIVE representation is not effect authority.
- Frozen `ProductExecutionPolicy(scope: ProductScope, revision: str, allowed_products: frozenset[TradingProduct], selected_product: TradingProduct, permission_profile_revision: str)`. Exact scope and nested values; exact frozenset of1..2 exact products; selected is explicit, belongs to allowed, and equals scope.product. revision exact ASCII `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}`; permission profile exact supported constant. No None/default selection and no mutable conversion. Revalidate nested values at consuming helper/constructor boundaries, including forged/mutated frozen instances.

These are consistency values, not grants or admission. They do not acquire locks. Do not add product to AccountWorkerLock's account/environment key.

## Permission observation and fixed local profile

New `execution/product_permissions.py`:

`CredentialPermissionProfile(revision: str, granted_products: frozenset[TradingProduct], forbidden_grants: tuple[tuple[str,str],...], metadata_complete: bool, blockers: tuple[str,...])` is frozen and contains only fixed safe schema names/codes. No identity, UID, keys, timestamps, transport or raw metadata fields, and no method/property that issues execution/binding/configuration.

`evaluate_bybit_permission_profile(identity: BybitAccountIdentity, *, profile_revision: str) -> CredentialPermissionProfile` supports only the exact revision above. Unsupported/non-exact-str revision raises fixed safe ValueError before inspecting identity. Wrong exact identity type raises another fixed safe ValueError. No error includes its input. Internal annotation uses TYPE_CHECKING and postponed annotations; lazily import the exact existing identity and `_PERMISSIONS` inside evaluation/shared schema checking. Reuse the current known response schema without mutating/copying identity or duplicating its whole catalog. This may load the existing Python account_binding graph on first evaluation; normal source-module import is allowed, while application credential/config/environment/store/data/clock I/O, client/session construction, native SDK/loader and network operations are forbidden. Module import itself stays light. Do not extract/refactor existing schema/identity in this slice.

Before dict/set conversion, check exact read_only bool and original exact nonempty permission tuple bounded by the known schema group count; every entry exact2tuple, group exact known str, grants exact tuple bounded by that group's schema, every grant exact known str, no duplicate group/grant. Reject subclasses/objects before membership/hash/format hooks. Noncanonical ordering of valid tuples is accepted without modifying input. Unknown empty group is invalid. Unknown values are NEVER echoed, truncated, hashed, represented or placed in result/errors.

Malformed read_only -> empty products/forbidden, completeFalse, blocker READ_ONLY_METADATA_INVALID. Malformed structure/duplicates -> same with PERMISSION_METADATA_INVALID. Unknown exact group/grant -> same with UNKNOWN_PERMISSION_GROUP or UNKNOWN_PERMISSION_GRANT. These are complete blocked diagnostic results, not partially validated products. UID/parent/master/timestamp/transport are not used to infer authenticity/freshness.

For a fully structurally valid tuple, granted_products is the OBSERVED local classification of recognized trade grants, even if other blockers apply:

- SPOT iff Spot/SpotTrade present.
- LINEAR iff ContractTrade/Order AND ContractTrade/Position present.
- Derivatives/DerivativesTrade is permitted metadata but alone does not supply LINEAR.
- Four allowed pairs are Order, Position, DerivativesTrade and SpotTrade in their exact groups. All other known nonempty grants are forbidden under this LOCAL profile. Known empty groups are not grants.
- Explicit Wallet group is required; absence means metadata_completeFalse with WALLET_PERMISSION_METADATA_MISSING. Valid shape + explicit Wallet + bool means completeTrue, not execution eligibility. Wallet grants must be empty or listed as known forbidden pairs.
- read_onlyTrue adds READ_ONLY; any forbidden pairs add FORBIDDEN_PERMISSION_GRANT; no observed supported product adds NO_SUPPORTED_PRODUCT_GRANT. Blockers are unique in that exact order: READ_ONLY, WALLET_PERMISSION_METADATA_MISSING, FORBIDDEN_PERMISSION_GRANT, NO_SUPPORTED_PRODUCT_GRANT. All unknown/malformed blockers above are single-code results. forbidden_grants is a sorted unique tuple of known disallowed pairs only.

Result constructor validates exact immutable containers/types, fixed revision/products/blockers/known forbidden pairs without exposing bad input. For invalid-metadata codes require singleton code, empty products/forbidden and completeFalse. Otherwise enforce Wallet-missing iff incomplete, forbidden blocker iff forbidden tuple nonempty, no-product blocker iff products empty, and canonical blocker order. READ_ONLY has no source authority; a manually created coherent result remains a caller-created observation.

Examples: dual+Walletempty+false -> {SPOT,LINEAR}/completeTrue/no blockers; dual+readonly -> same observed set/True/READ_ONLY; dual+missingWallet -> same set/False/WALLET missing; dual+Withdraw -> same set/True/known forbidden pair+blocker. Spot-only and LINEAR-only need only their own trade grants. Derivatives-only -> empty/True/NO_SUPPORTED_PRODUCT_GRANT. Never infer absent venue rights from a blocked local profile. Future issuing-session admission must consume all required profile/source/policy gates, not grant set alone.

## Primary evidence and limits

Bybit's [API key info](https://bybit-exchange.github.io/docs/v5/user/apikey-info) names the grant groups and readOnly wire integer0/1; the existing parser normalizes to bool. Explicit Wallet presence, Order+Position and strict unknown handling are local conservative rules, not official endpoint eligibility theorems. [Category enum](https://bybit-exchange.github.io/docs/v5/enum#category) is broader than USDT perpetual.

Pinned [enums](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/v1.231.0/crates/adapters/bybit/src/common/enums.rs), [symbol](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/v1.231.0/crates/adapters/bybit/src/common/symbol.rs), [parse](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/v1.231.0/crates/adapters/bybit/src/common/parse.rs) support category/suffix/BYBIT string mapping. Do not reuse upstream loose normalization/suffix matching as this app's strict validator. No installed-wheel/wire/native/venue acceptance follows from static source.

## Acceptance

Two sequential tasks, each behavioral RED with minimal importable stubs if needed, focused GREEN and separate Ruff/mypy receipts, fresh dual task review. No collection/import error counts as behavioral RED. Whole review and exact hosted CI including existing native36/restart6/restored12 unskipped; main merge/tree/readback/main CI. No new native runtime suite needed for a pure unused-contract slice. No actual store/UID/key/env/note/trialDB/provider/browser/WSL/native/order/AI/server action. All existing blockers/STOPPED/monitor-only remain, E16/full63 stays incomplete. Preserve original/ignored evidence and existing worktree.

# A07 pure held-domain root technical adoption — 2026-11-03

Root read complete SPEC99lines16836B/SHA8d1caa496bf8e18fa1278f972f1eca53177f684e8c90a0330aecfa767459ff17 and PLAN63lines11946B/SHAec4e3747878075b955958d2418741e22a1c7003f410883396131c7749d925c81. Their original bytes remain unchanged.

Adopted for fresh independent design review, not code authorization yet:
1. Required explicit source_record_sha256s tuple parameter, 1..1024 ascending unique exact lowercase hashes, existing65536-byte canonical cap independently applies. This commits supplied VALUES only; actual issuer owns authentic registry-record correspondence/completeness. No known-input reconstruction or hash-domain conflation.
2. Exact original13-fieldMember/10-fieldCoverage DTOs and fixed canonical member/coverage payload recipes, supplied-input builder checks, hash-versus-builder limits.
3. Two NEW pure files execution/risk_domain_v2.py and tests/execution/test_risk_domain_v2.py, before E05-M2-D/runtimeTask13, after independent adopted-design gate.
4. Catalog reconstruction uses the supplied catalog's own exact timedelta max_age field, not a fabricated or policy-derived replacement. Revalidate all original fields/canonical order/schema/revision/expiry before cross-comparisons. Public build_instrument_catalog is allowed with genuine observations/declared_universe/now/catalog.max_age; do not call require_catalog_selection using synthesized approval inputs.
5. Actual producer still calls require_catalog_selection with genuine approved allowed/selected/product/environment/revision. Full risk-key union includes held+pending+proposed-key and receives no proposed quantity; no user-X leverage filter on heldY.
6. Existing source/math/canonical helpers remain read-only. Stable task4 tier helper is the required dependency, not whole current evidence-file bytes after later append-only Tasks5/6; root must preserve/review integration before actual downstream adoption. No reverse model/runtime import or private receipt/authority scaffold.
7. Existing nested fixed error codes remain. New errors follow candidate vocabulary. A02 CatalogRejected becomes fixed INPUT_INVALID as the proposed boundary says, including its resource rejection; direct new-module technical bound checks use RESOURCE_LIMIT. Never include source/provider values in errors.
8. Honest scope: builder verifies supplied bodies/totals/current visible timestamps; pure hash lacks account/reservation/catalog/now and cannot reestablish their completeness/freshness. Both lack actual native/private/issuer authenticity. Unsupported account/shared-debt/activity and current-generation/fees/leverage/DEMO/PC-off remain actual gates.

No financial/operator choice/default/order/AI/server spending is introduced. Code/DB/SDK/network/source calls not performed for this adoption. Root will act on concrete scoped design findings before source write gate.


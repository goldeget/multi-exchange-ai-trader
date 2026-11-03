# E05-M1 root adoption and Risk V2 contract amendment — 2026-11-03

Status: ADOPTED TECHNICAL DESIGN; PURE IMPLEMENTATION AUTHORIZED; ACTUAL MODEL NOT QUALIFIED. This is not a source receipt, a completed M2/M3 result or product acceptance.

The user's continuing authority covers the full63 implementation, independent review, tests and passing main merges. Root resolves the technical choices below without another questionnaire. No LIVE/new paid AI/new server spending is authorized by this document. Previously authorized DEMO qualification remains subject to a concrete same-writer experiment review; no experiment is executed here. Existing source work, policy migration and Risk V1 are preserved.

## Evidence and approaches

Root read the entire M1 preparation (204 lines, SHA256 `6462210d0fc91f9ec129f0666aa02ccdeaa787367d8241b7162147bed041a2a7`) and fresh independent preflight (232 lines, SHA256 `54245b8c4080316a21f197b4c2827bc4e9ced164d894d65078caa5d34a5421f7`). Root separately opened the current official [isolated margin calculations](https://www.bybit.com/en/help-center/article/Margin-Calculations-Under-Different-Margin-Modes), updated 2026-10-22, and [UTA liquidation calculations](https://www.bybit.com/en/help-center/article/UTA-Trading-Rules-Liquidation-Process), updated 2026-10-15, and read their formula sections. These public explanations are not observations of the selected DEMO account. The earlier entry-based migration explanation remains conflicting historical evidence; it is not overwritten.

Keeping a positive-Decimal-only liquidation field would lose nonterminating roots and the legitimate L1 long no-positive-root case. Converting all financial DTOs and durable amounts into arbitrary ratios would expand the change unnecessarily. Root adopts exact ratio liquidation outcomes plus a deterministic conservative app ceiling for finite monetary reservations. This is the smallest change that preserves the existing exact-finance and smallest-feasible-leverage purposes. The choice is root's technical call, not a user-selected venue rule or financial budget.

## Binding amendments to Risk R2

These amendments supersede only the conflicting liquidation/margin representation and model-basis portions of the retained R2 design/plan. The original documents remain intact. Task0 and Task1's already-listed core DTOs/signatures are unchanged. Task4/6 and E05-M2 must consume this amendment before implementation. V1 contracts and behavior remain unchanged.

### Exact price and typed outcomes

`ExactRiskRatioV2` has exactly `numerator: Decimal` and `denominator: Decimal`. Both are exact finite canonical derived integer tuples with exponent0 and positive sign; numerator is nonnegative, denominator strictly positive, gcd=1. Zero is `(0,1)`. A positive price requires numerator>0. Equivalent noncanonical spellings (`100.0`, `1E+2`, negative zero) are rejected at value validation; builders produce canonical tuples with the reviewed math0 helpers. Source Decimal spelling elsewhere remains untouched. Derived work/encoded-size bounds apply, not the narrower source256 bound. Financial comparisons convert to exact Fraction and never use display values.

`LiquidationPositiveRootV2` has exactly: `kind: Literal["POSITIVE_ROOT"]`, `exact_price: ExactRiskRatioV2`, `candidate_tier_id: str`, `domain_upper_quote: Decimal`, `full_table_sha256: str`, `raw_manifest_sha256: str`, `consumed_tier_ids: tuple[str,...]`, `rule_revision: str`.

`LiquidationNoPositiveRootV2` has exactly: `kind: Literal["NO_POSITIVE_ROOT"]`, `reason: Literal["NON_POSITIVE_LONG_INTERCEPT"]`, `domain_upper_quote: Decimal`, `full_table_sha256: str`, `raw_manifest_sha256: str`, `consumed_tier_ids: tuple[str,...]`, `rule_revision: str`, `model_input_sha256: str`, `proof_sha256: str`.

`LinearLiquidationAssessmentV2.liquidation_price` is replaced by `outcome: LiquidationPositiveRootV2 | LiquidationNoPositiveRootV2`. Add common `model_input_sha256: str` and `exact_calculation_sha256: str`. Retain every other R2 field including `covered_tier_ids` (admission-covered IDs), `reference.input_sha256==margin_assessment_sha256`, the source-to-completion DAG and validity. Canonical digests include all new fields. The no-root branch must match the common model input hash and is rejected immediately for SHORT/CROSS/non-ONE_WAY. Positive candidate ID belongs to consumed IDs; admission-covered IDs must be included among full consumed IDs. The actual issuer resolves the full captured table/trace/proof and validates exact contents, not merely these labels.

The successful assessment union does not include Unsupported. Missing/unsupported/inconsistent/out-of-table calculations return a closed rejected model result and cannot produce an admissible assessment. Reuse existing RiskV2Rejected codes for public failures (`RISK_V2_INPUT_INVALID`, existing unsupported/model/buffer codes as appropriate); do not invent a second exception framework or silently expand the frozen whitelist. A model's internal unsupported reasons are explicit closed literals, not authority.

For positive outcomes Task6 uses exact LP and the bound stop T: LONG gap=T−LP; SHORT gap=LP−T. Require gap>0 and gap>=policy.liquidation_buffer_fraction*entry_limit. No tick guard or rounded financial LP is needed in this chosen representation. For a verified no-positive-root outcome, record `MODEL_NO_POSITIVE_TRIGGER` rather than manufacture a positive LP. Add `liquidation_buffer_result: Literal["GAP_SATISFIED","MODEL_NO_POSITIVE_TRIGGER"] | None` to `LinearEntryAssessmentV2`; None is permitted only where PLAN has no accepted liquidation evidence. Both branches remain subject to all margin/capacity/source/model/readback/binding gates. No-root means no positive trigger within the captured named model domain; it is not a venue guarantee against funding, fees, ADL, liquidation or future state changes.

### Finite reservation bridge

Retain `LinearMarginAssessmentV2.initial_margin_quote` and `venue_required_total_reserve_quote` as finite Decimal fields. The first is fee-exclusive point-model core envelope, and the second is a model point reserve, never a measured venue upper bound. Add exactly:

- `reserve_basis: Literal["POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"]`
- `reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"]`
- `reserve_quantum_usdt: Decimal`, exactly canonical `Decimal("0.00000001")`
- `model_input_sha256: str`
- `exact_calculation_sha256: str`

`RiskRequirementsV2` additionally pins `cost_assessment_basis: Literal["POINT_ESTIMATE_WITH_RECONCILIATION_V1","HARD_EXECUTION_FEE_UPPER_V1"]`, `margin_reserve_basis: Literal["POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"]`, `reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"]`, and `reserve_quantum_usdt: Decimal` with the same exact value. Requirements/model/assessment digests and actual issuer must agree. The concrete E05 model supports only the point-estimate cost basis initially; a hard-fee-upper requirement remains unsupported until separately proved. No synthetic upper receipt is issued.

For exact model outputs I,V and bound finite planned allowance C, let q=1e-8 USDT and `ceil_q(x)=ceil(x/q)*q` by exact integer arithmetic. Set `I_out=ceil_q(I)`, `V_out=ceil_q(V)`, `R_out=max(I_out+C,V_out)`. Capacity and smallest-feasible search use R_out. Independently prove I_out>=I,V_out>=V,R_out>=max(I+C,V). Do not add hidden final rounding. C and therefore R_out need not be multiples of q; the durable finite-Decimal reservation stores R_out exactly. q is an app output granularity, not venue internal precision, a capital choice or a bound on future actual cost. No ambient Decimal arithmetic or display CEILING80 enters these predicates.

## Concrete E05 model scope and calculations

First supported profile is USDT LinearPerpetual, UTA2.0 status5/6, ISOLATED, ONE_WAY/positionIdx0, selected symbol flat with no selected open/conditional/unresolved entry activity, actual AMR off, known source/mode/fees/settings and complete held-domain coverage. Other held symbols Y remain in the whole-account risk, margin, quantities and count. Borrowing, unknown extra margin, MNT fee deduction, delta-neutral/price adjustment, selected add-on/netting, CROSS/HEDGE/portfolio and any missing source profile remain unsupported. An unsupported new ENTRY does not disable separately lawful owned reduction EXIT.

Adopt named model basis `POINT_ESTIMATE_TWO_IM_ENVELOPE_V1`. Require Q>0 and P,M>0 without requiring P=M: P is exact submitted limit, M is captured mark, prospective E=P. For L>=1, actual taker rate f and independently applicable order close-reserve rate f0, use bLong=1−1/L,bShort=1+1/L:

`orderIM=QP/L; open=QP*f; orderClose=QP*b*f0; orderCost=orderIM+open+orderClose; positionClose=QP*b*f; I=max(QM/L,QP/L); V=max(orderCost,QM/L+positionClose+open,QP/L+positionClose+open)`.

Adopt additional conservative feasibility predicate `L*IMR_i<=1` for every admission-covered row, together with the actual maxLeverage interval intersection. This is a local model condition, not a claim that venue maxLeverage always equals 1/IMR. Preserve independent rates/deduction and raw numeric spelling.

Use current official LP equations per full table row, without re-deriving them from displayed IM: LONG `[QE−QE/L−A/(1−f)−D_i]/[Q(1−m_i)]`; SHORT `[QE+QE/L+A/(1+f)+D_i]/[Q(1+m_i)]`. Initial new-entry A=0 must be an explicit supported-profile fact, not a held-position default. Validate first lower=0, observed first D=0, contiguous strict intervals, unique IDs, 0<MMR<1, MMR<=IMR<=1, nonnegative D, and exact continuity recurrence. No monotonic rate/maxLeverage assumption. Check every captured row and retain the unique self-consistent positive root with `lower_i<Q*LP_i<=upper_i`; no extrapolation. A LONG nonpositive intercept on that validated domain produces the tagged proof. The trace binds raw page manifest, full normalized table, all consumed rows and proof domain independently of admission-covered IDs.

Search permitted local grid from the smallest L and evaluate the complete economic predicate, retaining each failed prefix. A rounded R_out capacity failure, proved IMR failure or exact buffer failure can advance L. Unsupported source/proof/representation/resource is not an economic failure and ends the search unsupported; do not skip L1 because the DTO cannot express its result. Local zero-origin rule remains explicitly local until actual source/control/readback acceptance.

SELL LIMIT is a floor and BUY LIMIT a ceiling. Root adopts the practical point-estimate-with-reconciliation product contract for model development in both directions; it does not relax `HARD_EXECUTION_QUOTE_CAP`, any separately active hard fee upper or immutable limit ownership. SHORT with a hard execution cap remains unsupported by the floor alone. Actual LONG and SHORT support each require their own M3 acceptance. Actual partial fills must revalue Q/E/cost/margin/current protection and invoke the reviewed same-owner response on mismatch; estimate is not a hard pre-execution bound.

## Follow-on implementation order and acceptance

1. Generic Risk Tasks1–7 continue in isolated scope from reviewed math0/A02 definitions. Task1's current DTOs remain unchanged. Task4/6 consume the typed amendment above before code. Independent review follows each task.
2. Implement pure full-table/raw-number source DTO/parser and exact model/selector/bridge with independent F1–F7 plus boundary, forged, stale, mismatch and ambient-context tests. Existing V1 decoder and returned snapshot remain unchanged.
3. Add a separate opt-in risk-limit envelope route to the SAME owned client/send/retry/budget, preserving identity UTF8 body, original NUMBER spelling, pagination manifest and bounded safe errors. No duplicate GET/new HTTP client/REST order writer.
4. Bind actual source registry/current model/whole-account held coverage and same native control/readback path. Complete native offline exact serialization/protection tests, local and hosted gates before considering effects.
5. Root reviews a concrete minimal DEMO qualification experiment: exact UID/host/lease/native instance, target flat/all orders, source capabilities/settings/fees/leverage, minimum feasible Q within explicit virtual-probe bounds, protection/cleanup/lifetime/attempts/UNKNOWN rules and a predeclared comparison verdict. No global qualified flag, restored receipt or alternate writer may bypass this review. First fill/LP/child observations are outputs, not circular prerequisites. Missing field/support and inconclusive algorithm comparison remain explicit results. Only a separately reviewed bootstrap profile can name uncertainty to be checked immediately after the first fill.
6. Run and independently reconcile approved BUY probe, residual cleanup/history/restart, then separately qualify SHORT within the adopted point-estimate contract. Full-auto supported profile is promoted only for the actual UID/host/mode/native/source/model/side and evidence observed. PC-off server operation remains a later real deployment/acceptance gate with no additional server expense inferred.

## Root rulings and consequences

45. Adopt the named two-IM point envelope, independent IMR predicate and full captured-table LP scenario. Cost if wrong: conservative rejections or model/actual mismatch; latest documentation alone does not qualify the DEMO account, and M3 must keep PASS/FAIL/INCONCLUSIVE distinct.
46. Adopt tagged positive/no-positive model outcomes with canonical exact ratio price, full-table/trace binding and explicit no-positive buffer result. Cost if wrong: codec/validator complexity and later actual proof resolution; no numeric fake, L1 skip or perpetual safety claim is allowed.
47. Adopt root-fixed app q=1e-8 USDT ceiling with named basis/revision and R_out-based feasibility, retaining exact C/R_out. Cost if wrong: a technically conservative tiny reserve increase can change selected L at capacity equality; it is auditable and must not be called venue precision or actual future upper.
48. Adopt point-estimate-with-reconciliation for practical model development and side-specific actual qualification; keep hard cap/fee predicates separate. Cost if wrong: favorable SELL fills and later fees/marks can exceed the estimate, requiring actual post-fill checks/owned response; no hard-bound promise or automatic SHORT qualification follows.
49. Reuse one client/send/budget for raw numeric source projection and distinguish probe authority from full-auto qualification. Cost if wrong: implementation/current-source complexity and bounded experiment uncertainty; no alternate writer, global bypass or circular first-observation prerequisite is acceptable.

No source implementation, private/API/native query, order, paid AI or server action is performed by this design adoption itself. Full63 remains active.

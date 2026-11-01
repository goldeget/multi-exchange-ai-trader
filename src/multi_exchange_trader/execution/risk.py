"""Pure, explicit linear-contract entry checks; this module has no venue authority.

Qualified callers must supply complete venue/account evidence and conservative per-unit
round-trip costs covering the intended holding/stop scenario. This function cannot qualify
an adapter, an accounting ledger, a liquidation model, or a user-settings write path.
PLAN is a calculation for a subsequent leverage application/readback sequence, never an
entry authorization. SUBMIT is a fresh check, still requiring the coordinator's atomic
reservation, ownership, protection-capability and single-path gateway gates.
"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, DecimalException, localcontext
from typing import get_type_hints


class RiskRejected(ValueError):
    """A fail-closed assessment result with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class RiskScope:
    account_id: str
    environment: str
    symbol: str
    strategy_id: str


@dataclass(frozen=True)
class UserRiskPolicy:
    scope: RiskScope
    revision: str
    sizing_rule: str
    leverage_min: Decimal
    leverage_max: Decimal
    per_trade_loss: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_consecutive_losses: int
    max_positions: int
    max_total_notional: Decimal
    max_total_risk: Decimal
    max_total_margin: Decimal
    margin_allocation: Decimal
    margin_mode: str
    position_mode: str
    liquidation_buffer_fraction: Decimal
    max_quote_age_seconds: Decimal
    max_evidence_age_seconds: Decimal


@dataclass(frozen=True)
class EntryProposal:
    side: str
    stop_price: Decimal


@dataclass(frozen=True)
class MarketEvidence:
    scope: RiskScope
    quote_id: str
    observed_at: datetime
    qualified: bool
    entry_price: Decimal
    mark_price: Decimal
    fee_per_unit: Decimal
    funding_per_unit: Decimal
    slippage_per_unit: Decimal
    funding_rate: Decimal
    next_funding_at: datetime
    costs_valid_until: datetime


@dataclass(frozen=True)
class InstrumentEvidence:
    scope: RiskScope
    revision: str
    observed_at: datetime
    qualified: bool
    quantity_step: Decimal
    price_tick: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    max_notional: Decimal
    leverage_step: Decimal


@dataclass(frozen=True)
class RiskTier:
    """Qualified ranges are (min_notional, max_notional], contiguous from zero."""

    tier_id: str
    min_notional: Decimal
    max_notional: Decimal
    min_leverage: Decimal
    max_leverage: Decimal
    maintenance_margin_rate: Decimal


@dataclass(frozen=True)
class TierEvidence:
    scope: RiskScope
    revision: str
    observed_at: datetime
    complete: bool
    qualified: bool
    tiers: tuple[RiskTier, ...]


@dataclass(frozen=True)
class AccountEvidence:
    """Account-wide gross counters include all strategies and external activity.

    available_margin excludes already committed account margin but not reservations.
    total_risk is nonnegative remaining worst-case stop risk including costs. Daily
    loss is nonnegative net realized loss (after fees/funding, no unrealized credit),
    measured from 00:00 UTC; drawdown is the ledger's nonnegative equity drawdown.
    A qualified ledger must provide these definitions and current reconciliation.
    """

    scope: RiskScope
    snapshot_id: str
    observed_at: datetime
    complete: bool
    ledger_qualified: bool
    loss_accounting: str
    loss_day: date
    equity: Decimal
    available_margin: Decimal
    total_notional: Decimal
    symbol_notional: Decimal
    total_risk: Decimal
    total_margin: Decimal
    position_count: int
    daily_loss: Decimal
    drawdown: Decimal
    consecutive_losses: int
    margin_mode: str
    position_mode: str
    external_same_symbol_orders: bool
    external_same_symbol_position: bool


@dataclass(frozen=True)
class ReservationTotals:
    """Account-wide pending/unresolved reservations, including other strategies.

    These must be disjoint from account counters: do not count an acknowledged but
    unresolved command in neither set. The caller must reserve atomically after
    assessment; this snapshot alone supplies no concurrency guarantee.
    """

    scope: RiskScope
    snapshot_id: str
    observed_at: datetime
    complete: bool
    notional_amount: Decimal
    symbol_notional_amount: Decimal
    risk_amount: Decimal
    margin_amount: Decimal
    position_count: int


@dataclass(frozen=True)
class LiquidationEvidence:
    """Attestation from a separately qualified venue/model, never a local estimate.

    source identifies that qualification. Quantity is proposed entry quantity, while
    the qualified computation must include the bound account snapshot, same-symbol
    exposure/reservations and the selected risk tier. This record is not proof of
    qualification by itself: only the trusted adapter may construct it for dispatch.
    """

    scope: RiskScope
    observed_at: datetime
    qualified: bool
    source: str
    account_snapshot_id: str
    reservation_snapshot_id: str
    quote_id: str
    policy_revision: str
    instrument_revision: str
    tier_revision: str
    tier_id: str
    quantity: Decimal
    leverage: Decimal
    side: str
    entry_price: Decimal
    margin_mode: str
    position_mode: str
    liquidation_price: Decimal


@dataclass(frozen=True)
class LeverageReadback:
    """Actual applied leverage from qualified independent venue readback, not ACK."""

    scope: RiskScope
    observed_at: datetime
    qualified: bool
    account_snapshot_id: str
    policy_revision: str
    applied_leverage: Decimal
    side: str
    margin_mode: str
    position_mode: str


@dataclass(frozen=True)
class EntryDecision:
    scope: RiskScope
    assessed_at: datetime
    stage: str
    quantity: Decimal
    notional: Decimal
    estimated_loss: Decimal
    estimated_loss_fraction: Decimal
    cost_allowance: Decimal
    funding_allowance: Decimal
    required_leverage: Decimal
    leverage: Decimal
    initial_margin: Decimal
    margin_amount: Decimal
    tier_id: str
    venue_leverage_min: Decimal
    venue_leverage_max: Decimal
    clamp_reasons: tuple[str, ...]
    stop_distance_fraction: Decimal
    liquidation_price: Decimal | None
    liquidation_distance_fraction: Decimal | None
    liquidation_buffer_fraction: Decimal | None
    submission_ready: bool
    blockers: tuple[str, ...]
    formula_version: str
    policy: UserRiskPolicy
    proposal: EntryProposal
    market: MarketEvidence
    instrument: InstrumentEvidence
    account: AccountEvidence
    tiers: TierEvidence
    reservations: ReservationTotals
    liquidation: LiquidationEvidence | None
    leverage_readback: LeverageReadback | None


def _hints(cls: type[object]) -> dict[str, object]:
    return dict(get_type_hints(cls))


def _record(value: object, cls: type[object]) -> None:
    if type(value) is not cls or not is_dataclass(value):
        raise RiskRejected("INVALID_INPUT", f"expected explicit {cls.__name__}")
    for item in fields(value):
        actual = getattr(value, item.name)
        expected = _hints(cls)[item.name]
        label = f"{cls.__name__}.{item.name}"
        if expected is RiskScope:
            _record(actual, RiskScope)
        elif expected == tuple[RiskTier, ...]:
            if type(actual) is not tuple or not actual:
                raise RiskRejected("INVALID_INPUT", label)
            for tier in actual:
                _record(tier, RiskTier)
        elif expected is Decimal:
            if type(actual) is not Decimal or not actual.is_finite():
                raise RiskRejected("INVALID_INPUT", f"finite Decimal required: {label}")
            # Source records only: derived divisions may legitimately have more
            # digits. Reject pathological representations before _precision.
            if (len(actual.as_tuple().digits) > 256
                    or abs(int(actual.as_tuple().exponent)) > 1000
                    or (actual != 0 and abs(actual.adjusted()) > 1000)):
                raise RiskRejected("INVALID_INPUT", f"Decimal representation bounds: {label}")
        elif expected is datetime:
            _instant(actual, label)
        elif (type(actual) is not expected
              or expected is str and (not actual or actual != actual.strip())):
            raise RiskRejected("INVALID_INPUT", label)
    if isinstance(value, RiskScope) and value.environment not in {"DEMO", "LIVE"}:
        raise RiskRejected("INVALID_INPUT", "explicit DEMO/LIVE environment required")


def _instant(value: object, label: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise RiskRejected("INVALID_INPUT", f"aware datetime required: {label}")


def _fresh(observed_at: datetime, now: datetime, maximum: Decimal, label: str) -> None:
    delta = now.astimezone(UTC) - observed_at.astimezone(UTC)
    # No float timestamp conversion or implicit freshness tolerance.
    microseconds = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    age = Decimal(f"{microseconds}e-6")
    if age < 0:
        raise RiskRejected("FUTURE_EVIDENCE", label)
    if age > maximum:
        raise RiskRejected("STALE_EVIDENCE", label)


def _positive(value: Decimal | int, label: str, *, zero: bool = False) -> None:
    if value < 0 or (not zero and value == 0):
        raise RiskRejected("INVALID_INPUT", f"invalid range: {label}")


def _scope(actual: RiskScope, expected: RiskScope) -> None:
    if actual != expected:
        raise RiskRejected("SCOPE_MISMATCH", "account/environment/symbol/strategy must match")


def _validate(
    policy: UserRiskPolicy, proposal: EntryProposal, market: MarketEvidence,
    instrument: InstrumentEvidence, account: AccountEvidence, tiers: TierEvidence,
    reservations: ReservationTotals, now: datetime, stage: str,
) -> None:
    for value, cls in (
        (policy, UserRiskPolicy), (proposal, EntryProposal), (market, MarketEvidence),
        (instrument, InstrumentEvidence), (account, AccountEvidence), (tiers, TierEvidence),
        (reservations, ReservationTotals),
    ):
        _record(value, cls)
    _instant(now, "now")
    if type(stage) is not str or stage not in {"PLAN", "SUBMIT"}:
        raise RiskRejected("INVALID_INPUT", "stage must explicitly be PLAN or SUBMIT")
    for name in (
        "leverage_min", "leverage_max", "per_trade_loss", "max_daily_loss", "max_drawdown",
        "max_total_notional", "max_total_risk", "max_total_margin", "margin_allocation",
        "max_consecutive_losses", "max_positions",
    ):
        _positive(getattr(policy, name), f"policy.{name}")
    for name in (
        "liquidation_buffer_fraction", "max_quote_age_seconds", "max_evidence_age_seconds",
    ):
        _positive(getattr(policy, name), f"policy.{name}", zero=True)
    if policy.leverage_min > policy.leverage_max or policy.sizing_rule != "LOSS_FIXED":
        raise RiskRejected("INVALID_INPUT", "user range/sizing rule")
    if policy.margin_mode not in {"ISOLATED", "CROSS"}:
        raise RiskRejected("INVALID_INPUT", "user margin mode")
    if policy.position_mode not in {"ONE_WAY", "HEDGE"}:
        raise RiskRejected("INVALID_INPUT", "user position mode")
    if proposal.side not in {"LONG", "SHORT"}:
        raise RiskRejected("INVALID_INPUT", "entry side")
    for evidence in (market, instrument, account, tiers, reservations):
        _scope(evidence.scope, policy.scope)
        age = (policy.max_quote_age_seconds if isinstance(evidence, MarketEvidence)
               else policy.max_evidence_age_seconds)
        _fresh(evidence.observed_at, now, age, type(evidence).__name__)
    if not (account.complete and tiers.complete and reservations.complete):
        raise RiskRejected("INCOMPLETE_EVIDENCE", "account/tier/reservation snapshot")
    if not (market.qualified and instrument.qualified and tiers.qualified):
        raise RiskRejected("UNQUALIFIED_EVIDENCE", "market/instrument/tier source")
    if (not account.ledger_qualified or account.loss_day != now.astimezone(UTC).date()
            or account.loss_accounting != "UTC_REALIZED_NET_FEES_FUNDING"):
        raise RiskRejected("UNVERIFIED_LOSS_LEDGER", "qualified current UTC loss ledger required")
    if (account.margin_mode != policy.margin_mode or account.position_mode != policy.position_mode):
        raise RiskRejected("MODE_MISMATCH", "actual account modes differ from explicit user policy")
    if account.external_same_symbol_orders or account.external_same_symbol_position:
        raise RiskRejected("EXTERNAL_CONFLICT", "external activity must remain unmanaged")
    for name in ("entry_price", "mark_price"):
        _positive(getattr(market, name), f"market.{name}")
    for name in ("fee_per_unit", "funding_per_unit", "slippage_per_unit"):
        _positive(getattr(market, name), f"market.{name}", zero=True)
    _positive(proposal.stop_price, "stop_price")
    if (market.next_funding_at.astimezone(UTC) <= now.astimezone(UTC)
            or market.costs_valid_until.astimezone(UTC) < now.astimezone(UTC)):
        raise RiskRejected("STALE_COSTS", "current funding schedule and cost allowance required")
    for name in (
        "quantity_step", "price_tick", "min_quantity", "max_quantity", "max_notional",
        "leverage_step",
    ):
        _positive(getattr(instrument, name), f"instrument.{name}")
    _positive(instrument.min_notional, "min_notional", zero=True)
    if (instrument.min_quantity > instrument.max_quantity
            or instrument.min_notional > instrument.max_notional):
        raise RiskRejected("INVALID_INPUT", "instrument bounds")
    _positive(account.equity, "equity")
    for name in (
        "available_margin", "total_notional", "symbol_notional", "total_risk", "total_margin",
        "position_count", "daily_loss", "drawdown", "consecutive_losses",
    ):
        _positive(getattr(account, name), f"account.{name}", zero=True)
    for name in (
        "notional_amount", "symbol_notional_amount", "risk_amount", "margin_amount", "position_count",
    ):
        _positive(getattr(reservations, name), f"reservations.{name}", zero=True)
    if (account.symbol_notional > account.total_notional
            or reservations.symbol_notional_amount > reservations.notional_amount):
        raise RiskRejected("INVALID_INPUT", "symbol exposure exceeds account gross exposure")
    previous = Decimal(0)
    identifiers: set[str] = set()
    for tier in tiers.tiers:
        if (tier.min_notional != previous or tier.max_notional <= tier.min_notional
                or tier.tier_id in identifiers):
            raise RiskRejected("INVALID_INPUT", "risk tiers must be contiguous, ordered and unique")
        _positive(tier.min_leverage, "tier.min_leverage")
        _positive(tier.max_leverage, "tier.max_leverage")
        if (tier.min_leverage > tier.max_leverage
                or not Decimal(0) <= tier.maintenance_margin_rate < Decimal(1)):
            raise RiskRejected("INVALID_INPUT", "risk-tier leverage/maintenance bounds")
        previous = tier.max_notional
        identifiers.add(tier.tier_id)


def _precision(*records: object) -> int:
    """Keep all finite input digits for exact sums/products before rounded divisions."""
    precision = 80
    for record in records:
        if type(record) is Decimal:
            parts = record.as_tuple()
            precision += len(parts.digits) + abs(int(parts.exponent))
        elif is_dataclass(record) and not isinstance(record, type):
            precision += _precision(*(getattr(record, item.name) for item in fields(record)))
        elif type(record) is tuple:
            precision += _precision(*record)
    return precision


def assess_entry(
    *, policy: UserRiskPolicy, proposal: EntryProposal, market: MarketEvidence,
    instrument: InstrumentEvidence, account: AccountEvidence, tiers: TierEvidence,
    reservations: ReservationTotals, now: datetime, stage: str,
    liquidation: LiquidationEvidence | None, leverage_readback: LeverageReadback | None,
) -> EntryDecision:
    """Assess one proposed linear entry without network, side effects or defaults.

    Quantity = floor_to_step(loss_limit / (stop_distance + all per-unit costs)).
    Leverage is the smallest permitted grid value covering initial margin plus cost
    reserve within the explicit allocation, available margin, and aggregate cap.
    Raising leverage never increases quantity or excuses a failed loss/liquidation gate.
    The preserved records and formula version support an external append-only audit.
    """
    _validate(policy, proposal, market, instrument, account, tiers, reservations, now, stage)
    if liquidation is not None:
        _record(liquidation, LiquidationEvidence)
    if leverage_readback is not None:
        _record(leverage_readback, LeverageReadback)
    try:
        precision = _precision(policy, proposal, market, instrument, account, tiers,
                               reservations, liquidation, leverage_readback)
        with localcontext(Context(prec=precision)):
            return _calculate(policy, proposal, market, instrument, account, tiers, reservations,
                              now, stage, liquidation, leverage_readback)
    except DecimalException as exc:
        raise RiskRejected("INVALID_INPUT", "Decimal arithmetic cannot be represented safely") from exc


def _calculate(
    policy: UserRiskPolicy, proposal: EntryProposal, market: MarketEvidence,
    instrument: InstrumentEvidence, account: AccountEvidence, tiers: TierEvidence,
    reservations: ReservationTotals, now: datetime, stage: str,
    liquidation: LiquidationEvidence | None, leverage_readback: LeverageReadback | None,
) -> EntryDecision:
    entry = market.entry_price
    stop = proposal.stop_price
    if (entry % instrument.price_tick or stop % instrument.price_tick):
        raise RiskRejected("INVALID_PRICE_TICK", "entry and stop must match runtime price tick")
    distance = entry - stop if proposal.side == "LONG" else stop - entry
    if distance <= 0:
        raise RiskRejected("INVALID_STOP", "stop must be strictly adverse to entry")
    unit_cost = market.fee_per_unit + market.funding_per_unit + market.slippage_per_unit
    unit_loss = distance + unit_cost
    quantity = (policy.per_trade_loss / unit_loss / instrument.quantity_step).to_integral_value(
        rounding=ROUND_FLOOR,
    ) * instrument.quantity_step
    notional = quantity * entry
    if quantity < instrument.min_quantity or notional < instrument.min_notional:
        raise RiskRejected("MINIMUM_SIZE", "loss-fixed rounded-down quantity is below venue minimum")
    if quantity > instrument.max_quantity or notional > instrument.max_notional:
        raise RiskRejected("MAXIMUM_SIZE", "loss-fixed quantity exceeds runtime instrument maximum")
    loss = quantity * unit_loss
    costs = quantity * unit_cost
    if loss > policy.per_trade_loss:
        raise RiskRejected("TRADE_LOSS_LIMIT", "rounding must never exceed user loss budget")
    total_risk = account.total_risk + reservations.risk_amount + loss
    if total_risk > policy.max_total_risk:
        raise RiskRejected("TOTAL_RISK_LIMIT", "account plus reserved plus proposed stop risk")
    if account.daily_loss + total_risk > policy.max_daily_loss:
        raise RiskRejected("DAILY_LOSS_LIMIT", "UTC realized loss plus remaining/pending stop risk")
    if account.drawdown + total_risk > policy.max_drawdown:
        raise RiskRejected("DRAWDOWN_LIMIT", "ledger drawdown plus remaining/pending stop risk")
    if account.consecutive_losses >= policy.max_consecutive_losses:
        raise RiskRejected("CONSECUTIVE_LOSS_LIMIT", "explicit consecutive-loss stop reached")
    if account.position_count + reservations.position_count + 1 > policy.max_positions:
        raise RiskRejected("POSITION_LIMIT", "existing plus reserved plus proposed position count")
    if account.total_notional + reservations.notional_amount + notional > policy.max_total_notional:
        raise RiskRejected("EXPOSURE_LIMIT", "account gross exposure includes every reservation")
    tier_notional = account.symbol_notional + reservations.symbol_notional_amount + notional
    tier = next((item for item in tiers.tiers
                 if item.min_notional < tier_notional <= item.max_notional), None)
    if tier is None:
        raise RiskRejected("UNVERIFIED_RISK_TIER", "proposed total symbol size lacks a verified tier")
    capacity = min(
        policy.margin_allocation,
        account.available_margin - reservations.margin_amount,
        policy.max_total_margin - account.total_margin - reservations.margin_amount,
    )
    if capacity <= costs:
        raise RiskRejected("MARGIN_LIMIT", "available allocation cannot cover explicit cost reserve")
    required = notional / (capacity - costs)
    lower = max(policy.leverage_min, tier.min_leverage)
    upper = min(policy.leverage_max, tier.max_leverage)
    leverage = (max(required, lower) / instrument.leverage_step).to_integral_value(
        rounding=ROUND_CEILING,
    ) * instrument.leverage_step
    if lower > upper or leverage > upper:
        raise RiskRejected(
            "LEVERAGE_UNSATISFIABLE",
            f"required={required}; user=[{policy.leverage_min},{policy.leverage_max}]; "
            f"venue=[{tier.min_leverage},{tier.max_leverage}]; step={instrument.leverage_step}; "
            f"rounded={leverage}; available_margin_allocation={capacity}",
        )
    reasons: list[str] = []
    if required < policy.leverage_min:
        reasons.append("USER_MINIMUM")
    if required < tier.min_leverage:
        reasons.append("VENUE_MINIMUM")
    if leverage > max(required, lower):
        reasons.append("LEVERAGE_STEP_UP")
    initial_margin = notional / leverage
    margin = initial_margin + costs
    if margin > capacity:
        raise RiskRejected("MARGIN_LIMIT", "rounded leverage cannot cover available allocation")
    blockers: list[str] = []
    liquidation_price: Decimal | None = None
    liquidation_distance: Decimal | None = None
    liquidation_buffer: Decimal | None = None
    if liquidation is None:
        blockers.append("UNVERIFIED_LIQUIDATION")
    else:
        _scope(liquidation.scope, policy.scope)
        _fresh(liquidation.observed_at, now, policy.max_evidence_age_seconds, "liquidation")
        if (not liquidation.qualified or liquidation.quantity != quantity
                or liquidation.leverage != leverage or liquidation.side != proposal.side
                or liquidation.entry_price != entry or liquidation.margin_mode != policy.margin_mode
                or liquidation.position_mode != policy.position_mode
                or liquidation.account_snapshot_id != account.snapshot_id
                or liquidation.reservation_snapshot_id != reservations.snapshot_id
                or liquidation.quote_id != market.quote_id
                or liquidation.policy_revision != policy.revision
                or liquidation.instrument_revision != instrument.revision
                or liquidation.tier_revision != tiers.revision or liquidation.tier_id != tier.tier_id):
            raise RiskRejected("UNVERIFIED_LIQUIDATION", "qualified evidence differs from exact proposal")
        _positive(liquidation.liquidation_price, "liquidation_price")
        liquidation_price = liquidation.liquidation_price
        gap = stop - liquidation_price if proposal.side == "LONG" else liquidation_price - stop
        liquidation_buffer = gap / entry
        if gap <= 0 or liquidation_buffer < policy.liquidation_buffer_fraction:
            raise RiskRejected("LIQUIDATION_BUFFER", "SL must precede liquidation by explicit user gap")
        liquidation_distance = abs(entry - liquidation_price) / entry
    if leverage_readback is None:
        blockers.append("UNVERIFIED_LEVERAGE")
    else:
        _scope(leverage_readback.scope, policy.scope)
        _fresh(leverage_readback.observed_at, now, policy.max_evidence_age_seconds, "leverage readback")
        if (not leverage_readback.qualified or leverage_readback.applied_leverage != leverage
                or leverage_readback.side != proposal.side
                or leverage_readback.margin_mode != policy.margin_mode
                or leverage_readback.position_mode != policy.position_mode
                or leverage_readback.account_snapshot_id != account.snapshot_id
                or leverage_readback.policy_revision != policy.revision):
            raise RiskRejected("UNVERIFIED_LEVERAGE", "actual applied readback differs from proposal")
    if stage == "SUBMIT" and blockers:
        raise RiskRejected(blockers[0], "final assessment requires independent verified evidence")
    if stage == "PLAN" and not blockers:
        blockers.append("PLANNING_ONLY")
    return EntryDecision(
        scope=policy.scope, assessed_at=now, stage=stage, quantity=quantity, notional=notional,
        estimated_loss=loss, estimated_loss_fraction=loss / account.equity,
        cost_allowance=costs, funding_allowance=quantity * market.funding_per_unit,
        required_leverage=required, leverage=leverage, initial_margin=initial_margin,
        margin_amount=margin, tier_id=tier.tier_id, venue_leverage_min=tier.min_leverage,
        venue_leverage_max=tier.max_leverage, clamp_reasons=tuple(reasons),
        stop_distance_fraction=distance / entry, liquidation_price=liquidation_price,
        liquidation_distance_fraction=liquidation_distance,
        liquidation_buffer_fraction=liquidation_buffer, submission_ready=stage == "SUBMIT",
        blockers=tuple(blockers), formula_version="LINEAR_LOSS_FIXED_COST_RESERVE_V1",
        policy=policy, proposal=proposal, market=market, instrument=instrument,
        account=account, tiers=tiers, reservations=reservations,
        liquidation=liquidation, leverage_readback=leverage_readback,
    )

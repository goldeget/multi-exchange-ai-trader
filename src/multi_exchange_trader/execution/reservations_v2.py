"""Pure local quantity accounting values and identities; no execution authority.

The existing journal exclusively owns storage. These values neither authenticate
an owner nor qualify the supplied account, policy, marks, or model evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.linear_risk_v2 import value_linear_admission_v2
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_codec import (
    DurableEntryIntentV2,
    encode_entry_intent_v2,
)
from multi_exchange_trader.execution.risk_v2_math import decimal_text_v2, finite_decimal_v2
from multi_exchange_trader.trading_product import InstrumentKey

if TYPE_CHECKING:
    from multi_exchange_trader.execution.journal import CommandRecord

_MAX_REVISION = 2**63 - 1
_CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"


@dataclass(frozen=True)
class ReservationV2:
    schema_version: Literal[2]
    instrument: InstrumentKey
    envelope_sha256: str
    base_quantity: Decimal
    risk_amount: Decimal
    margin_amount: Decimal

    def __post_init__(self) -> None:
        _reservation_payload(self)


@dataclass(frozen=True)
class ReservationAdmissionV2:
    account_scope: ProtectionScope
    owner_epoch: str
    expected_journal_revision: int
    expected_held_quantity_sha256: str
    expected_command_identity_sha256: str
    policy: c.LinearRiskPolicyV2
    account: c.LinearAccountRiskSnapshotV2
    marks: tuple[c.LinearMarkQuoteV2, ...]
    now: datetime

    def __post_init__(self) -> None:
        _validate_admission(self)
        object.__setattr__(self, "now", c._utc_v2(self.now))


def _revision(value: object, *, increment: bool = False) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_REVISION:
        c._reject_v2()
    if increment:
        if value == _MAX_REVISION:
            c._reject_v2("RISK_V2_RESOURCE_LIMIT")
        return value + 1
    return value


def _reservation_payload(value: object) -> dict[str, object]:
    values = c._fields_v2(value, ReservationV2)
    if type(values["schema_version"]) is not int or values["schema_version"] != 2:
        c._reject_v2()
    key = c._key_payload_v2(values["instrument"])
    if key["product"] != "LINEAR":
        c._reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    result: dict[str, object] = {
        "schema_version": 2,
        "instrument": key,
        "envelope_sha256": c._digest_v2(values["envelope_sha256"]),
    }
    for name in ("base_quantity", "risk_amount", "margin_amount"):
        result[name] = decimal_text_v2(
            c._decimal_v2(
                values[name],
                source=False,
                sign="positive" if name == "base_quantity" else "nonnegative",
            )
        )
    # Enforce the existing canonical resource limit even for standalone DTOs.
    c._json_size_v2(result)
    return result


def entry_command_identity_sha256_v2(
    account_scope: ProtectionScope,
    command_key: str,
    intent: DurableEntryIntentV2,
    reservation: ReservationV2,
) -> str:
    account = c._account_payload_v2(account_scope)
    key = c._identifier_v2(command_key)
    wire = encode_entry_intent_v2(intent)
    payload = _reservation_payload(reservation)
    if account != c._account_payload_v2(intent.envelope.scope.product_scope.account):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    required = intent.assessment.required_reservation_quote
    if required is None:
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    if (
        key != intent.command_key
        or reservation.instrument != intent.envelope.scope.instrument
        or reservation.envelope_sha256 != c.envelope_sha256_v2(intent.envelope)
        or decimal_text_v2(reservation.base_quantity)
        != decimal_text_v2(intent.envelope.base_quantity)
        or decimal_text_v2(reservation.risk_amount)
        != decimal_text_v2(intent.assessment.preparation.sizing.estimated_loss_quote)
        or decimal_text_v2(reservation.margin_amount) != decimal_text_v2(required)
    ):
        c._reject_v2(_CONFLICT)
    return c._canonical_sha256_v2(
        "LocalEntryCommandIdentityV2",
        {
            **account,
            "command_key": key,
            "kind": "ENTRY",
            "intent_sha256": hashlib.sha256(wire.encode("utf-8")).hexdigest(),
            "reservation": payload,
        },
    )


def _validate_admission(value: object) -> None:
    values = c._fields_v2(value, ReservationAdmissionV2)
    c._account_payload_v2(values["account_scope"])
    c._identifier_v2(values["owner_epoch"])
    _revision(values["expected_journal_revision"])
    c._digest_v2(values["expected_held_quantity_sha256"])
    c._digest_v2(values["expected_command_identity_sha256"])
    c.risk_policy_sha256_v2(values["policy"])
    c.account_snapshot_sha256_v2(values["account"])
    c.mark_set_sha256_v2(values["marks"])
    c._utc_v2(values["now"])
    account = c._account_payload_v2(values["account_scope"])
    if account != c._account_payload_v2(
        values["account"].account
    ) or account != c._account_payload_v2(values["policy"].scope.product_scope.account):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")


def _held_hash(account: ProtectionScope, records: tuple[CommandRecord, ...]) -> str:
    entries: list[dict[str, object]] = []
    for record in sorted(records, key=lambda item: (item.command_key, item.client_order_id)):
        reservation = record.reservation_v2
        if reservation is None or record.terminal_confirmed:
            continue
        payload = _reservation_payload(reservation)
        payload["version"] = payload.pop("schema_version")
        entries.append(
            {
                **payload,
                "command_key": record.command_key,
                "client_order_id": record.client_order_id,
                "payload_hash": record.payload_hash,
                "position_count": 1,
            }
        )
    if len(entries) > 1024:
        c._reject_v2("RISK_V2_RESOURCE_LIMIT")
    return c._canonical_sha256_v2(
        "LocalHeldQuantitySetV2",
        {
            **c._account_payload_v2(account),
            "entries": tuple(entries),
        },
    )


def _snapshot(
    account: ProtectionScope,
    records: tuple[CommandRecord, ...],
    *,
    owner_epoch: str,
    revision: int,
    now: datetime,
    exclude: str | None,
) -> c.LinearReservationSnapshotV2:
    c._identifier_v2(owner_epoch)
    _revision(revision)
    c._utc_v2(now)
    if exclude is not None:
        c._identifier_v2(exclude)
        if not any(
            item.command_key == exclude and item.reservation_v2 is not None for item in records
        ):
            c._reject_v2(_CONFLICT)
    digest = _held_hash(account, records)
    snapshot_id = c._canonical_sha256_v2(
        "LocalReservationSnapshotIdentityV2",
        {
            **c._account_payload_v2(account),
            "owner_epoch": owner_epoch,
            "journal_revision": revision,
            "held_quantity_sha256": digest,
            "excluded_command_key": exclude,
        },
    )
    components: list[c.ExposureQuantityV2] = []
    risk, margin = Fraction(0), Fraction(0)
    # Decode only after journal has verified every retained row's immutable identity.
    from multi_exchange_trader.execution.risk_v2_codec import decode_entry_intent_v2

    for record in records:
        reservation = record.reservation_v2
        if reservation is None or record.terminal_confirmed or record.command_key == exclude:
            continue
        intent = decode_entry_intent_v2(record.payload_json)
        components.append(
            c.ExposureQuantityV2(
                record.client_order_id,
                reservation.instrument,
                "PENDING_ENTRY",
                intent.envelope.side,
                reservation.base_quantity,
            )
        )
        risk += Fraction(reservation.risk_amount)
        margin += Fraction(reservation.margin_amount)
    components.sort(key=lambda item: (*c._key_order_v2(item.instrument), item.component_id))
    result = c.LinearReservationSnapshotV2(
        account,
        snapshot_id,
        owner_epoch,
        revision,
        digest,
        now,
        exclude,
        tuple(components),
        finite_decimal_v2(risk),
        finite_decimal_v2(margin),
        len(components),
    )
    # Include observed_at/economic fields in the existing snapshot digest/resource contract.
    c.reservation_snapshot_sha256_v2(result)
    return result


def _check_capacity(
    intent: DurableEntryIntentV2,
    reservation: ReservationV2,
    admission: ReservationAdmissionV2,
    snapshot: c.LinearReservationSnapshotV2,
) -> None:
    policy, account, now = admission.policy, admission.account, admission.now
    if (
        admission.account_scope != account.account
        or admission.account_scope != policy.scope.product_scope.account
        or c._scope_payload_v2(policy.scope) != c._scope_payload_v2(intent.envelope.scope)
    ):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    value_linear_admission_v2(
        policy=policy,
        sizing=intent.assessment.preparation.sizing,
        account=account,
        reservations=snapshot,
        marks=admission.marks,
        now=now,
    )
    if account.margin_mode != policy.margin_mode or account.position_mode != policy.position_mode:
        c._reject_v2("RISK_V2_UNSUPPORTED_MODE")
    if account.loss_day != now.date():
        c._reject_v2("RISK_V2_LOSS_LEDGER_UNVERIFIED")
    loss = Fraction(reservation.risk_amount)
    total = Fraction(account.total_risk) + Fraction(snapshot.risk_amount) + loss
    checks = (
        (loss > policy.per_trade_loss, "RISK_V2_TRADE_LOSS_LIMIT"),
        (total > policy.max_total_risk, "RISK_V2_TOTAL_RISK_LIMIT"),
        (Fraction(account.daily_loss) + total > policy.max_daily_loss, "RISK_V2_DAILY_LOSS_LIMIT"),
        (Fraction(account.drawdown) + total > policy.max_drawdown, "RISK_V2_DRAWDOWN_LIMIT"),
        (
            account.consecutive_losses >= policy.max_consecutive_losses,
            "RISK_V2_CONSECUTIVE_LOSS_LIMIT",
        ),
        (
            account.position_count + snapshot.position_count + 1 > policy.max_positions,
            "RISK_V2_POSITION_LIMIT",
        ),
        (bool(account.external_same_symbol_activity_ids), "RISK_V2_EXTERNAL_CONFLICT"),
    )
    for failed, code in checks:
        if failed:
            c._reject_v2(code)
    held = Fraction(snapshot.margin_amount)
    capacity = min(
        Fraction(policy.margin_allocation),
        Fraction(account.available_margin) - held,
        Fraction(policy.max_total_margin) - Fraction(account.total_margin) - held,
    )
    if Fraction(reservation.margin_amount) > capacity:
        c._reject_v2("RISK_V2_MARGIN_LIMIT")

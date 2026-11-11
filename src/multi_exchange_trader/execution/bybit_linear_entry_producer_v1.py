"""Current approved-policy/source data into the existing Bybit entry calculation.

Account coverage, costs and minimum evidence are explicit dependencies. This
owner does not qualify their issuers, convert observational metrics, register an
executable PLAN, or bypass the runtime's financial/native qualification gates.
The final U2-B consumer must still seal its own admission and fresh SUBMIT.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime

from ..account.bybit_readonly import BybitReadOnlyClient
from . import bybit_demo_uta1_profile_v1 as demo
from . import bybit_linear_model_inputs_v1 as a
from . import bybit_linear_model_v1 as m
from . import linear_risk_evidence_v2 as e
from . import linear_risk_v2 as r
from . import risk_domain_v2 as d
from . import risk_v2_contracts as c
from .account_binding import BybitDemoAccountSession
from .bybit_linear_source_assembler_v1 import BybitLinearSourceAssemblerV1, BybitLinearVenueReadV1
from .risk_v2_math import RiskV2Rejected
from .runtime_policy import RuntimePolicyBook
from .strategy_publication_v1 import StrategyPublicationOwnerV1

type ModelInputsV1 = a.BybitLinearModelInputsV1 | demo.BybitDemoUta1ModelInputsV1


@dataclass(frozen=True)
class BybitLinearPreparedEntryV1:
    """Calculation data; original-owner currentness is never supplied by this value alone."""

    source_read: BybitLinearVenueReadV1
    inputs: ModelInputsV1
    calculation: m.BybitBoundModelCalculationV1
    policy_revision: int
    publication_generation: int
    valid_until: datetime


@dataclass(frozen=True)
class _Current:
    result: BybitLinearPreparedEntryV1
    source_slot: object
    model_sha256: str
    calculation_sha256: str


def _model_hash(inputs: ModelInputsV1) -> str:
    if type(inputs) is demo.BybitDemoUta1ModelInputsV1:
        return demo.bybit_demo_uta1_model_inputs_sha256_v1(inputs)
    if type(inputs) is a.BybitLinearModelInputsV1:
        return a.bybit_model_inputs_sha256_v1(inputs)
    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")


class BybitLinearEntryProducerV1:
    """Consume one original assembler read; original math/source exceptions retain their owner."""

    def __init__(self, *, assembler: BybitLinearSourceAssemblerV1,
                 policy_book: RuntimePolicyBook) -> None:
        if type(assembler) is not BybitLinearSourceAssemblerV1 or type(policy_book) is not RuntimePolicyBook:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.assembler = assembler
        self.policy_book = policy_book
        self._publication = StrategyPublicationOwnerV1(policy_book)
        self._current: _Current | None = None
        self._busy: object | None = None
        self._epoch = object()

    @contextmanager
    def _operation(self) -> Iterator[object]:
        if self._busy is not None:
            self._current = None
            self._epoch = object()
            raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        token = object()
        self._busy = token
        epoch = self._epoch
        try:
            yield token
            if self._busy is not token or self._epoch is not epoch:
                raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        except BaseException:
            self._current = None
            raise
        finally:
            if self._busy is token:
                self._busy = None

    def _policy(self, inputs: e.LinearRiskInputsV2, revision: int, generation: int) -> None:
        if type(revision) is not int or revision <= 0 or type(generation) is not int or generation <= 0:
            raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
        actual = self.policy_book.risk_policy_v2(inputs.policy.scope)
        if actual.revision != str(revision) or c.risk_policy_sha256_v2(actual) != c.risk_policy_sha256_v2(inputs.policy):
            raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
        self._publication.require_current(inputs.policy.scope.strategy, generation=generation,
            policy_revision=revision)

    def prepare_plan_v1(self, *, read: BybitLinearVenueReadV1, inputs: e.LinearRiskInputsV2,
                        domain: d.LinearRiskDomainCoverageV2, policy_revision: int,
                        publication_generation: int) -> BybitLinearPreparedEntryV1 | m.BybitModelRejectedV1:
        """Bind explicit economic inputs to a retained genuine read, then actually calculate.

        This is pre-registration PLAN calculation. Own-PENDING evaluation belongs
        to the existing separate replay path and cannot omit a reservation here.
        """
        with self._operation() as token:
            self._current = None
            e._inputs_payload_v2(inputs)
            d.risk_domain_sha256_v2(domain)
            self._policy(inputs, policy_revision, publication_generation)
            self.assembler.require_current_v1(read)
            original = self.assembler._current
            if original is None or c._scope_payload_v2(original.scope) != c._scope_payload_v2(inputs.policy.scope):
                raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
            inputs = replace(
                inputs,
                last_trade_quote=self.assembler.issue_current_last_trade_quote_v2(read),
            )
            if (c.reservation_snapshot_sha256_v2(read.full_reservations)
                    != c.reservation_snapshot_sha256_v2(read.evaluation_reservations)
                    or c.reservation_snapshot_sha256_v2(inputs.reservations)
                    != c.reservation_snapshot_sha256_v2(read.full_reservations)
                    or c.mark_set_sha256_v2(inputs.marks) != c.mark_set_sha256_v2(read.marks)):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            now = c._utc_v2(self.assembler.clock())
            preparation = r.prepare_linear_entry_v2(inputs, now=now)
            bound: ModelInputsV1
            if type(read.venue) is demo.BybitDemoUta1VenueStateV1:
                bound = demo.bind_bybit_demo_uta1_model_inputs_v1(inputs,
                    preparation=preparation, domain=domain, venue=read.venue,
                    catalog=read.catalog, selected_mark=read.selected_mark, now=now)
                calculation = demo.calculate_bound_bybit_demo_uta1_model_v1(bound, now=now)
            elif type(read.venue) is a.BybitVenueStateV1:
                bound = a.bind_bybit_model_inputs_v1(inputs, preparation=preparation,
                    domain=domain, venue=read.venue, catalog=read.catalog,
                    selected_mark=read.selected_mark, now=now)
                calculation = m.calculate_bound_bybit_model_v1(bound, now=now)
            else:
                raise RiskV2Rejected("RISK_V2_UNSUPPORTED_MODE")
            if type(calculation) is m.BybitModelRejectedV1:
                return calculation
            if not isinstance(calculation, m.BybitBoundModelCalculationV1):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            result = BybitLinearPreparedEntryV1(read, bound, calculation, policy_revision,
                publication_generation, min(bound.valid_until, calculation.valid_until))
            self._current = _Current(result, self.assembler._current, _model_hash(bound),
                m.bybit_bound_calculation_sha256_v1(calculation))
            self._require(result, token)
            return result

    def _require(self, result: BybitLinearPreparedEntryV1, token: object) -> None:
        slot = self._current
        if slot is None or result is not slot.result or self._busy is not token:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if (_model_hash(result.inputs) != slot.model_sha256
                or m.bybit_bound_calculation_sha256_v1(result.calculation) != slot.calculation_sha256):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        assembler, book, publication = self.assembler, self.policy_book, self._publication
        session = assembler.session
        if type(session) is not BybitDemoAccountSession or type(session._client) is not BybitReadOnlyClient:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        client, pair = session._client, session._credential_pair
        if (type(pair) is not tuple or len(pair) != 2 or any(type(value) is not str for value in pair)
                or type(client.environment) is not str or type(client.base_url) is not str):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        http_client, http_state = client._client, client._client._state
        binding, identity = session._binding, session._identity
        clock, guard, epoch = assembler.clock, assembler.owner_guard, self._epoch
        environment, base_url = client.environment, client.base_url
        # All clock callbacks precede source validation and the final persisted
        # policy/publication read. This is data currentness, not a SUBMIT seal.
        now = c._utc_v2(clock())
        assembler.require_current_v1(result.source_read)
        assembler.require_current_last_trade_quote_v2(
            result.source_read, result.inputs.risk_inputs.last_trade_quote
        )
        self._policy(result.inputs.risk_inputs, result.policy_revision, result.publication_generation)
        if now >= result.valid_until:
            raise RiskV2Rejected("RISK_V2_EXPIRED")
        # Finite original-owner suffix, like the worker publication boundary:
        # exact primitive values and identity only; no clock/guard/SQL/hash call.
        if (self._current is not slot or self._busy is not token
                or self._epoch is not epoch or self.assembler is not assembler
                or self.policy_book is not book or self._publication is not publication
                or publication._book is not book or assembler._current is not slot.source_slot
                or assembler.session is not session or assembler.clock is not clock
                or assembler.owner_guard is not guard or session._closed is not False
                or session._binding is not binding or session._identity is not identity
                or session._client is not client or session._credential_client is not client
                or session._credential_pair is not pair or client._client is not http_client
                or http_client._state is not http_state
                or type(client.environment) is not str or type(client.base_url) is not str
                or (client.environment, client.base_url) != (environment, base_url)
                or client.environment != "demo" or client.base_url != "https://api-demo.bybit.com"
                or type(session._api_key) is not str or type(session._api_secret) is not str
                or (session._api_key, session._api_secret) != pair
                or type(client._api_key) is not str or type(client._api_secret) is not str
                or (client._api_key, client._api_secret) != pair):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    def require_current_v1(self, result: BybitLinearPreparedEntryV1) -> BybitLinearPreparedEntryV1:
        with self._operation() as token:
            self._require(result, token)
            return result

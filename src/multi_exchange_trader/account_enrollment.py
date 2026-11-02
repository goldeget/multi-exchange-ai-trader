"""Explicit GET-only observation and one-time operator adoption; no runtime authority.

The user-selected UID predates the observation. Adopting provider relationship
claims is explicitly weaker than independently confirming those claims. A saved
profile remains expectation configuration, never current authentication.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

import httpx

from .account.bybit_readonly import BybitReadOnlyClient
from .account.config import allowed_host, require_demo_credentials
from .account_profiles import AccountProfileResult, AccountProfileSnapshot, AccountProfileStore
from .credential_resolution import CredentialResolver
from .execution.account_binding import _parse_account_metadata, _server_time, _uid, _utc

ADOPTION_POLICY = "uid-anchored-adoption-v1"
_TTL = timedelta(seconds=30)


class AccountEnrollmentError(ValueError):
    """Fixed redacted local diagnostics only."""


class EnrollmentCandidate:
    """Exact issuer-owned in-memory handle; copies/data cannot grant adoption."""

    __slots__ = ()

    def __new__(cls) -> Self:
        raise AccountEnrollmentError("ENROLLMENT_CANDIDATE_REQUIRED")

    def __repr__(self) -> str:
        return "EnrollmentCandidate(opaque=True)"


@dataclass(frozen=True, repr=False)
class _Observation:
    generation: object
    started_at: datetime
    started_mono: float
    observed_at: datetime
    observed_mono: float
    server_times: tuple[datetime, datetime]
    parent_uid: str = field(repr=False)
    is_master: bool = field(repr=False)


class BybitDemoEnrollmentSession:
    """Separate credential-owning session with no authentication/native methods.

    Construct explicitly from one frozen resolver's DEMO pair or use
    from_environment, which checks the pending profile before resolving keys.
    Injected transports may observe but cannot publish operator adoption. Tests
    can isolate the real HTTP transport's lowest I/O boundary hermetically; that
    qualification is not evidence of actual venue access.
    """

    def __init__(self, *, store: AccountProfileStore, snapshot: AccountProfileSnapshot,
                 api_key: str, api_secret: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], float] = time.monotonic,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if (type(store) is not AccountProfileStore or type(snapshot) is not AccountProfileSnapshot
                or snapshot.state != "PENDING" or type(api_key) is not str or not api_key.strip()
                or type(api_secret) is not str or not api_secret.strip()):
            raise AccountEnrollmentError("ENROLLMENT_PENDING_PROFILE_AND_CREDENTIALS_REQUIRED")
        _uid(snapshot._uid)
        self._store, self._snapshot, self._path = store, snapshot, store.path
        self._target_store, self._target_snapshot = store, snapshot
        self._api_key, self._api_secret = api_key.strip(), api_secret.strip()
        self._pair = self._api_key, self._api_secret
        self._clock, self._monotonic = clock, monotonic
        self._transport_injected = transport is not None
        self._client = BybitReadOnlyClient(api_key=self._api_key, api_secret=self._api_secret,
            base_url=allowed_host("demo"), environment="demo", transport=transport)
        self._credential_client = self._client
        self._http = self._client._client
        self._transport = self._http._transport
        self._mounts = tuple(self._http._mounts.items())
        self._generation = object()
        self._candidate: EnrollmentCandidate | None = None
        self._observation: _Observation | None = None
        self._adopting: object | None = None
        self._last_clock: tuple[datetime, float] | None = None
        self._closed, self._busy = False, False

    @classmethod
    def from_environment(cls, *, store: AccountProfileStore,
                         environ: Mapping[str, str]) -> Self:
        snapshot = store.load()
        if snapshot is None or snapshot.state != "PENDING":
            raise AccountEnrollmentError("ENROLLMENT_PENDING_PROFILE_REQUIRED")
        resolver = CredentialResolver.from_environment(environ=environ)
        key, secret = require_demo_credentials(resolver=resolver)
        return cls(store=store, snapshot=snapshot, api_key=key, api_secret=secret)

    def __repr__(self) -> str:
        return "BybitDemoEnrollmentSession(<private>)"

    def _retire(self) -> None:
        self._candidate, self._observation, self._adopting = None, None, None
        self._generation = object()
        self._last_clock = None

    def _guard(self) -> None:
        if (self._closed or self._client is not self._credential_client
                or self._store is not self._target_store or self._snapshot is not self._target_snapshot
                or type(self._client) is not BybitReadOnlyClient
                or (self._api_key, self._api_secret) != self._pair
                or (self._client._api_key, self._client._api_secret) != self._pair
                or self._client.base_url != allowed_host("demo")
                or self._client.environment != "demo" or self._store.path != self._path
                or self._client._client is not self._http
                or self._http._transport is not self._transport
                or tuple(self._http._mounts.items()) != self._mounts):
            raise AccountEnrollmentError("ENROLLMENT_SESSION_CHANGED_OR_CLOSED")

    def _now(self) -> tuple[datetime, float]:
        try:
            wall, mono = _utc(self._clock()), self._monotonic()
            if type(mono) not in (int, float) or not math.isfinite(mono) or mono < 0:
                raise ValueError
            if self._last_clock is not None and (
                wall < self._last_clock[0] or mono < self._last_clock[1]
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 - custom clock errors must not disclose private values.
            failure = AccountEnrollmentError("ENROLLMENT_CLOCK_INVALID")
        else:
            # Private sample watermark only (Ruling7). This never renews the
            # observation's original start/expiry or changes authority/target.
            self._last_clock = wall, mono
            return wall, mono
        raise failure from None

    @staticmethod
    def _fresh(start: datetime, mono_start: float, now: datetime, mono: float,
               server_times: tuple[datetime, ...]) -> None:
        if (not timedelta(0) <= now - start <= _TTL or not 0 <= mono - mono_start <= 30
                or any(not timedelta(0) <= now - stamp <= _TTL for stamp in server_times)):
            raise AccountEnrollmentError("ENROLLMENT_STALE")

    async def observe(self) -> EnrollmentCandidate:
        if self._busy or self._adopting is not None:
            raise AccountEnrollmentError("ENROLLMENT_OPERATION_IN_PROGRESS")
        self._retire()
        self._busy = True
        try:
            self._guard()
            start, start_mono = self._now()
            generation = self._generation
            deadline = time.monotonic() + 30
            # Overall timeout bounds retries and both GETs as a single observation.
            async with asyncio.timeout(30):
                _, stamp = await self._client.read("/v5/market/time", [], signed=False,
                                                   deadline=deadline)
                self._guard()
                now, mono = self._now()
                first_server = _server_time(stamp)
                self._fresh(start, start_mono, now, mono, (first_server,))
                assert stamp is not None
                self._client.anchor_server_time(stamp)
                result, stamp = await self._client.read("/v5/user/query-api", [], signed=True,
                                                       deadline=deadline)
            self._guard()
            observed, observed_mono = self._now()
            servers = first_server, _server_time(stamp)
            self._fresh(start, start_mono, observed, observed_mono, servers)
            if observed < now or observed_mono < mono or generation is not self._generation:
                raise AccountEnrollmentError("ENROLLMENT_CLOCK_OR_GENERATION_CHANGED")
            identity = _parse_account_metadata(result, api_key=self._api_key,
                api_secret=self._api_secret, expected_uid=self._snapshot._uid,
                observed_at=observed, server_observed_at=servers[-1],
                transport_injected=self._transport_injected)
            self._observation = _Observation(generation, start, start_mono, observed,
                observed_mono, servers, identity.parent_uid, identity.is_master)
            self._candidate = object.__new__(EnrollmentCandidate)
            return self._candidate
        except Exception:  # noqa: BLE001 - provider/clock diagnostics cannot cross boundary.
            failure = AccountEnrollmentError("ENROLLMENT_OBSERVATION_FAILED")
        except BaseException:
            self._retire()
            raise
        finally:
            self._busy = False
        self._retire()
        raise failure from None

    def adopt(self, candidate: EnrollmentCandidate, *, policy: str,
              adopt_observed_relationship: bool) -> AccountProfileResult:
        """Consume VALID -> ADOPTING before any publication, retire on every outcome."""
        operation = object()
        observation = self._observation
        try:
            self._guard()
            if (self._busy or type(candidate) is not EnrollmentCandidate
                    or candidate is not self._candidate or observation is None
                    or policy != ADOPTION_POLICY or adopt_observed_relationship is not True
                    or self._transport_injected):
                raise AccountEnrollmentError("ENROLLMENT_EXPLICIT_VALID_CANDIDATE_REQUIRED")
            adopt_at, adopt_mono = self._now()
            # Only this exact ongoing operation can pass the store's repeated guards.
            self._candidate = None
            self._adopting = operation

            def guard() -> None:
                self._guard()
                now, mono = self._now()
                if (self._adopting is not operation or self._observation is not observation
                        or observation.generation is not self._generation
                        or now < observation.observed_at or mono < observation.observed_mono
                        or now < adopt_at or mono < adopt_mono):
                    raise AccountEnrollmentError("ENROLLMENT_CANDIDATE_RETIRED")
                self._fresh(observation.started_at, observation.started_mono,
                            now, mono, observation.server_times)

            guard()
            stamp = observation.observed_at.isoformat().replace("+00:00", "Z")
            relationship: dict[str, object] = {
                "parent_uid": observation.parent_uid, "is_master": observation.is_master,
                "basis": "PROVIDER_OBSERVED_OPERATOR_ADOPTED", "source": "BYBIT_DEMO_QUERY_API",
                "evidence_id": str(uuid4()), "confirmed_at": adopt_at.isoformat().replace("+00:00", "Z"),
                "provider_observed_at": stamp, "policy": ADOPTION_POLICY,
            }
            last_observation: dict[str, object] = {
                "source": "BYBIT_DEMO_QUERY_API", "parent_uid": observation.parent_uid,
                "is_master": observation.is_master, "observed_at": stamp,
                "server_observed_at": observation.server_times[-1].isoformat().replace("+00:00", "Z"),
            }
            return self._store._adopt_initial(profile_id=self._snapshot.profile_id,
                expected_uid=self._snapshot._uid, expected_revision=self._snapshot.revision,
                relationship=relationship, last_observation=last_observation, guard=guard)
        finally:
            self._retire()

    async def aclose(self) -> None:
        self._closed = True
        self._retire()
        try:
            await self._credential_client.aclose()
        except Exception:  # noqa: BLE001 - transport diagnostics may contain credentials.
            failure = AccountEnrollmentError("ENROLLMENT_CLOSE_FAILED")
        else:
            return
        raise failure from None

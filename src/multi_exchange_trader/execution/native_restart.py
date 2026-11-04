"""Sealed local generation lifecycle; neither reconciliation nor execution authority."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .journal import CommandState
from .native_node import NativeNodeAssembly
from .native_recovery import OwnedCacheExpectation
from .native_restart_codec import (
    capture_native_cache,
    install_native_cache,
    prepare_native_cache,
)
from .native_restart_store import (
    DATABASE_FILES,
    NativeRestartError,
    NativeRestartIdentity,
    NativeRestartStore,
    application_digest,
)
from .protection import ProtectionScope
from .runtime import NativeOwnedRequest, NativeOwnershipResolver


@dataclass(frozen=True)
class WorkerNativeRestartStatus:
    status: Literal["FRESH_EMPTY_CACHE", "RESTORED_LOCAL_CACHE", "BLOCKED", "TEST_ASSEMBLY"]
    orders: int = 0
    positions: int = 0
    missing_owned_orders: int = 0
    generation_id: str | None = None
    production_supported: Literal[False] = False


def _empty_application(directory: Path) -> bool:
    # Store constructors already validated schemas/scope. Inspect every table,
    # including unscoped rows, audit, inbox and history. Missing DBs are not empty proof.
    application_digest(directory)  # Apply the store's bounded path and SQLite checks first.
    for name in DATABASE_FILES:
        path = directory / name
        if not path.is_file():
            return False
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (table,) in tables:
                if name == "history.sqlite3" and table == "history_scope":
                    continue  # Exact scope row has already been checked by HistoryStore.
                quoted = '"' + table.replace('"', '""') + '"'
                if db.execute(f"SELECT 1 FROM {quoted} LIMIT 1").fetchone() is not None:
                    return False
    return True


class NativeRestartCoordinator:
    """Called only under the worker's scoped lease and admission fence."""

    def __init__(self, directory: Path, assembly: NativeNodeAssembly,
                 scope: ProtectionScope, symbols: tuple[str, ...], trader_id: str) -> None:
        self.assembly = assembly
        self.store = NativeRestartStore(directory)
        strategy = assembly.strategy
        if str(strategy.trader_id) != trader_id:
            raise NativeRestartError()
        self.identity = NativeRestartIdentity(scope, assembly.channel.native_account_id,
            str(strategy.trader_id), str(strategy.id),
            tuple(sorted(symbol + "-LINEAR.BYBIT" for symbol in symbols)))
        self.status = WorkerNativeRestartStatus("BLOCKED")

    def _expectations(self, requests: tuple[NativeOwnedRequest, ...]
                      ) -> tuple[tuple[OwnedCacheExpectation, ...], frozenset[str]]:
        owners, missing = [], set()
        for item in requests:
            request, venues = item.request, item.venue_order_ids
            if request.scope != self.identity.scope or len(venues) > 1:
                raise NativeRestartError()
            venue = next(iter(venues), None)
            owners.append(OwnedCacheExpectation(request, self.identity.native_account_id,
                self.identity.trader_id, self.identity.strategy_id, venue, None))
            if not venues and item.command_state in (
                    CommandState.PENDING, CommandState.SENDING, CommandState.UNKNOWN):
                missing.add(request.owned.client_order_id)
        return tuple(owners), frozenset(missing)

    def restore(self, resolve: NativeOwnershipResolver) -> None:
        self.assembly.bridge._retire_prepared_installation_v2()
        requests = resolve(())
        try:
            generation = self.store.load(self.identity)
            owners, missing = self._expectations(requests)
            if generation is None:
                if requests or not _empty_application(self.store.directory):
                    raise NativeRestartError()
                payload = capture_native_cache(self.assembly.node.cache,
                    identity=self.identity, owners=owners, allowed_missing_ids=missing)
                # A fresh constructor must be empty, including instruments and accounts.
                if any(payload[key] for key in ("instruments", "orders", "positions")):
                    raise NativeRestartError()
            else:
                payload = generation.payload
            prepared = prepare_native_cache(payload, identity=self.identity,
                owners=owners, allowed_missing_ids=missing)
            owners, missing = self._expectations(resolve(prepared.venue_order_ids))
            prepared = prepare_native_cache(payload, identity=self.identity,
                owners=owners, allowed_missing_ids=missing)
            cache = self.assembly.node.cache
            if (cache.orders() or cache.positions() or cache.instruments() or cache.accounts()
                    or cache.client_order_ids() or cache.position_ids()
                    or cache.position_snapshot_ids()):
                raise NativeRestartError()
            self.store.mark_active(self.identity, generation)
            result = install_native_cache(self.assembly.node.cache, prepared)
            self.status = WorkerNativeRestartStatus(
                "FRESH_EMPTY_CACHE" if generation is None else "RESTORED_LOCAL_CACHE",
                result.orders, result.positions, result.missing_owned_orders,
                None if generation is None else generation.generation_id)
        except Exception:  # noqa: BLE001 - preserve fixed redacted startup blocker.
            self.status = WorkerNativeRestartStatus("BLOCKED")
            raise NativeRestartError() from None

    def seal(self, resolve: NativeOwnershipResolver) -> None:
        requests = resolve(())
        owners, missing = self._expectations(requests)
        payload = capture_native_cache(self.assembly.node.cache, identity=self.identity,
            owners=owners, allowed_missing_ids=missing)
        prepared = prepare_native_cache(payload, identity=self.identity,
            owners=owners, allowed_missing_ids=missing)
        owners, missing = self._expectations(resolve(prepared.venue_order_ids))
        prepare_native_cache(payload, identity=self.identity,
            owners=owners, allowed_missing_ids=missing)
        self.store.seal(self.identity, payload)

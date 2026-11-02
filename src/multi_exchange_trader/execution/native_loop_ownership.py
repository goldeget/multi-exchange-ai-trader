"""CPython 3.12 shared-loop ownership around the pinned kernel constructor."""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from .monitoring_models import MonitoringError


@contextmanager
def preserve_default_executor(loop: asyncio.AbstractEventLoop) -> Iterator[None]:
    # The pinned kernel sets the default synchronously. Never yield with its pool
    # installed: unrelated to_thread jobs must stay owned by the caller's loop.
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12)
            or type(loop).__module__ not in ("asyncio.unix_events", "asyncio.windows_events")
            or loop is not asyncio.get_running_loop()):
        raise MonitoringError("MONITORING_SHARED_LOOP_UNSUPPORTED")
    previous = getattr(loop, "_default_executor", None)
    if previous is not None and not isinstance(previous, ThreadPoolExecutor):
        raise MonitoringError("MONITORING_SHARED_EXECUTOR_UNSUPPORTED")
    try:
        yield
    finally:
        # set_default_executor disallows None in 3.12. Restore the versioned
        # absent setting directly; never shut down either pool here.
        if previous is None:
            loop._default_executor = None  # type: ignore[attr-defined]
        else:
            loop.set_default_executor(previous)

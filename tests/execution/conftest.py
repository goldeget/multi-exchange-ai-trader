"""Close synthetic HTTP clients even when their harness is imported by another module."""

from collections.abc import AsyncIterator

import pytest
from worker_receipt_support import SESSIONS


@pytest.fixture(autouse=True)
async def close_synthetic_worker_sessions() -> AsyncIterator[None]:
    try:
        yield
    finally:
        while SESSIONS:
            await SESSIONS.pop().aclose()

"""Real shared-loop/thread ownership, without replacing the SDK lifecycle."""
import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest


def preservation():
    spec = importlib.util.find_spec('multi_exchange_trader.execution.native_loop_ownership')
    assert spec is not None, 'shared loop executor ownership helper is required'
    return importlib.import_module(spec.name).preserve_default_executor


@pytest.mark.parametrize('existing', [False, True])
async def test_sdk_pool_disposal_preserves_unrelated_thread_work(existing):
    preserve = preservation()
    loop = asyncio.get_running_loop()
    previous = loop._default_executor
    original = ThreadPoolExecutor(max_workers=1) if existing else None
    loop._default_executor = original
    sdk = ThreadPoolExecutor(max_workers=1)
    gate = Event()
    queued = None
    try:
        if original:
            running = loop.run_in_executor(None, gate.wait)
            queued = loop.run_in_executor(None, lambda: 'queued survives')
        with preserve(loop):
            loop.set_default_executor(sdk)
        assert loop._default_executor is original
        sdk.shutdown(wait=True, cancel_futures=True)
        gate.set()
        if original:
            await running
            assert await queued == 'queued survives'
        assert await asyncio.to_thread(lambda: 42) == 42
    finally:
        gate.set()
        if loop._default_executor is not previous:
            loop._default_executor.shutdown(wait=True)
        loop._default_executor = previous
        sdk.shutdown(wait=True)


async def test_constructor_exception_restores_previous_thread_pool():
    preserve = preservation()
    loop = asyncio.get_running_loop()
    assert await asyncio.to_thread(lambda: 'before') == 'before'
    previous = loop._default_executor
    sdk = ThreadPoolExecutor(max_workers=1)
    try:
        with pytest.raises(RuntimeError), preserve(loop):
            loop.set_default_executor(sdk)
            raise RuntimeError('constructor failed')
        sdk.shutdown(wait=True, cancel_futures=True)
        assert loop._default_executor is previous
        assert await asyncio.to_thread(lambda: 'after') == 'after'
    finally:
        sdk.shutdown(wait=True)

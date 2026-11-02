import restored_monitoring_io_support as support

from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.runtime import RuntimeBlocked


def test_cleanup_contract_rejects_wrong_failure_owner_and_missing_evidence():
    owner, foreign = object(), object()
    contract = support.cleanup_contract(owner, "gap")
    gap = "MONITORING_DELIVERY_GAP_RETAINS_RESOURCES"
    assert support.classify_cleanup(contract, owner, MonitoringError(gap), True) == (MonitoringError, gap, "gap")
    for actual_owner, error, evidence in (
        (foreign, MonitoringError(gap), True),
        (owner, ValueError(gap), True),
        (owner, MonitoringError("OTHER"), True),
        (owner, TimeoutError(), True),
        (owner, MonitoringError(gap), False),
        (owner, RuntimeBlocked("ACCOUNT_WORKER_LEASE_REQUIRED"), True),
    ):
        assert support.classify_cleanup(contract, actual_owner, error, evidence) is None
    lease = support.cleanup_contract(owner, "lease")
    assert support.classify_cleanup(lease, owner, RuntimeBlocked("ACCOUNT_WORKER_LEASE_REQUIRED"), True)
    assert support.classify_cleanup(lease, owner, RuntimeBlocked("ACCOUNT_WORKER_LEASE_REQUIRED"), False) is None
    assert support.classify_cleanup(None, owner, MonitoringError(gap), True) is None


def test_cleanup_evidence_requires_original_lock_and_observed_gap(tmp_path, monkeypatch):
    from types import SimpleNamespace

    scope = support.SCOPE
    lock = SimpleNamespace(path=tmp_path / "lease", acquired=True, acquisition_id=7,
        matches_scope=lambda **kwargs: kwargs == {"account_id": scope.account_id, "environment": scope.environment})
    journal = SimpleNamespace(scope=scope, get=lambda key: object())
    store = SimpleNamespace(get_position=lambda key: object())
    history = object()
    runtime = SimpleNamespace(worker_lock=lock, _closed=False, _lease=7,
        _lock_path=lock.path.resolve(), journal=journal, store=store)
    stamp = SimpleNamespace(received=3, delivered=2)
    assembly = SimpleNamespace(monitoring_stamp=lambda: stamp)
    status = SimpleNamespace(resources_retained=True, retained_stamp=stamp)
    worker = SimpleNamespace(_runtime=runtime, runtime=runtime, _lock=lock, _assembly=assembly,
        scope=scope, _history_store=history, _store=store, _journal=journal,
        _channel_closed=False, _stopped=False, monitoring_status=status, directory=tmp_path)
    owner = SimpleNamespace(worker=worker, assembly=assembly)
    (tmp_path / "native-restart.json").write_bytes(b"fixture")
    monkeypatch.setattr(support, "decode_document", lambda data: {"state": "active"})
    gap = support.cleanup_contract(owner, "gap")
    assert support.cleanup_evidence(gap, owner)
    stamp.delivered = 3
    assert not support.cleanup_evidence(gap, owner)
    stamp.delivered = 2
    lease = support.cleanup_contract(owner, "lease")
    assert not support.cleanup_evidence(lease, owner)
    lock.acquisition_id = 8
    assert support.cleanup_evidence(lease, owner)
    runtime._lock_path = tmp_path / "foreign"
    assert not support.cleanup_evidence(lease, owner)
    runtime._lock_path = lock.path.resolve()
    worker._lock = object()
    assert not support.cleanup_evidence(lease, owner)

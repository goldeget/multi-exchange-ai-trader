"""Synthetic stdin-only imports into explicit private fixture stores."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from credentials.test_store import PAIR, private_store

from multi_exchange_trader import credentials_cli
from multi_exchange_trader.credentials import CredentialStore


def install_stdin(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))


def store_for_test(tmp_path: Path) -> Any:
    return private_store(importlib.import_module("multi_exchange_trader.credentials"), tmp_path)


def test_stdin_registration_uses_existing_private_store_without_a_plaintext_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_for_test(tmp_path)
    values = {**PAIR, "BYBIT_DEMO_API_KEY": "synthetic-한글-demo-key"}
    install_stdin(monkeypatch, json.dumps(values, ensure_ascii=False).encode("utf-8"))
    assert credentials_cli.main(["--import-stdin", "--store", str(store.path)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"schema": 1, "revision": 1, "namespaces": ["BYBIT_DEMO"]}
    assert captured.err == ""
    assert store.load().bybit_pair("demo") == (
        values["BYBIT_DEMO_API_KEY"],
        PAIR["BYBIT_DEMO_API_SECRET"],
    )
    assert not any(value in captured.out for value in values.values())
    assert str(tmp_path) not in captured.out
    assert {path.name for path in store.path.parent.iterdir()} <= {
        store.path.name,
        ".credentials.lock",
    }


def test_stdin_revision_cas_preserves_existing_store_and_allows_partial_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_for_test(tmp_path)
    store.register(PAIR, expected_revision=None)
    before = store.path.read_bytes()
    replacement = {"BYBIT_DEMO_API_SECRET": "synthetic-rotated-secret"}
    arguments = ["--import-stdin", "--store", str(store.path)]
    install_stdin(monkeypatch, json.dumps(replacement).encode())
    assert credentials_cli.main(arguments) == 1
    assert capsys.readouterr().err.strip() == "REVISION_CONFLICT"
    assert store.path.read_bytes() == before
    install_stdin(monkeypatch, json.dumps(replacement).encode())
    assert credentials_cli.main([*arguments, "--expected-revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 2
    assert store.load().bybit_pair("demo") == (
        PAIR["BYBIT_DEMO_API_KEY"],
        replacement["BYBIT_DEMO_API_SECRET"],
    )


INVALID_INPUTS = [
    b"",
    b"{synthetic-source-path",
    b"[]",
    b"null",
    b"true",
    b'{"BYBIT_DEMO_API_KEY":"synthetic-one","BYBIT_DEMO_API_KEY":"synthetic-two"}',
    b'{"BYBIT_DEMO_API_KEY":"synthetic-one","BYBIT_DEMO_API_\\u004bEY":"synthetic-two"}',
    b'{"BYBIT_DEMO_API_SECRET":true}',
    b'{"BYBIT_DEMO_API_SECRET":null}',
    b'{"BYBIT_DEMO_API_SECRET":5}',
    b'{"BYBIT_DEMO_API_SECRET":{"nested":"synthetic"}}',
    b'{"LIVE_TRADING_ENABLED":"true"}',
    b'{"UNKNOWN_SYNTHETIC_KEY":"synthetic"}',
    b'{"BYBIT_DEMO_API_SECRET":"synthetic\\u0000"}',
    b'{"BYBIT_DEMO_API_SECRET":"synthetic\\t"}',
    b'{"BYBIT_DEMO_API_SECRET":"synthetic\\ud800"}',
    b'{"BYBIT_DEMO_API_SECRET":"\xff"}',
    json.dumps(PAIR).encode("utf-16"),
    b'{"BYBIT_DEMO_API_SECRET":' + b"[" * 1500 + b'"synthetic"' + b"]" * 1500 + b"}",
    b" " * 16385,
]


@pytest.mark.parametrize(
    "raw", INVALID_INPUTS, ids=[f"invalid-{index}" for index in range(len(INVALID_INPUTS))]
)
def test_malformed_unbounded_nonstring_and_unapproved_stdin_never_changes_existing_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: bytes,
) -> None:
    store = store_for_test(tmp_path)
    store.register(PAIR, expected_revision=None)
    before = store.path.read_bytes()
    install_stdin(monkeypatch, raw)
    assert (
        credentials_cli.main(
            ["--import-stdin", "--store", str(store.path), "--expected-revision", "1"]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() in {"INVALID_IMPORT", "INVALID_VALUES"}
    assert "synthetic" not in captured.err and str(tmp_path) not in captured.err
    assert "Traceback" not in captured.err
    assert store.path.read_bytes() == before
    assert store.load().bybit_pair("demo") == (
        PAIR["BYBIT_DEMO_API_KEY"],
        PAIR["BYBIT_DEMO_API_SECRET"],
    )


class NoReadStdin:
    @property
    def buffer(self) -> Any:
        raise AssertionError("synthetic stdin must not be read implicitly")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--import-stdin", "--import-env", "synthetic-private-source"],
        ["--import-stdin", "--api-key", "synthetic-private-key"],
        ["--import-stdin", "--expected-revision", "synthetic-private-revision"],
    ],
)
def test_only_explicit_stdin_mode_reads_input_and_argument_errors_never_echo_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", NoReadStdin())
    assert credentials_cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip() == "INVALID_ARGUMENTS"


def test_stdin_read_is_bounded_and_io_errors_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BoundedInput(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= 16385
            return super().read(size)

    store = store_for_test(tmp_path)
    store.register(PAIR, expected_revision=None)
    before = store.path.read_bytes()
    raw = BoundedInput(b" " * 30000)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw))
    arguments = ["--import-stdin", "--store", str(store.path), "--expected-revision", "1"]
    assert credentials_cli.main(arguments) == 1
    assert raw.tell() == 16385
    assert capsys.readouterr().err.strip() == "INVALID_IMPORT"

    class FailedInput(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("synthetic-secret /synthetic/private/source")

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(FailedInput()))
    assert credentials_cli.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip() == "INVALID_IMPORT"
    assert store.path.read_bytes() == before


def test_real_module_entrypoint_accepts_utf8_pipe_without_secret_arguments(tmp_path: Path) -> None:
    store = store_for_test(tmp_path)
    command = [
        sys.executable,
        "-B",
        "-Xutf8",
        "-m",
        "multi_exchange_trader.credentials_cli",
        "--import-stdin",
        "--store",
        str(store.path),
    ]
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    completed = subprocess.run(
        command,
        input=json.dumps(PAIR).encode(),
        capture_output=True,
        check=False,
        timeout=20,
        env=environment,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["namespaces"] == ["BYBIT_DEMO"]
    assert completed.stderr == b""
    assert b"synthetic" not in completed.stdout and str(tmp_path).encode() not in completed.stdout
    assert store.load().bybit_pair("demo") == (
        PAIR["BYBIT_DEMO_API_KEY"],
        PAIR["BYBIT_DEMO_API_SECRET"],
    )


def test_stdin_publication_failure_keeps_previous_store_and_redacts_persistence_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_for_test(tmp_path)
    store.register(PAIR, expected_revision=None)
    before = store.path.read_bytes()

    def refuse_publish(*args: Any, **kwargs: Any) -> None:
        raise OSError("synthetic-secret /synthetic/private/source")

    monkeypatch.setattr(CredentialStore, "_publish", refuse_publish)
    install_stdin(monkeypatch, b'{"BYBIT_DEMO_API_SECRET":"synthetic-rotated-secret"}')
    assert (
        credentials_cli.main(
            [
                "--import-stdin",
                "--store",
                str(store.path),
                "--expected-revision",
                "1",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip() == "PERSISTENCE_FAILED"
    assert store.path.read_bytes() == before

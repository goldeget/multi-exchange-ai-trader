"""Observed rights, bounded safe diagnostics and absence of effect authority."""

import copy
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from enum import StrEnum
from itertools import permutations
from pathlib import Path

import httpx
import pytest

from multi_exchange_trader.execution.product_permissions import (
    CredentialPermissionProfile,
    evaluate_bybit_permission_profile,
)
from multi_exchange_trader.execution.product_scope import BYBIT_PERMISSION_PROFILE_REVISION

REVISION = BYBIT_PERMISSION_PROFILE_REVISION
DUAL = (("Spot", ("SpotTrade",)), ("ContractTrade", ("Order", "Position")), ("Wallet", ()))
INVALID_CODES = (
    "READ_ONLY_METADATA_INVALID", "PERMISSION_METADATA_INVALID",
    "UNKNOWN_PERMISSION_GROUP", "UNKNOWN_PERMISSION_GRANT",
)
NORMAL_CODES = (
    "READ_ONLY", "WALLET_PERMISSION_METADATA_MISSING", "FORBIDDEN_PERMISSION_GRANT",
    "NO_SUPPORTED_PRODUCT_GRANT",
)


class Text(str):
    pass


class HookText(str):
    def __hash__(self):
        raise AssertionError("subclass hash hook called")

    def __eq__(self, other):
        raise AssertionError("subclass equality hook called")

    def __repr__(self):
        raise AssertionError("subclass repr hook called")


class Tuple(tuple):
    pass


class FrozenSet(frozenset):
    pass


class ProductEnum(StrEnum):
    SPOT = "SPOT"


class Hostile:
    def __getattribute__(self, name):
        raise AssertionError("attribute hook called")

    def __hash__(self):
        raise AssertionError("hash hook called")

    def __eq__(self, other):
        raise AssertionError("equality hook called")

    def __repr__(self):
        raise AssertionError("repr hook called")

    def __str__(self):
        raise AssertionError("string hook called")


def identity(permissions=DUAL, read_only=False):
    from multi_exchange_trader.execution.account_binding import BybitAccountIdentity

    now = datetime(2026, 11, 3, tzinfo=UTC)
    return BybitAccountIdentity("synthetic-uid", "synthetic-parent", False, read_only,
                                permissions, now, now, True)


def evaluate(value):
    return evaluate_bybit_permission_profile(value, profile_revision=REVISION)


def profile(**changes):
    values = {"revision": REVISION, "granted_products": frozenset({"SPOT", "LINEAR"}),
              "forbidden_grants": (), "metadata_complete": True, "blockers": ()}
    return CredentialPermissionProfile(**(values | changes))


def assert_invalid(value, code):
    result = evaluate(value)
    assert result.revision == REVISION
    assert result.granted_products == frozenset()
    assert result.forbidden_grants == ()
    assert result.metadata_complete is False
    assert result.blockers == (code,)


@pytest.mark.parametrize(("permissions", "products"), [
    (DUAL, frozenset({"SPOT", "LINEAR"})),
    ((("Spot", ("SpotTrade",)), ("Wallet", ())), frozenset({"SPOT"})),
    ((("ContractTrade", ("Order", "Position")), ("Wallet", ())), frozenset({"LINEAR"})),
    ((("ContractTrade", ("Order",)), ("Wallet", ())), frozenset()),
    ((("ContractTrade", ("Position",)), ("Wallet", ())), frozenset()),
    ((("Derivatives", ("DerivativesTrade",)), ("Wallet", ())), frozenset()),
    ((("Wallet", ()), ("Spot", ()), ("Affiliate", ())), frozenset()),
])
def test_classifies_observed_products_without_inventing_trade_rights(permissions, products):
    result = evaluate(identity(permissions))
    assert result.granted_products == products
    assert result.metadata_complete is True
    assert result.forbidden_grants == ()
    assert result.blockers == (() if products else ("NO_SUPPORTED_PRODUCT_GRANT",))


@pytest.mark.parametrize(("permissions", "readonly", "complete", "blockers", "forbidden"), [
    (DUAL, True, True, ("READ_ONLY",), ()),
    (DUAL[:-1], False, False, ("WALLET_PERMISSION_METADATA_MISSING",), ()),
    (DUAL[:-1] + (("Wallet", ("Withdraw",)),), False, True,
     ("FORBIDDEN_PERMISSION_GRANT",), (("Wallet", "Withdraw"),)),
    (DUAL[:-1] + (("Options", ("OptionsTrade",)),), True, False,
     ("READ_ONLY", "WALLET_PERMISSION_METADATA_MISSING", "FORBIDDEN_PERMISSION_GRANT"),
     (("Options", "OptionsTrade"),)),
])
def test_blocked_metadata_preserves_observed_dual_products(
    permissions, readonly, complete, blockers, forbidden,
):
    result = evaluate(identity(permissions, readonly))
    assert result.granted_products == frozenset({"SPOT", "LINEAR"})
    assert result.metadata_complete is complete
    assert result.blockers == blockers
    assert result.forbidden_grants == forbidden


def test_each_known_nontrade_grant_is_forbidden_and_all_empty_groups_are_safe():
    from multi_exchange_trader.execution.account_binding import _PERMISSIONS

    allowed = {("ContractTrade", "Order"), ("ContractTrade", "Position"),
               ("Derivatives", "DerivativesTrade"), ("Spot", "SpotTrade")}
    for group, grants in _PERMISSIONS.items():
        assert evaluate(identity(((group, ()), ("Wallet", ())) if group != "Wallet"
                                 else (("Wallet", ()),))).metadata_complete
        for grant in grants:
            if (group, grant) in allowed:
                continue
            permissions = DUAL[:-1] + (("Wallet", ()), (group, (grant,)))
            if group == "Wallet":
                permissions = DUAL[:-1] + (("Wallet", (grant,)),)
            result = evaluate(identity(permissions))
            assert result.granted_products == frozenset({"SPOT", "LINEAR"})
            assert result.forbidden_grants == ((group, grant),)
            assert result.blockers == ("FORBIDDEN_PERMISSION_GRANT",)


def test_tuple_order_is_irrelevant_without_mutating_identity_or_permissions():
    expected = profile(forbidden_grants=(("Options", "OptionsTrade"),
                                         ("Wallet", "AccountTransfer"), ("Wallet", "Withdraw")),
                       blockers=("READ_ONLY", "FORBIDDEN_PERMISSION_GRANT"))
    original = (("Wallet", ("Withdraw", "AccountTransfer")),
                ("ContractTrade", ("Position", "Order")),
                ("Options", ("OptionsTrade",)), ("Spot", ("SpotTrade",)))
    for entries in permutations(original):
        value = identity(entries, True)
        before = value.__dict__.copy()
        assert evaluate(value) == expected
        assert value.__dict__ == before
        assert value.permissions is entries


def test_invalid_shape_and_exact_type_guards_run_before_hostile_hooks():
    bad_shapes = [None, [], (), Tuple(DUAL), Hostile(), (("Wallet", []),),
                  (("Wallet", Tuple()),), (["Wallet", ()],), (("Wallet",),),
                  ((Text("Wallet"), ()),), ((Hostile(), ()),),
                  ((HookText("Wallet"), ()),), (("Wallet", (HookText("Withdraw"),)),),
                  (("Wallet", (Hostile(),)),), (("Wallet", (Text("Withdraw"),)),),
                  (("Wallet", (), "extra"),), (("Wallet", ()), ("Wallet", ())),
                  (("ContractTrade", ("Order", "Order")),), DUAL * 20,
                  (("Wallet", ("Withdraw",) * 5),),
                  (("Spot", ("SpotTrade", "unknown")),),
                  (("secret-unknown", ("a", "b", "c", "d", "e")),)]
    for permissions in bad_shapes:
        assert_invalid(identity(permissions), "PERMISSION_METADATA_INVALID")
    for read_only in (None, 0, 1, "false", Text("False"), Hostile()):
        assert_invalid(identity(DUAL, read_only), "READ_ONLY_METADATA_INVALID")


@pytest.mark.parametrize(("permissions", "code"), [
    ((("synthetic-secret-group", ()),), "UNKNOWN_PERMISSION_GROUP"),
    ((("synthetic-secret-group", ("synthetic-secret-grant",)),), "UNKNOWN_PERMISSION_GROUP"),
    ((("Wallet", ("synthetic-secret-grant",)),), "UNKNOWN_PERMISSION_GRANT"),
])
def test_unknown_metadata_returns_only_fixed_safe_diagnostics(permissions, code):
    value = identity(permissions)
    assert_invalid(value, code)
    rendered = repr(evaluate(value))
    assert "synthetic-secret" not in rendered
    assert "unknown" not in rendered


@pytest.mark.parametrize(("entries", "code"), [
    ((("unknown", ()), ("Wallet", ("Withdraw", "Withdraw"))), "PERMISSION_METADATA_INVALID"),
    ((("Wallet", ("unknown",)), ("Spot", [])), "PERMISSION_METADATA_INVALID"),
    ((("Wallet", ("unknown",)), ("unknown", ())), "UNKNOWN_PERMISSION_GROUP"),
    ((("unknown", ()), ("Spot", ("unknown", "SpotTrade"))), "PERMISSION_METADATA_INVALID"),
    ((("unknown", ("a", "b", "c", "d", "e")), ("Wallet", ())),
     "PERMISSION_METADATA_INVALID"),
])
def test_mixed_fault_priority_is_independent_of_entry_order(entries, code):
    for ordered in permutations(entries):
        assert_invalid(identity(ordered), code)
        assert_invalid(identity(ordered, 1), "READ_ONLY_METADATA_INVALID")


def test_missing_exact_identity_attributes_use_fixed_priority_and_do_not_traverse_others():
    from multi_exchange_trader.execution.account_binding import BybitAccountIdentity

    value = object.__new__(BybitAccountIdentity)
    assert_invalid(value, "READ_ONLY_METADATA_INVALID")
    object.__setattr__(value, "read_only", False)
    assert_invalid(value, "PERMISSION_METADATA_INVALID")
    object.__setattr__(value, "permissions", DUAL)
    assert evaluate(value) == profile()


def test_call_contract_rejects_revision_before_identity_and_never_calls_hooks():
    for revision in (None, True, "unsupported-secret", Text(REVISION), HookText(REVISION), Hostile()):
        with pytest.raises(ValueError) as error:
            evaluate_bybit_permission_profile(Hostile(), profile_revision=revision)
        assert str(error.value) == "SUPPORTED_PERMISSION_PROFILE_REVISION_REQUIRED"
    from multi_exchange_trader.execution.account_binding import BybitAccountIdentity

    class ChildIdentity(BybitAccountIdentity):
        pass

    child = object.__new__(ChildIdentity)
    for value in (None, Hostile(), child):
        with pytest.raises(ValueError) as error:
            evaluate(value)
        assert str(error.value) == "EXACT_BYBIT_ACCOUNT_IDENTITY_REQUIRED"


def test_result_is_frozen_minimal_observation_and_cannot_construct_binding():
    from multi_exchange_trader.execution.account_binding import AuthenticatedAccountBinding

    result = evaluate(identity())
    assert [field.name for field in fields(result)] == [
        "revision", "granted_products", "forbidden_grants", "metadata_complete", "blockers"]
    assert hash(result) == hash(copy.copy(result))
    with pytest.raises(FrozenInstanceError):
        result.metadata_complete = False
    with pytest.raises(ValueError, match="AUTHENTICATED_ACCOUNT_BINDING_REQUIRED"):
        AuthenticatedAccountBinding()


def test_constructor_rejects_nonexact_types_unknown_values_and_noncanonical_forbidden():
    bad = [
        {"revision": Text(REVISION)}, {"revision": "synthetic-secret"},
        {"revision": Hostile()}, {"granted_products": {"SPOT"}},
        {"revision": HookText(REVISION)},
        {"granted_products": FrozenSet({"SPOT"})},
        {"granted_products": frozenset({Text("SPOT")})},
        {"granted_products": frozenset({ProductEnum.SPOT})},
        {"granted_products": frozenset({"OTHER"})}, {"metadata_complete": 1},
        {"metadata_complete": Hostile()}, {"blockers": []},
        {"blockers": Tuple()}, {"blockers": (Hostile(),)},
        {"blockers": (Text("READ_ONLY"),)}, {"blockers": ("synthetic-secret",)},
        {"blockers": ("READ_ONLY", "READ_ONLY")},
        {"forbidden_grants": []}, {"forbidden_grants": Tuple()},
        {"forbidden_grants": (["Wallet", "Withdraw"],)},
        {"forbidden_grants": ((Hostile(), "Withdraw"),)},
        {"forbidden_grants": ((HookText("Wallet"), "Withdraw"),)},
        {"forbidden_grants": (("Wallet", HookText("Withdraw")),)},
        {"forbidden_grants": (("Wallet", Text("Withdraw")),)},
        {"forbidden_grants": (("synthetic-secret", "Withdraw"),)},
        {"forbidden_grants": (("Wallet", "synthetic-secret"),)},
        {"forbidden_grants": (("Spot", "SpotTrade"),)},
        {"forbidden_grants": (("Wallet", "Withdraw"), ("Wallet", "Withdraw")),
         "blockers": ("FORBIDDEN_PERMISSION_GRANT",)},
        {"forbidden_grants": (("Wallet", "Withdraw"), ("Options", "OptionsTrade")),
         "blockers": ("FORBIDDEN_PERMISSION_GRANT",)},
    ]
    for changes in bad:
        with pytest.raises(ValueError) as error:
            profile(**changes)
        assert str(error.value) == "INVALID_CREDENTIAL_PERMISSION_PROFILE"


@pytest.mark.parametrize("code", INVALID_CODES)
def test_constructor_invalid_diagnostic_requires_singleton_empty_incomplete_result(code):
    assert profile(granted_products=frozenset(), metadata_complete=False,
                   blockers=(code,)).blockers == (code,)
    for changes in (
        {"granted_products": frozenset({"SPOT"})}, {"metadata_complete": True},
        {"forbidden_grants": (("Wallet", "Withdraw"),)},
        {"blockers": (code, "READ_ONLY")}, {"blockers": (code, code)},
        {"blockers": (code, INVALID_CODES[(INVALID_CODES.index(code) + 1) % 4])},
    ):
        values = {"granted_products": frozenset(), "metadata_complete": False, "blockers": (code,)}
        with pytest.raises(ValueError):
            profile(**(values | changes))


@pytest.mark.parametrize("changes", [
    {"metadata_complete": False}, {"blockers": ("WALLET_PERMISSION_METADATA_MISSING",)},
    {"forbidden_grants": (("Wallet", "Withdraw"),)},
    {"blockers": ("FORBIDDEN_PERMISSION_GRANT",)}, {"granted_products": frozenset()},
    {"blockers": ("NO_SUPPORTED_PRODUCT_GRANT",)},
    {"metadata_complete": False, "blockers": ("WALLET_PERMISSION_METADATA_MISSING", "READ_ONLY")},
])
def test_constructor_enforces_each_normal_cross_invariant_in_both_directions(changes):
    with pytest.raises(ValueError):
        profile(**changes)


def test_constructor_accepts_every_canonical_normal_blocker_combination():
    for read_only in (False, True):
        for complete in (False, True):
            for forbidden in ((), (("Wallet", "Withdraw"),)):
                for products in (frozenset(), frozenset({"SPOT"}), frozenset({"LINEAR"}),
                                 frozenset({"SPOT", "LINEAR"})):
                    codes = tuple(code for code, include in zip(
                        NORMAL_CODES, (read_only, not complete, bool(forbidden), not products),
                        strict=True) if include)
                    result = profile(granted_products=products, metadata_complete=complete,
                                     forbidden_grants=forbidden, blockers=codes)
                    assert result.blockers == codes


async def test_synthetic_observation_never_authorizes_existing_session_or_legacy_linear():
    from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
    from multi_exchange_trader.execution.protection import ProtectionScope

    value = identity()
    result = evaluate(value)
    assert result.granted_products == frozenset({"SPOT", "LINEAR"})
    assert not value.linear_execution_permitted
    owner = BybitDemoAccountSession(
        api_key="synthetic-key", api_secret="synthetic-secret",
        transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP expected")),
    )
    try:
        for candidate in (value, result, copy.copy(result)):
            with pytest.raises(ValueError, match="AUTHENTICATED_ACCOUNT_BINDING_REQUIRED"):
                owner.require_binding(candidate, scope=ProtectionScope("synthetic-uid", "DEMO"))
    finally:
        await owner.aclose()


def test_light_module_import_does_not_load_identity_private_http_or_native_graph():
    source = str(Path(__file__).parents[2] / "src")
    script = r'''
import sys
sys.path.insert(0, sys.argv[1])
sys.dont_write_bytecode = True
blocked = ("httpx", "socket", "ssl", "urllib", "sqlite3", "ctypes", "nautilus_trader",
           "multi_exchange_trader.account", "multi_exchange_trader.credentials",
           "multi_exchange_trader.credential_resolution", "multi_exchange_trader.settings",
           "multi_exchange_trader.execution.account_binding")
assert not any(name.startswith(blocked) for name in sys.modules)
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError("heavy import forbidden")
sys.meta_path.insert(0, Guard())
import multi_exchange_trader.execution.product_permissions
assert not any(name.startswith(blocked) for name in sys.modules)
print("light import passed")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, source],
        env={"SYSTEMROOT": r"C:\Windows"}, cwd=source,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "light import passed"


@pytest.mark.parametrize("first_use", ["evaluate", "constructor"])
def test_fresh_import_and_first_use_allow_module_loading_but_no_application_effects(first_use):
    source = str(Path(__file__).parents[2] / "src")
    script = r'''
import sys
import os
import io
import builtins
import time
import pathlib
import socket
import sqlite3
import ctypes
import httpx
import asyncio

source, first_use = sys.argv[1:]
sys.path.insert(0, source)
sys.dont_write_bytecode = True
assert "multi_exchange_trader.execution.account_binding" not in sys.modules

def denied(*args, **kwargs):
    raise AssertionError("application effect forbidden")

class EnvironmentGuard:
    __getitem__ = __setitem__ = __delitem__ = __iter__ = __len__ = denied
    get = keys = values = items = copy = denied

class ImportGuard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("nautilus_trader"):
            raise AssertionError("native SDK import forbidden")
        if first_use == "import" and fullname.startswith((
            "multi_exchange_trader.account", "multi_exchange_trader.credentials",
            "multi_exchange_trader.credential_resolution",
            "multi_exchange_trader.execution.account_binding",
        )):
            raise AssertionError("heavy module import forbidden")

def audit(event, args):
    if event == "open":
        filename, mode, flags = args
        if not isinstance(filename, str) or not filename.endswith((".py", ".pyc", ".pyd")):
            raise AssertionError("application file read forbidden")
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("file mutation forbidden")
    if event.startswith(("socket.", "subprocess.", "ctypes.", "sqlite3.connect",
                         "os.system", "os.spawn", "os.exec", "os.mkdir", "os.remove",
                         "os.rename")):
        raise AssertionError("effect forbidden")

def calls(frame, event, arg):
    if event != "call":
        return
    module = frame.f_globals.get("__name__", "")
    # Module and class definitions may load; application function execution may not.
    if module.startswith(("multi_exchange_trader.credentials",
                          "multi_exchange_trader.credential_resolution",
                          "multi_exchange_trader.account.config",
                          "multi_exchange_trader.account.bybit_readonly")):
        if (frame.f_code.co_flags & 0x02 and not frame.f_code.co_name.startswith("<")
                and frame.f_code.co_name != "__create_fn__"):
            raise AssertionError("credential/config/client operation forbidden")
    if module.startswith("multi_exchange_trader.execution"):
        if (frame.f_code.co_name in (
            "load_native_event_decoder", "build_demo_exec_config",
            "authenticate", "configure_native_execution",
        ) or frame.f_code.co_qualname == "BybitDemoAccountSession.__init__"):
            raise AssertionError("session/native operation forbidden")

sys.meta_path.insert(0, ImportGuard())
os.environ = EnvironmentGuard()
os.getenv = os.putenv = os.unsetenv = denied
builtins.open = io.open = denied
time.time = time.monotonic = time.perf_counter = denied
httpx.Client.__init__ = httpx.AsyncClient.__init__ = denied
socket.socket = socket.create_connection = socket.getaddrinfo = denied
sqlite3.connect = ctypes.CDLL = denied
if hasattr(ctypes, "WinDLL"):
    ctypes.WinDLL = denied
sys.addaudithook(audit)
sys.setprofile(calls)

from multi_exchange_trader.execution.product_permissions import (
    CredentialPermissionProfile, evaluate_bybit_permission_profile,
)
assert "multi_exchange_trader.execution.account_binding" not in sys.modules
if first_use == "evaluate":
    # The first evaluation loads the existing graph, then rejects the wrong class.
    try:
        evaluate_bybit_permission_profile(None, profile_revision="BYBIT_SPOT_LINEAR_NO_FUNDS_V1")
    except ValueError as error:
        assert str(error) == "EXACT_BYBIT_ACCOUNT_IDENTITY_REQUIRED"
    else:
        raise AssertionError("identity rejection required")
elif first_use == "constructor":
    result = CredentialPermissionProfile("BYBIT_SPOT_LINEAR_NO_FUNDS_V1",
                                         frozenset({"SPOT"}), (), True, ())
    assert result.granted_products == frozenset({"SPOT"})
if first_use != "import":
    from multi_exchange_trader.execution.account_binding import BybitAccountIdentity
    value = object.__new__(BybitAccountIdentity)
    object.__setattr__(value, "read_only", False)
    object.__setattr__(value, "permissions", (("Spot", ("SpotTrade",)), ("Wallet", ())))
    result = evaluate_bybit_permission_profile(value,
                          profile_revision="BYBIT_SPOT_LINEAR_NO_FUNDS_V1")
    assert result.granted_products == frozenset({"SPOT"})
    assert not result.blockers
assert not any(name.startswith("nautilus_trader") for name in sys.modules)
sys.setprofile(None)
print("first-use effect guards passed")
'''
    isolated_env = {
        "SYSTEMROOT": r"C:\Windows", "TRADER_CREDENTIAL_FILE": "off",
        "TRADER_ACCOUNT_PROFILE_FILE": "off", "BYBIT_ENV_FILE": "off",
        "AI_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, source, first_use], env=isolated_env,
        cwd=source, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "first-use effect guards passed"

"""Private selection consistency, nested revalidation, and isolated import purity."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from enum import StrEnum
from pathlib import Path

import pytest

from multi_exchange_trader.execution.product_scope import (
    BYBIT_PERMISSION_PROFILE_REVISION,
    ProductExecutionPolicy,
    ProductScope,
)
from multi_exchange_trader.execution.protection import ProtectionScope


class Text(str):
    pass


class ProductEnum(StrEnum):
    SPOT = "SPOT"


def policy(**changes):
    values = {
        "scope": ProductScope(ProtectionScope("synthetic-account", "DEMO"), "SPOT"),
        "revision": "policy-v1",
        "allowed_products": frozenset({"SPOT", "LINEAR"}),
        "selected_product": "SPOT",
        "permission_profile_revision": BYBIT_PERMISSION_PROFILE_REVISION,
    }
    return ProductExecutionPolicy(**(values | changes))


@pytest.mark.parametrize("environment", ["DEMO", "LIVE"])
@pytest.mark.parametrize("product", ["SPOT", "LINEAR"])
def test_explicit_selection_retains_account_and_frozen_hashable_scope(environment, product):
    account = ProtectionScope("synthetic-account", environment)
    scope = ProductScope(account, product)
    value = policy(scope=scope, selected_product=product)
    assert scope.account is account
    assert value.scope is scope
    assert value.selected_product == product
    assert hash(value) == hash(policy(scope=scope, selected_product=product))
    with pytest.raises(FrozenInstanceError):
        scope.product = "LINEAR"
    with pytest.raises(FrozenInstanceError):
        value.selected_product = "LINEAR"


def test_product_and_environment_are_independent_distinct_values():
    scopes = {ProductScope(ProtectionScope("synthetic-account", env), product)
              for env in ("DEMO", "LIVE") for product in ("SPOT", "LINEAR")}
    assert len(scopes) == 4


@pytest.mark.parametrize("account_id", ["x", "x" * 256, "synthetic account", "합성계정"])
def test_local_account_identifier_bounds_accept_without_uid_inference(account_id):
    account = ProtectionScope(account_id, "DEMO")
    assert ProductScope(account, "SPOT").account is account


@pytest.mark.parametrize(
    "account_id", [None, True, "", "x" * 257, " x", "x ", "\tx", "x\n",
                   "a\x00b", "a\x1fb", "a\x7fb", Text("x")],
)
def test_scope_revalidates_account_identifier_even_when_protection_scope_was_forged(account_id):
    account = ProtectionScope("synthetic-account", "DEMO")
    object.__setattr__(account, "account_id", account_id)
    with pytest.raises(ValueError):
        ProductScope(account, "SPOT")


@pytest.mark.parametrize("environment", [None, True, "demo", "LIVE ", Text("DEMO")])
def test_scope_revalidates_exact_environment(environment):
    account = ProtectionScope("synthetic-account", "DEMO")
    object.__setattr__(account, "environment", environment)
    with pytest.raises(ValueError):
        ProductScope(account, "SPOT")


def test_scope_rejects_account_subclass_and_non_scope():
    class ChildAccount(ProtectionScope):
        pass

    for account in (None, True, "synthetic-account", ChildAccount("x", "DEMO")):
        with pytest.raises(ValueError):
            ProductScope(account, "SPOT")


@pytest.mark.parametrize("product", [None, True, "spot", "SPOT ", Text("SPOT"), ProductEnum.SPOT])
def test_scope_and_policy_reject_non_exact_product(product):
    with pytest.raises(ValueError):
        ProductScope(ProtectionScope("x", "DEMO"), product)
    with pytest.raises(ValueError):
        policy(selected_product=product)
    with pytest.raises(ValueError):
        policy(allowed_products=frozenset({product}))


@pytest.mark.parametrize("products", [frozenset({"SPOT"}), frozenset({"SPOT", "LINEAR"})])
def test_allowed_products_are_explicit_immutable_supported_set(products):
    assert policy(allowed_products=products).allowed_products is products


def test_allowed_products_reject_mutable_empty_subclass_and_unknown_sets():
    class FrozenProducts(frozenset):
        pass

    for products in (None, True, {"SPOT"}, ["SPOT"], ("SPOT",), frozenset(),
                     FrozenProducts({"SPOT"}), frozenset({"SPOT", "INVERSE"})):
        with pytest.raises(ValueError):
            policy(allowed_products=products)


def test_selected_product_must_be_in_allowed_and_equal_scope_product():
    with pytest.raises(ValueError):
        policy(allowed_products=frozenset({"LINEAR"}))
    with pytest.raises(ValueError):
        policy(selected_product="LINEAR")


@pytest.mark.parametrize("revision", ["a", "a" * 96, "A0_.:/-z"])
def test_policy_revision_accepts_local_ascii_envelope(revision):
    assert policy(revision=revision).revision == revision


@pytest.mark.parametrize(
    "revision", [None, True, "", "a" * 97, " a", "a ", "a\n", "_a", "/a", "é", "aé",
                 "a@b", Text("a")],
)
def test_policy_revision_rejects_non_exact_or_invalid_ascii(revision):
    with pytest.raises(ValueError):
        policy(revision=revision)


@pytest.mark.parametrize("revision", [None, True, "", "BYBIT_SPOT_LINEAR_NO_FUNDS_V2",
                                      Text("BYBIT_SPOT_LINEAR_NO_FUNDS_V1")])
def test_only_supported_exact_permission_profile_revision_is_accepted(revision):
    with pytest.raises(ValueError):
        policy(permission_profile_revision=revision)


def test_policy_requires_every_field_and_exact_scope():
    with pytest.raises(TypeError):
        ProductScope(ProtectionScope("x", "DEMO"))
    with pytest.raises(TypeError):
        ProductExecutionPolicy(ProductScope(ProtectionScope("x", "DEMO"), "SPOT"))

    class ChildScope(ProductScope):
        pass

    for scope in (None, True, "scope", ChildScope(ProtectionScope("x", "DEMO"), "SPOT")):
        with pytest.raises(ValueError):
            policy(scope=scope)


@pytest.mark.parametrize(
    ("target", "field", "bad"),
    [("scope", "product", "spot"), ("scope", "account", None),
     ("account", "account_id", "x\x00y"), ("account", "environment", Text("DEMO"))],
)
def test_policy_revalidates_forged_nested_scope_and_account(target, field, bad):
    scope = ProductScope(ProtectionScope("synthetic-account", "DEMO"), "SPOT")
    object.__setattr__(scope if target == "scope" else scope.account, field, bad)
    with pytest.raises(ValueError):
        policy(scope=scope)


def test_scope_rejects_missing_forged_account_fields_with_controlled_error():
    for missing in ("account_id", "environment"):
        account = ProtectionScope("synthetic-account", "DEMO")
        object.__delattr__(account, missing)
        with pytest.raises(ValueError):
            ProductScope(account, "SPOT")
    with pytest.raises(ValueError):
        ProductScope(object.__new__(ProtectionScope), "SPOT")


def test_policy_rejects_missing_forged_nested_fields_with_controlled_error():
    for missing in ("account", "product"):
        scope = ProductScope(ProtectionScope("synthetic-account", "DEMO"), "SPOT")
        object.__delattr__(scope, missing)
        with pytest.raises(ValueError):
            policy(scope=scope)
    with pytest.raises(ValueError):
        policy(scope=object.__new__(ProductScope))
    scope = ProductScope(ProtectionScope("synthetic-account", "DEMO"), "SPOT")
    object.__setattr__(scope, "account", object.__new__(ProtectionScope))
    with pytest.raises(ValueError):
        policy(scope=scope)


def test_nested_scope_validation_checks_type_before_untrusted_attributes():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError("untrusted attribute hook must not run")

    with pytest.raises(ValueError):
        ProductScope(Hostile(), "SPOT")
    with pytest.raises(ValueError):
        policy(scope=Hostile())


@pytest.mark.parametrize("include_scope", [False, True])
def test_fresh_isolated_imports_have_no_private_native_network_or_application_io(include_scope):
    source = str(Path(__file__).parents[2] / "src")
    script = r'''
import sys
import os
import builtins
import io

source = sys.argv[1]
include_scope = sys.argv[2] == "True"
sys.path.insert(0, source)
blocked = ("httpx", "requests", "socket", "ssl", "urllib", "sqlite3", "ctypes",
           "nautilus_trader", "multi_exchange_trader.credentials",
           "multi_exchange_trader.account", "multi_exchange_trader.settings",
           "multi_exchange_trader.execution.account_binding")
if not include_scope:
    blocked += ("multi_exchange_trader.execution",)
allowed_app_modules = {"multi_exchange_trader", "multi_exchange_trader.trading_product"}
if include_scope:
    allowed_app_modules.update({"multi_exchange_trader.execution",
                               "multi_exchange_trader.execution.product_scope",
                               "multi_exchange_trader.execution.protection"})
assert not any(name.startswith(blocked) for name in sys.modules)

class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(blocked) or
                (fullname.startswith("multi_exchange_trader") and
                 fullname not in allowed_app_modules)):
            raise AssertionError("forbidden import: " + fullname)

def denied(*args, **kwargs):
    raise AssertionError("application file/environment access forbidden")

class EnvironmentGuard:
    __getitem__ = __setitem__ = __delitem__ = __iter__ = __len__ = denied
    get = keys = values = items = copy = denied

os.environ = EnvironmentGuard()
os.getenv = os.putenv = os.unsetenv = denied
builtins.open = io.open = denied

def deny_effects(event, args):
    if event == "open":
        filename, mode, flags = args
        if not isinstance(filename, str) or not filename.endswith((".py", ".pyc")):
            raise AssertionError("application file I/O forbidden")
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("file mutation forbidden")
    if event.startswith(("socket.", "subprocess.", "ctypes.", "sqlite3.", "os.system",
                         "os.spawn", "os.exec", "os.mkdir", "os.remove", "os.rename")):
        raise AssertionError("effect forbidden: " + event)

sys.meta_path.insert(0, Guard())
sys.dont_write_bytecode = True
sys.addaudithook(deny_effects)
from multi_exchange_trader.trading_product import InstrumentKey, bybit_native_instrument_id
assert bybit_native_instrument_id(InstrumentKey("BYBIT", "SPOT", "BTCUSDT")) == \
    "BTCUSDT-SPOT.BYBIT"
if include_scope:
    from multi_exchange_trader.execution.product_scope import ProductScope
    from multi_exchange_trader.execution.protection import ProtectionScope
    scope = ProductScope(ProtectionScope("synthetic-import-account", "DEMO"), "SPOT")
    assert scope.product == "SPOT"
assert not any(name.startswith(blocked) for name in sys.modules)
print("isolated import and pure construction passed")
'''
    isolated_env = {
        "SYSTEMROOT": r"C:\Windows",
        "TRADER_CREDENTIAL_FILE": "off",
        "TRADER_ACCOUNT_PROFILE_FILE": "off",
        "BYBIT_ENV_FILE": "off",
        "AI_ENV_FILE": "off",
        "ANTHROPIC_ENV_FILE": "off",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, source, str(include_scope)],
        env=isolated_env, cwd=source, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated import and pure construction passed"

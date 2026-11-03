"""Explicit synthetic Task1 values only; never an operational policy/source."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogSource,
    InstrumentObservation,
    parse_bybit_instrument_response,
)
from multi_exchange_trader.trading_product import InstrumentKey, TradingProduct

NOW = datetime(2026, 11, 3, tzinfo=UTC)
EXPIRY = NOW + timedelta(seconds=1)
DIGEST = "a" * 64


def scope_v2() -> c.RiskScopeV2:
    return c.RiskScopeV2(
        ProductScope(ProtectionScope("FIXTURE_ACCOUNT", "DEMO"), "LINEAR"),
        InstrumentKey("BYBIT", "LINEAR", "XAAUSDT"),
        StrategyReference("FIXTURE_STRATEGY", "FIXTURE_STRATEGY_V1"),
    )


def instrument_v2(product: TradingProduct = "LINEAR") -> InstrumentObservation:
    row: dict[str, object] = {
        "symbol": "XAAUSDT",
        "baseCoin": "XAA",
        "quoteCoin": "USDT",
        "status": "Trading",
        "priceFilter": {"tickSize": "0.1", "minPrice": "0.1", "maxPrice": "1000"},
        "lotSizeFilter": {
            "qtyStep": "0.1",
            "minOrderQty": "0.1",
            "maxOrderQty": "10",
            "maxMktOrderQty": "0.9",
            "minNotionalValue": "5",
            "basePrecision": "0.1",
            "quotePrecision": "0.1",
            "minOrderAmt": "5",
            "maxLimitOrderQty": "10",
            "maxMarketOrderQty": "0.9",
        },
        "settleCoin": "USDT",
        "contractType": "LinearPerpetual",
        "priceScale": "1",
        "isPreListing": False,
        "leverageFilter": {"minLeverage": "1", "maxLeverage": "150", "leverageStep": "0.1"},
    }
    return parse_bybit_instrument_response(
        {
            "retCode": 0,
            "result": {"category": product.lower(), "list": [row]},
            "time": 1793663999000,
        },
        expected=InstrumentKey("BYBIT", product, "XAAUSDT"),
        source=CatalogSource("FIXTURE_CATALOG", "FIXTURE", INSTRUMENT_ENDPOINT, NOW),
    )


def policy_v2() -> c.LinearRiskPolicyV2:
    return c.LinearRiskPolicyV2(
        scope_v2(),
        "FIXTURE_POLICY_V1",
        "LOSS_FIXED",
        c.LinearCapitalCapsV2(
            Decimal(500), Decimal(1000), "LINEAR_MARK_GROSS_AT_ADMISSION_V1", "NOT_REQUIRED", None
        ),
        Decimal(1),
        Decimal(150),
        Decimal(12),
        Decimal(100),
        Decimal(200),
        3,
        4,
        Decimal(300),
        Decimal(1000),
        Decimal(60),
        "ISOLATED",
        "ONE_WAY",
        Decimal("0.01"),
        Decimal(2),
        Decimal(10),
    )


def observation_ref_v2(identifier: str = "FIXTURE_FEE") -> c.RiskObservationRefV2:
    return c.RiskObservationRefV2(
        identifier,
        "FIXTURE_SOURCE",
        "FIXTURE",
        "FIXTURE_SOURCE_V1",
        NOW - timedelta(seconds=1),
        NOW,
        DIGEST,
    )


def computation_ref_v2() -> c.RiskComputationRefV2:
    return c.RiskComputationRefV2("FIXTURE_COST", "FIXTURE_COST_V1", "FIXTURE", NOW, DIGEST)


def proposal_v2() -> c.LinearEntryProposalV2:
    return c.LinearEntryProposalV2(
        scope_v2(),
        "LONG",
        Decimal(90),
        c.FixedLimitPriceV2("BUY", Decimal(100), "FIXTURE_QUOTE", EXPIRY),
    )


def costs_v2() -> c.LinearCostAllowanceV2:
    return c.LinearCostAllowanceV2(
        scope_v2(),
        computation_ref_v2(),
        c.risk_policy_sha256_v2(policy_v2()),
        instrument_v2().specification_revision,
        "FIXTURE_QUOTE",
        "LONG",
        Decimal(100),
        Decimal(90),
        observation_ref_v2(),
        "USDT",
        "FIXTURE_FEE_V1",
        observation_ref_v2("FIXTURE_FUNDING"),
        Decimal("-0.001"),
        NOW + timedelta(hours=1),
        NOW + timedelta(hours=2),
        "FIXTURE_FUNDING_V1",
        "FIXTURE_GAP_V1",
        Decimal(1),
        Decimal("0.5"),
        Decimal("0.5"),
        EXPIRY,
        "FIXTURE_COST_V1",
    )


def envelope_v2() -> c.FixedLimitEnvelopeV2:
    return c.FixedLimitEnvelopeV2(
        2,
        scope_v2(),
        c.pin_instrument_v2(instrument_v2(), catalog_revision="FIXTURE_CATALOG_V1"),
        "FIXTURE_POLICY_V1",
        c.risk_policy_sha256_v2(policy_v2()),
        "BUY",
        Decimal(1),
        Decimal(100),
        "LIMIT",
        "IOC",
        "ENTRY",
        False,
        "FIXTURE_QUOTE",
        EXPIRY,
    )


def sizing_v2() -> c.LinearSizedEntryV2:
    return c.LinearSizedEntryV2(
        scope_v2(),
        NOW,
        c.risk_policy_sha256_v2(policy_v2()),
        envelope_v2(),
        computation_ref_v2(),
        c.cost_allowance_sha256_v2(costs_v2()),
        Decimal(90),
        Decimal(10),
        Decimal(2),
        Decimal(12),
        Decimal(12),
        Decimal(2),
        Decimal("0.5"),
        "LINEAR_FIXED_LIMIT_LOSS_SIZE_V2",
    )

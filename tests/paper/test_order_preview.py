"""Linux native qualification of additive review/confirm using the existing PAPER ledger."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from multi_exchange_trader.paper.store import PaperStore


def draft():
    return {
        "scenario_id": "manual", "product": "SYNTHETIC_PERPETUAL", "order_type": "MARKET",
        "config_revision": "PAPER-v1",
        "command": {"kind": "enter", "instrument": "BTC-PAPER", "side": "long",
                    "quantity": "0.001", "leverage": 3},
    }


def test_review_has_no_effect_confirm_retries_original_after_expiry_restart(tmp_path):
    from multi_exchange_trader.paper.order_preview import OrderReviews

    paper = PaperStore(tmp_path / "paper.sqlite3")
    before = paper.command("manual", "seed", {"kind": "create"})
    path = tmp_path / "reviews.sqlite3"
    reviews = OrderReviews(path, paper)
    review = reviews.preview(draft())
    assert paper.state("manual") == before
    assert review["costs"]["estimated_fee"] != "0.00000000"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: reviews.confirm(review["preview_id"], "confirmed"), range(3)))
    assert results[0] == results[1] == results[2]
    assert len(paper.state("manual")["fills"]) == 1
    expired_clock = lambda: datetime.now(UTC) + timedelta(hours=1)
    restarted = OrderReviews(path, PaperStore(paper.path), clock=expired_clock)
    assert restarted.confirm(review["preview_id"], "confirmed") == results[0]
    with pytest.raises(ValueError, match="idempotency"):
        restarted.confirm(review["preview_id"], "different")


def test_review_changed_balance_price_or_expiry_rejects_without_command(tmp_path):
    from multi_exchange_trader.paper.order_preview import OrderReviews

    paper = PaperStore(tmp_path / "paper.sqlite3")
    paper.command("manual", "seed", {"kind": "create"})
    reviews = OrderReviews(tmp_path / "reviews.sqlite3", paper)
    review = reviews.preview(draft())
    paper.command("manual", "other", {"kind": "protect", "instrument": "BTC-PAPER", "sl": None, "tp": None})
    with pytest.raises(ValueError, match="revision"):
        reviews.confirm(review["preview_id"], "changed")
    review = reviews.preview(draft())
    reviews.clock = lambda: datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(ValueError, match="expired"):
        reviews.confirm(review["preview_id"], "expired")
    assert paper.state("manual")["fills"] == []


def test_review_rejects_unqualified_products_and_legacy_bypass(tmp_path):
    from multi_exchange_trader.paper.order_preview import OrderReviews

    paper = PaperStore(tmp_path / "paper.sqlite3")
    paper.command("manual", "seed", {"kind": "create"})
    reviews = OrderReviews(tmp_path / "reviews.sqlite3", paper)
    for changed in ({"product": "SPOT"}, {"order_type": "LIMIT"}, {"config_revision": "PAPER-v2"}):
        with pytest.raises(ValueError):
            reviews.preview(draft() | changed)
    with pytest.raises(ValueError):
        paper.command("manual", "bypass", {"kind": "spot_limit", "instrument": "BTC-PAPER", "price": "60000.0"})
    assert len(paper.audit("manual")["events"]) == 1

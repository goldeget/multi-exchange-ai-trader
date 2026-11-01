"""Native spot review/confirm and server-side placement-boundary qualification."""

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI


async def test_spot_preview_resting_fee_reservation_confirm_and_api_bypass(tmp_path):
    from multi_exchange_trader.paper.api import install as install_paper
    from multi_exchange_trader.paper.order_preview import OrderReviews
    from multi_exchange_trader.paper.spot_api import install as install_spot
    from multi_exchange_trader.paper.spot_store import SpotPaperStore
    from multi_exchange_trader.paper.store import PaperStore

    app = FastAPI()
    app.state.paper_store = PaperStore(tmp_path / "perpetual.sqlite3")
    spot = SpotPaperStore(tmp_path / "spot.sqlite3")
    app.state.spot_paper_store = spot
    app.state.spot_paper_qualified = True
    reviews = OrderReviews(tmp_path / "review.sqlite3", app.state.paper_store, spot=spot, spot_qualified=True)
    app.state.order_reviews = reviews
    install_paper(app)
    install_spot(app)
    placement = {"kind": "order", "instrument": "BTC-PAPER", "side": "buy", "quantity": "0.010",
                 "order_type": "LIMIT", "limit_price": "59000.0"}
    route = "/api/v1/paper/spot/scenarios/manual/commands"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        seeded = await client.post(route, json={"idempotency_key": "seed", "command": {"kind": "create"}})
        assert seeded.status_code == 200
        assert (await client.post(route, json={"idempotency_key": "bypass", "command": placement})).status_code == 409
        preview = await client.post("/api/v1/paper/order-previews", json={"scenario_id": "manual",
            "product": "SPOT", "order_type": "LIMIT", "config_revision": "SPOT-PAPER-v2", "command": placement})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["costs"]["estimated_fee"] == "0.23600000"
        assert body["costs"]["charged_fee_at_preview"] == "0.00000000"
        assert body["order_state_after"]["status"] == "ACCEPTED"
        assert spot.state("manual")["orders"] == []
        confirmation = {"preview_id": body["preview_id"], "idempotency_key": "same"}
        confirmed = await client.post("/api/v1/paper/order-confirmations", json=confirmation)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["state"]["fills"] == []
        assert confirmed.json()["state"]["orders"][0]["status"] == "ACCEPTED"
        reviews.clock = lambda: datetime.now(UTC) + timedelta(hours=1)
        reviews.spot_qualified = False
        app.state.spot_paper_qualified = False
        replayed = await client.post("/api/v1/paper/order-confirmations", json=confirmation)
        assert replayed.json() == confirmed.json()
        cancelled = await client.post(route, json={"idempotency_key": "cancel", "command": {
            "kind": "cancel", "instrument": "BTC-PAPER", "order_id": "S000001"}})
        assert cancelled.status_code == 200
        assert cancelled.json()["orders"][0]["status"] == "CANCELED"
        assert app.state.paper_store.scenarios() == []

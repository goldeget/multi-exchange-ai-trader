"""Public read evidence only. Run only where access to configured hosts is permitted."""

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from multi_exchange_trader.market_data.adapters import binance, bybit
from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.models import MarketType

ALLOWED_URLS = {
    *binance.TICKER_URLS.values(),
    *binance.CANDLE_URLS.values(),
    bybit.TICKER_URL,
    bybit.CANDLE_URL,
}
MAX_AGE_SECONDS = 120


def utc_now() -> datetime:
    return datetime.now(UTC)


def safe_denial_evidence(response: httpx.Response) -> dict[str, str]:
    """Recognize bounded phrases; never copy arbitrary provider diagnostics."""
    text = response.content[:8192].decode("utf-8", errors="replace").lower()
    for phrase, category in (
        ("restricted location", "geographic_restriction"),
        ("block access from your country", "geographic_restriction"),
        ("too many requests", "rate_limit"),
        ("web application firewall", "waf"),
    ):
        if phrase in text:
            return {"denial_category": category, "safe_evidence": phrase}
    return {"denial_category": "unknown", "safe_evidence": "unclassified denial"}


class EvidenceClient(httpx.AsyncClient):
    """Record safe metadata, preserve environment proxy/TLS, and stop denied hosts."""

    def __init__(
        self, records: list[dict[str, Any]], transport: httpx.AsyncBaseTransport | None
    ) -> None:
        super().__init__(transport=transport, trust_env=True, verify=True)
        self.records = records
        self.denied: set[str] = set()

    async def get(self, url: Any, **kwargs: Any) -> httpx.Response:
        if str(url) not in ALLOWED_URLS:
            raise MarketDataUnavailable("host_denied", "Endpoint is outside fixed public allowlist")
        target = httpx.URL(url)
        record: dict[str, Any] = {
            "host": target.host,
            "path": target.path,
            "started_at": utc_now().isoformat(),
            "http_status": None,
        }
        self.records.append(record)
        started = perf_counter()
        try:
            if target.host in self.denied:
                record["skipped"] = True
                raise MarketDataUnavailable("host_denied", "Host was denied earlier in this run")
            kwargs["timeout"] = httpx.Timeout(5)
            response = await super().get(url, **kwargs)
            record["http_status"] = response.status_code
            if response.status_code in (403, 451):
                self.denied.add(target.host)
                record["denial_source"] = "upstream_http"
                record.update(safe_denial_evidence(response))
                raise MarketDataUnavailable("host_denied", "Host denied request")
            return response
        except httpx.HTTPError as exc:
            # Proxy diagnostics may contain credentials. Inspect only to classify; never serialize.
            denied = isinstance(exc, httpx.ProxyError) and "403" in str(exc)
            if denied:
                self.denied.add(target.host)
                record["denial_source"] = "proxy_connect"
            reason = (
                "host_denied"
                if denied
                else "timeout"
                if isinstance(exc, httpx.TimeoutException)
                else "transport"
            )
            record["failure"] = reason
            raise MarketDataUnavailable(reason, "Public request could not complete") from None
        except MarketDataUnavailable as exc:
            record["failure"] = exc.reason
            raise
        finally:
            record["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
            record["ended_at"] = utc_now().isoformat()


def recent(value: datetime, fetched: datetime) -> bool:
    return -5 <= (fetched - value).total_seconds() <= MAX_AGE_SECONDS


async def run(transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict[str, Any], int]:
    """Return all eight markets; an injected transport is only for local unit tests."""
    report: dict[str, Any] = {
        "scope": "public market matrix only; not complete Phase 2A acceptance",
        "started_at": utc_now().isoformat(),
        "evidence_mode": "live_network" if transport is None else "injected_transport",
        "code_sha": (
            await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        ).stdout.strip(),
        "freshness_seconds": MAX_AGE_SECONDS,
        "interval": "1m",
        "limit": 2,
        "requests": [],
        "markets": [],
    }
    async with EvidenceClient(report["requests"], transport) as client:
        for exchange, factory in (
            ("binance", binance.BinanceAdapter),
            ("bybit", bybit.BybitAdapter),
        ):
            adapter = factory(client)
            markets: tuple[MarketType, ...] = ("spot", "perpetual")
            for market in markets:
                for symbol in ("BTCUSDT", "ETHUSDT"):
                    row: dict[str, Any] = {
                        "exchange": exchange,
                        "market_type": market,
                        "symbol": symbol,
                    }
                    for kind in ("ticker", "candles"):
                        request_index = len(report["requests"])
                        try:
                            if kind == "ticker":
                                ticker = await adapter.get_ticker(symbol, market)
                                if ticker.last_price <= 0:
                                    raise MarketDataUnavailable("malformed", "Nonpositive price")
                                result = ticker.model_dump(mode="json")
                                result["freshness_basis"] = (
                                    "provider_timestamp"
                                    if ticker.observed_at
                                    else "receipt_only; source has no observation timestamp"
                                )
                                valid = ticker.last_price > 0 and (
                                    ticker.observed_at is None
                                    or recent(ticker.observed_at, ticker.fetched_at)
                                )
                            else:
                                candles = await adapter.get_candles(symbol, market, "1m", 2)
                                if not candles:
                                    raise MarketDataUnavailable("malformed", "Empty candle batch")
                                fetched = utc_now()
                                source = (
                                    binance.CANDLE_URLS[market]
                                    if exchange == "binance"
                                    else bybit.CANDLE_URL
                                )
                                result = {
                                    "count": len(candles),
                                    "items": [candle.model_dump(mode="json") for candle in candles],
                                    "source": source,
                                    "fetched_at": fetched.isoformat(),
                                    "latest_timestamp": (
                                        candles[-1].timestamp.isoformat() if candles else None
                                    ),
                                }
                                valid = bool(candles) and recent(candles[-1].timestamp, fetched)
                            result["status"] = "valid" if valid else "stale"
                            row[kind] = result
                        except MarketDataUnavailable as exc:
                            category = {
                                "malformed": "schema",
                                "host_denied": "blocked",
                                "transport": "transport",
                                "timeout": "timeout",
                            }.get(exc.reason, "provider")
                            row[kind] = {"status": category, "reason": exc.reason}
                        row[kind]["request_index"] = request_index
                    statuses = {row["ticker"]["status"], row["candles"]["status"]}
                    row["status"] = (
                        "valid"
                        if statuses == {"valid"}
                        else "blocked"
                        if "blocked" in statuses
                        else "failed"
                    )
                    report["markets"].append(row)
    report["ended_at"] = utc_now().isoformat()
    passed = all(row["status"] == "valid" for row in report["markets"])
    report["matrix_passed"] = passed
    report["live_verified"] = transport is None and passed
    return report, 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("work/phase2a-live.json"))
    args = parser.parse_args()
    report, code = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence saved to {args.output}; matrix_passed={report['matrix_passed']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

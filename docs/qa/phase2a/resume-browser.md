# Phase 2-A resumed browser QA

**Normal-data desktop/mobile QA: BLOCKED.** This run supplies supplementary evidence for the actual local-backend-unavailable state only. It does not qualify live prices, candle rendering, provider connectivity or recovery.

## Setup and boundary

Recorded 2026-10-30 UTC using installed Chromium 154.0.8037.57 and Python Playwright. The frontend ran at `http://127.0.0.1:5173/` with no preview query, request interception, response injection or sample data. Browser contexts used desktop 1440×1000 and mobile 390×844, device scale factor 1. Screenshots are full-page captures; their image heights exceed the viewport heights.

The backend was deliberately stopped: the configured exchange hosts were absent from the enforced cloud network policy, so real-mode polling through a running backend would attempt hosts outside the authorized policy. Vite's actual proxy to `127.0.0.1:8000` returned HTTP 500 because the connection was refused. These HTTP responses describe local proxy failures, not responses from an exchange. No exchange host was contacted by this browser QA. No paid browser connector was used.

## Observed results

Both viewport sizes showed the Korean connection error for all four ticker rows and the chart, four unavailable prices (`—`), no source timestamps and no chart canvas. The empty-candle message remained visible. OKX was disabled, and no OKX request occurred. The synthetic preview banner was absent.

Selecting ETH, perpetual and 1h, then selecting the Bybit ETH row, updated the chart heading to `ETH / USDT Bybit · 무기한 · 1h` and selected that row at both sizes. The actual unavailable-data presentation remained consistent. Browser page-error events were empty; expected console resource errors accompanied the local HTTP 500 responses.

Document scroll widths equaled the viewport widths (1440 and 390). No element crossed either horizontal viewport edge in the initial or selected states. Visual inspection of the desktop initial and mobile selected screenshots found readable controls, error text and empty-chart messaging; the mobile chart appears below the ticker panel with vertical scrolling.

| Viewport | Initial BTC / Binance / spot / 15m | Selected ETH / Bybit / perpetual / 1h |
|---|---|---|
| 1440×1000 | [Screenshot](resume-1440-spot-btc-unavailable.png) | [Screenshot](resume-1440-perp-bybit-eth-1h-unavailable.png) |
| 390×844 | [Screenshot](resume-390-spot-btc-unavailable.png) | [Screenshot](resume-390-perp-bybit-eth-1h-unavailable.png) |

[Recorded DOM states, request URLs and HTTP statuses](resume-browser.json) include the checked commit, UTC recording time and browser version.

## Outstanding normal-data checks

Resume only after the configured exchange hosts are authorized by the enforced network policy and the read-only API matrix succeeds. Run the backend and repeat both viewport sizes without interception or preview. Still unverified: successful initial loading, populated real prices and candle/volume charts, source timestamps and delay, all live venue/market/instrument combinations, stale-after-success presentation and recovery after an actual upstream interruption. No normal-data browser PASS or Phase 3 readiness is claimed.

# Public market dashboard

This milestone is a public-read-only market dashboard for Binance and Bybit data. It does not include an AI workflow or a trading engine. The PAPER engine is not implemented. OKX is disabled and unverified, and the app makes no OKX requests. No exchange API keys are required.

Live provider access requires the Cloud Environment network draft to be reviewed and published. Configuring network access does not enable OKX; it remains disabled until its routes and data contract are verified.

## Install

Use the existing checkout at `/workspace/multi-exchange-ai-trader`:

```sh
cd /workspace/multi-exchange-ai-trader
UV_CACHE_DIR=/tmp/multi-exchange-ai-trader-uv-cache uv sync --locked
npm ci --cache /tmp/multi-exchange-ai-trader-npm-cache --prefix apps/web
```

## Start and check health

Start the backend in one terminal:

```sh
cd /workspace/multi-exchange-ai-trader
UV_CACHE_DIR=/tmp/multi-exchange-ai-trader-uv-cache uv run uvicorn multi_exchange_trader.main:app --host 127.0.0.1 --port 8000
```

Check that it is ready from another terminal:

```sh
curl -fsS http://127.0.0.1:8000/api/v1/health
```

The response is `{"status":"ok"}`.

Start the frontend in a second terminal:

```sh
cd /workspace/multi-exchange-ai-trader
npm run dev --prefix apps/web -- --host 127.0.0.1 --port 5173
```

## Synthetic preview

Open `http://127.0.0.1:5173/?preview=1`. This opt-in view uses deterministic synthetic sample data, shows a persistent synthetic-data label, and makes zero API calls. Do not treat the sample values as market quotes.

Without `?preview=1`, the dashboard requests public data from the local backend. Provider connectivity depends on the reviewed and published Cloud Environment network draft.

## CI

GitHub Actions runs the locked Python and npm checks, scans repository history with Gitleaks, and runs GitHub dependency review on pull requests. Jobs use read-only permissions; no exchange credentials or live exchange calls are used. Dependency review depends on the files recognized by GitHub's dependency graph and may not cover every transitive dependency in `uv.lock`; the locked install and Python checks run independently.

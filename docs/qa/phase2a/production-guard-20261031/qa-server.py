"""External QA instrumentation; no new public endpoint or reset/probe."""
import sys, os, json
from pathlib import Path
from contextlib import asynccontextmanager
import uvicorn
sys.path.insert(0,str(Path.cwd()/'src'))
from multi_exchange_trader.api import create_app

assert os.environ['MARKET_DATA_BLOCKED_HOSTS']=='fapi.binance.com,api.bybit.com'
app=create_app()
original=app.router.lifespan_context
counts={}
@asynccontextmanager
async def instrument(application):
    async with original(application):
        async def outgoing(request):
            host=request.url.host
            counts[host]=counts.get(host,0)+1
            Path('work/production-guard/outgoing-counts.json').write_text(json.dumps(counts,indent=2)+'\n')
            assert host not in {'fapi.binance.com','api.bybit.com'}, 'preblocked host reached send'
        application.state.public_market_client.event_hooks['request'].append(outgoing)
        Path('work/production-guard/outgoing-counts.json').write_text('{}\n')
        yield
app.router.lifespan_context=instrument
uvicorn.run(app,host='127.0.0.1',port=8000)

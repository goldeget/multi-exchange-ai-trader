const {chromium}=require('playwright');const fs=require('fs');const assert=require('node:assert/strict');
const out='docs/qa/phase2a/production-guard-20261031';fs.mkdirSync(out,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium'});
const results=await Promise.all([['desktop',1440,1000],['mobile',390,844]].map(async([name,width,height])=>{
 const r={name,viewport:{width,height},started_at_utc:new Date().toISOString(),mode:'actual Binance spot; configured production denial guard; local chart instrumentation only',checks:[],responses:[],pageerrors:[],requests:{},screens:[]};
 const context=await browser.newContext({viewport:{width,height},hasTouch:name==='mobile'});const page=await context.newPage();
 page.on('pageerror',e=>r.pageerrors.push(e.message));
 page.on('request',req=>{if(req.url().includes('/api/v1/market/'))r.requests[req.url()]=(r.requests[req.url()]||0)+1;});
 page.on('response',async res=>{if(res.url().includes('/api/v1/market/'))try{r.responses.push({at_utc:new Date().toISOString(),url:res.url(),http:res.status(),data:await res.json()})}catch{}});
 // Observe actual chart APIs. No market response is replaced or injected.
 await page.route('**/src/chart/CandleChart.tsx*',async route=>{const res=await route.fetch();let s=await res.text();const match=s.match(/const (series\d*) = (chart\d*)\.addSeries/);assert.ok(match);const chartName=match[2];s=s.replace(match[0],`globalThis.__qaCharts ??= [];globalThis.__qaRemoved ??= 0;globalThis.__qaCharts.push(${chartName});const remove = ${chartName}.remove.bind(${chartName});${chartName}.remove = () => {globalThis.__qaRemoved++;remove();};${match[0]}`);await route.fulfill({response:res,body:s});});
 const chart=page.locator('.chart');
 const state=()=>page.evaluate(()=>({created:globalThis.__qaCharts?.length||0,removed:globalThis.__qaRemoved||0,range:globalThis.__qaCharts?.at(-1)?.timeScale().getVisibleLogicalRange(),height:document.querySelector('.chart').getBoundingClientRect().height,tables:document.querySelectorAll('.chart table').length,width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
 const blockedCounts=()=>Object.fromEntries(Object.entries(r.requests).filter(([url])=>url.includes('exchange=bybit')||url.includes('market_type=perpetual')));
 const shot=async tag=>{const path=`${out}/${name}-${tag}.png`;await page.screenshot({path,fullPage:true});r.screens.push(path);};
 const normal=async()=>{await page.getByText('캔들 수신 완료',{exact:true}).waitFor({timeout:25000});await page.getByText(/운영 설정으로 비활성화/).first().waitFor();};
 try{
  await page.goto('http://127.0.0.1:5173/');await normal();await shot('normal');
  await chart.scrollIntoViewIfNeeded();let box=await chart.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+170);await page.mouse.wheel(0,-400);await page.mouse.down();await page.mouse.move(box.x+box.width/2+45,box.y+170,{steps:8});await page.mouse.up();await page.waitForTimeout(600);
  const before=await state(),deniedBefore=blockedCounts(),candlesBefore=r.responses.filter(x=>x.http===200&&x.url.includes('/candles?')).length;
  for(let n=1;n<=4;n++){await page.waitForTimeout(8500);const s=await state();assert.equal(s.height,380);console.log(name,'periodic range observation',n*8.5)}
  const after=await state();assert.equal(after.created,before.created);assert.equal(after.removed,before.removed);assert.ok(Math.abs(after.range.from-before.range.from)<0.05&&Math.abs(after.range.to-before.range.to)<0.05);assert.ok(r.responses.filter(x=>x.http===200&&x.url.includes('/candles?')).length>candlesBefore);assert.deepEqual(blockedCounts(),deniedBefore);r.checks.push({check:'actual 30s polling preserves range and chart instance; terminal polling stops',before,after,terminalCountsBefore:deniedBefore,terminalCountsAfter:blockedCounts()});await shot('after-periodic');
  if(name==='mobile'){
   await chart.scrollIntoViewIfNeeded();box=await chart.boundingBox();const cdp=await context.newCDPSession(page);const x=box.x+box.width/2,y=box.y+170;const pinchBefore=await state();
   try{await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x-25,y,id:1},{x:x+25,y,id:2}]});for(let n=1;n<=8;n++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-25-n*4,y,id:1},{x:x+25+n*4,y,id:2}]});await page.waitForTimeout(50)}await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await page.waitForTimeout(500);const pinchAfter=await state();r.pinch={before:pinchBefore,after:pinchAfter,status:Math.abs((pinchAfter.range.to-pinchAfter.range.from)-(pinchBefore.range.to-pinchBefore.range.from))>1?'PASS':'FAIL: input accepted but no zoom change'};}catch(e){r.pinch={status:'UNRUN',tool_error:e.message};}
   await shot('pinch');
  }
  // Switch selects: no retained old candles while the new selection loads; fit new range.
  for(const symbol of ['ETHUSDT','BTCUSDT']){
   await page.getByRole('combobox',{name:'종목',exact:true}).selectOption(symbol);await normal();const s=await state();assert.equal(s.tables,1);assert.equal(s.height,380);assert.ok(s.range.to-s.range.from>190);r.checks.push({check:'new symbol fits fresh data without chart leak',symbol,state:s});
  }
  for(const interval of ['1m','5m','15m','1h','4h','1d']){
   const response=page.waitForResponse(res=>res.url().includes('/candles?')&&res.url().includes(`interval=${interval}`)&&res.status()===200);await page.getByRole('combobox',{name:'봉 간격',exact:true}).selectOption(interval);await response;await normal();const s=await state();assert.equal(s.tables,1);assert.equal(s.height,380);r.checks.push({check:'new interval and single chart',interval,state:s});
  }
  await page.getByRole('combobox',{name:'시장',exact:true}).selectOption('perpetual');await page.getByText('운영 설정으로 비활성화 · 재시도 중단',{exact:true}).first().waitFor();await page.waitForTimeout(1000);assert.equal(await page.locator('.chart canvas').count(),0);await shot('preblocked-perpetual');
  await page.getByRole('combobox',{name:'시장',exact:true}).selectOption('spot');await normal();
  await page.getByRole('button',{name:/Bybit.*BTC/}).click();await page.waitForTimeout(600);assert.equal(await page.locator('.chart canvas').count(),0);await shot('preblocked-bybit');await page.getByRole('button',{name:/Binance.*BTC/}).click();await normal();
  await page.getByRole('combobox',{name:'종목',exact:true}).focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');await normal();r.keyboard=await page.evaluate(()=>({tag:document.activeElement.tagName,label:document.activeElement.labels?.[0]?.innerText,value:document.activeElement.value,focusVisible:document.activeElement.matches(':focus-visible'),statuses:document.querySelectorAll('[role=status]').length,korean:document.body.innerText.includes('공개 시장 관측소')}));assert.equal(r.keyboard.tag,'SELECT');assert.equal(r.keyboard.value,'ETHUSDT');assert.ok(r.keyboard.statuses>=5);
  await page.setViewportSize(name==='desktop'?{width:1000,height:1000}:{width:844,height:390});await page.waitForTimeout(500);let s=await state();assert.equal(s.height,380);assert.equal(s.width,s.scrollWidth);r.checks.push({check:'responsive resize',state:s});await page.setViewportSize({width,height});
  await page.reload();await normal();await page.waitForTimeout(1000);r.checks.push({check:'reload retains configured denial; terminal API reasons',statuses:r.responses.filter(x=>x.http===503).map(x=>x.data.reason)});await shot('final');assert.deepEqual(r.pageerrors,[]);r.final=await state();assert.equal(r.final.height,380);assert.equal(r.final.width,r.final.scrollWidth);r.passed=true;
 }catch(e){r.failure=e.message;r.passed=false;console.error(name,e.message)}
 finally{r.ended_at_utc=new Date().toISOString();fs.writeFileSync(`${out}/live-${name}.json`,JSON.stringify(r,null,2)+'\n');await context.close();}
 return r;
}));await browser.close();console.log(JSON.stringify(results.map(r=>({name:r.name,passed:r.passed,failure:r.failure,pinch:r.pinch,keyboard:r.keyboard,checks:r.checks.length}))));if(results.some(r=>!r.passed))process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});

// Real-browser product regression. Uses live Binance spot only; no price injection.
const {chromium}=require('playwright');
const fs=require('fs');
const assert=require('node:assert/strict');
const crypto=require('crypto');
const mode=process.argv[2]||'green';
const out='docs/qa/phase2a/chart-stability-20261031';fs.mkdirSync(out,{recursive:true});
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium'});
 const results=await Promise.all([['desktop',1440,1000],['mobile',390,844]].map(async([name,width,height])=>{
  const r={mode,name,started_at_utc:new Date().toISOString(),viewport:{width,height},checks:[],samples:[],responses:[],pageerrors:[],screens:[],blocked_denied_routes:0};
  const context=await browser.newContext({viewport:{width,height},hasTouch:name==='mobile'});
  const page=await context.newPage();let fault=false;
  const measure=()=>page.locator('.chart').evaluate(el=>({at_utc:new Date().toISOString(),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height,cssHeight:getComputedStyle(el).height,cssMinHeight:getComputedStyle(el).minHeight,scrollHeight:document.documentElement.scrollHeight,scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth,internalTableLayout:getComputedStyle(el.querySelector('table')).tableLayout,internalCellBorder:getComputedStyle(el.querySelector('td')).borderBottomWidth,internalCellPadding:getComputedStyle(el.querySelector('td')).padding,marketCellPadding:getComputedStyle(document.querySelector('.markets td')).padding,canvas:[...el.querySelectorAll('canvas')].map(c=>({width:c.getBoundingClientRect().width,height:c.getBoundingClientRect().height}))}));
  const stable=async label=>{const samples=[];for(let n=0;n<4;n++){samples.push(await measure());await page.waitForTimeout(1000)}r.samples.push({label,samples});const heights=samples.map(s=>s.height);assert.ok(Math.max(...heights)-Math.min(...heights)<=1,`${label}: height grew ${heights}`);assert.equal(heights[0],380);assert.ok(samples.every(s=>s.scrollWidth===s.viewport));if(mode!=='red'){assert.ok(samples.every(s=>s.internalTableLayout==='auto'&&s.internalCellBorder==='0px'&&s.internalCellPadding==='0px'));assert.ok(samples.every(s=>parseFloat(s.marketCellPadding)>0));}r.checks.push({label,status:'PASS'});};
  const shot=async tag=>{const path=`${out}/${mode}-${name}-${tag}.png`;await page.screenshot({path,fullPage:true});r.screens.push(path)};
  page.on('pageerror',e=>r.pageerrors.push(e.message));
  page.on('response',async res=>{if(res.url().includes('/api/v1/market/')&&res.status()===200)try{r.responses.push({at_utc:new Date().toISOString(),url:res.url(),data:await res.json()})}catch{}});
  await page.route('**/api/v1/market/**',route=>{const u=new URL(route.request().url());if(u.searchParams.get('exchange')!=='binance'||u.searchParams.get('market_type')!=='spot'){r.blocked_denied_routes++;return route.abort('blockedbyclient')}return fault?route.abort('blockedbyclient'):route.continue()});
  try{
   await page.goto('http://127.0.0.1:5173/');await page.getByText('캔들 수신 완료',{exact:true}).waitFor({timeout:25000});
   if(mode==='red'){
    let caught;try{await stable('unmodified fixed viewport')}catch(e){caught=e.message;r.expected_red_failure=e.message}
    assert.ok(caught,'Expected reproduction failed: chart was stable');await shot('growth');
    // Controlled hypothesis experiment only, never normal acceptance evidence.
    await page.addStyleTag({content:'.chart { height:380px; }'});
    await page.waitForTimeout(300);
    await stable('hypothesis: bounded height on same page');r.passed=false;
   }else{
    await stable('initial bounded chart');await shot('normal');
    await page.setViewportSize(name==='desktop'?{width:1000,height:1000}:{width:844,height:390});await stable('resized viewport');r.resized=await measure();await shot('resized');
    await page.setViewportSize({width,height});await stable('restored viewport');
    for(const symbol of ['ETHUSDT','BTCUSDT']){
     await page.locator('select').nth(0).selectOption(symbol);
     await page.getByText('캔들 수신 완료',{exact:true}).waitFor({timeout:25000});
     for(const interval of ['1m','5m','15m','1h','4h','1d']){
      const response=page.waitForResponse(res=>res.url().includes('/candles?')&&res.url().includes(`symbol=${symbol}`)&&res.url().includes(`interval=${interval}`)&&res.status()===200);
      await page.locator('select').nth(2).selectOption(interval);await response;
      assert.equal((await measure()).height,380);r.checks.push({label:`${symbol}/${interval} live candles`,status:'PASS'});
     }
    }
    const chart=page.locator('.chart');await chart.evaluate(el=>el.scrollIntoView({block:'center'}));
    const before=hash(await chart.screenshot());const box=await chart.boundingBox();const x=box.x+box.width/2,y=box.y+box.height/2;
    await page.mouse.move(x,y);await page.mouse.wheel(0,-350);await page.mouse.down();await page.mouse.move(x+55,y,{steps:10});await page.mouse.up();if(name==='mobile')await page.touchscreen.tap(x,y);
    await page.waitForTimeout(500);const after=hash(await chart.screenshot());assert.notEqual(before,after);r.checks.push({label:'zoom/drag/crosshair and mobile touch changed chart pixels',status:'PASS',before,after});await stable('after interactions');await shot('interaction');
    // Repeat the long window on the fixed product, using the real wall clock.
    const successCount=r.responses.length;fault=true;r.fault_started_at_utc=new Date().toISOString();
    for(let n=1;n<=7;n++){await page.waitForTimeout(10000);const s=await measure();r.samples.push({label:`fault ${n*10}s`,samples:[s]});assert.equal(s.height,380);console.log(`${name}: fixed chart stable at ${n*10}s`)}
    assert.equal(r.responses.length,successCount);assert.equal(await page.getByText('지연 · 마지막 수신값',{exact:true}).count(),2);assert.equal(await page.getByText('지연 · 마지막 수신 캔들',{exact:true}).count(),1);r.checks.push({label:'70s fault: no success,2 ticker stale,1 candle stale; height380',status:'PASS'});await shot('70s-stale');
    fault=false;r.fault_ended_at_utc=new Date().toISOString();await page.getByText('캔들 수신 완료',{exact:true}).waitFor({timeout:35000});await page.waitForTimeout(5000);
    assert.equal(await page.getByText('수신 완료',{exact:true}).count(),2);assert.equal(await page.getByText('지연 · 마지막 수신값',{exact:true}).count(),0);await stable('actual response recovery');r.checks.push({label:'actual ticker/candle recovery',status:'PASS'});await shot('recovered');
    assert.deepEqual(r.pageerrors,[]);r.passed=true;
   }
  }catch(e){r.failure=e.message;r.passed=false;console.error(name,e.message)}
  finally{r.ended_at_utc=new Date().toISOString();fs.writeFileSync(`${out}/${mode}-${name}.json`,JSON.stringify(r,null,2)+'\n');await context.close()}
  return r;
 }));
 await browser.close();console.log(JSON.stringify(results.map(r=>({name:r.name,passed:r.passed,red:r.expected_red_failure,failure:r.failure,checks:r.checks}))));
 if(results.some(r=>!r.passed))process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});

import {test as base,expect} from '@playwright/test';
import {assertPageStructure,openOffline} from './helpers';
import {FIXED_TIME} from './routes';

type Audit={apiReads:string[];blocked:string[];pageErrors:string[]};
const test=base.extend<{providerAudit:Audit}>({
 providerAudit:[async({context,page,baseURL},use,testInfo)=>{
  if(!baseURL)throw new Error('A local browser test server is required.');
  const address=new URL(baseURL);
  if(!['127.0.0.1','localhost','[::1]'].includes(address.hostname))throw new Error('Only a local browser test server is allowed.');
  const audit:Audit={apiReads:[],blocked:[],pageErrors:[]};
  const onError=(error:Error)=>audit.pageErrors.push(error.message);
  page.on('pageerror',onError);
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.method()!=='GET'||url.origin!==address.origin){
    audit.blocked.push(`${request.method()} ${url.origin}${url.pathname}`);
    await route.abort('blockedbyclient');return;
   }
   if(url.pathname==='/api'||url.pathname.startsWith('/api/')){
    audit.apiReads.push(url.pathname);
    await route.fulfill({status:503,contentType:'application/json',body:'{}'});return;
   }
   await route.continue();
  });
  await page.clock.setFixedTime(new Date(FIXED_TIME));
  await use(audit);
  await testInfo.attach('provider-network-audit',{body:Buffer.from(JSON.stringify(audit,null,2)),contentType:'application/json'});
  expect(audit.blocked,'Provider rendering must not try writes or external requests.').toEqual([]);
  expect(audit.pageErrors).toEqual([]);
  expect(audit.apiReads).toEqual(expect.arrayContaining(['/api/v1/connections/status','/api/v1/settings/ai','/api/v1/ai/budget']));
  page.off('pageerror',onError);
 },{auto:true}],
});

const selectedAI={provider:'OPENROUTER',registered:true,status:'REGISTERED_UNVERIFIED',model_id:'anthropic/claude-haiku-5.5',verified_at:null,mode:'OFFLINE_TEST',external_calls:false};
const settings={revision:0,monthly_limit:'8.00',mode:'OFFLINE_TEST',llm_enabled:false};
const budget={currency:'USD',monthly_limit:'8.00',used:'0.00',reserved:'0.00',uncertain:'0.00',remaining:'8.00',provider_balance:null};

for(const scenario of [
 {name:'registered unverified',ai:selectedAI,registration:'등록됨 · 권한 미검증'},
 {name:'invalid configuration',ai:{...selectedAI,status:'INVALID_CONFIGURATION',model_id:null},registration:'설정 확인 필요'},
]){
 test(`OpenRouter ${scenario.name} keeps generation off`,async({page,providerAudit},testInfo)=>{
  const responses:Record<string,unknown>={
   '/api/v1/connections/status':{schema_version:1,bybit:{registered:false,status:'NOT_CONFIGURED'},ai:scenario.ai},
   '/api/v1/settings/ai':settings,
   '/api/v1/ai/budget':budget,
  };
  // Page routes run before the context guard. Only these exact same-origin GETs
  // receive controlled data; all remaining API reads are mocked by the guard.
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url());
   const baseURL=testInfo.project.use.baseURL;
   if(request.method()!=='GET'||!baseURL||url.origin!==new URL(baseURL).origin||!Object.hasOwn(responses,url.pathname)){
    await route.fallback();return;
   }
   providerAudit.apiReads.push(url.pathname);
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(responses[url.pathname])});
  });
  await openOffline(page,'view=settings&tab=connections');
  await assertPageStructure(page,'연결 · 예산',false);
  const card=page.locator('.provider').filter({has:page.getByRole('heading',{name:'OpenRouter',exact:true})});
  await expect(card).toContainText(scenario.registration);
  await expect(card).toContainText('호출 꺼짐');
  await expect(card.locator('.badge.ok')).toHaveCount(0);
  if(scenario.ai.model_id)await expect(card).toContainText('anthropic/claude-haiku-5.5');
  else await expect(card).not.toContainText('등록됨');
  await expect(page.getByText('외부 호출은 꺼져 있습니다.',{exact:true})).toBeVisible();
  await expect(page.getByText('유료 PAPER 분석',{exact:true}).locator('..')).toContainText('꺼짐');
  await expect(page.getByText('제공자 잔액',{exact:true}).locator('..')).toContainText('미확인');
  await expect(page.locator('[data-source="FIXTURE"]')).toHaveCount(0);
  await testInfo.attach('provider-connection',{body:await page.screenshot({fullPage:true,animations:'disabled'}),contentType:'image/png'});
 });
}

import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ConnectionsPage} from './ConnectionsPage';
import {validConnection} from './contracts';
import {budgetFixture,connectionFixture,settingsFixture} from './fixtures';

afterEach(()=>{cleanup();vi.unstubAllGlobals()});

const registered={registered:true,status:'REGISTERED_UNVERIFIED',mode:'OFFLINE_TEST',external_calls:false,provider:'OPENROUTER',model_id:'anthropic/claude-haiku-5.5',verified_at:null};
function connection(ai:unknown){return {...connectionFixture,ai}}
function serve(ai:unknown){
 vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(new Response(JSON.stringify(
  url==='/api/v1/connections/status'?connection(ai):url==='/api/v1/settings/ai'?settingsFixture:budgetFixture
 ),{status:200}))));
}
function provider(name:string){return screen.getByRole('heading',{name}).closest<HTMLElement>('.provider')!}

it('shows selected OpenRouter registration as unverified with its model and calls off',async()=>{
 serve(registered);
 render(<ConnectionsPage/>);
 await screen.findByRole('heading',{name:'OpenRouter'});
 expect(within(provider('OpenRouter')).getByText(/등록됨.*미검증/)).toBeInTheDocument();
 expect(within(provider('OpenRouter')).getByText('anthropic/claude-haiku-5.5')).toBeInTheDocument();
 expect(within(provider('OpenRouter')).getByText('호출 꺼짐')).toBeInTheDocument();
 expect(screen.getByText('외부 호출은 꺼져 있습니다.')).toBeInTheDocument();
 expect(screen.getByText('미확인')).toBeInTheDocument();
 expect(provider('OpenRouter').querySelector('.badge.ok')).toBeNull();
});

it('keeps Anthropic registration on its own card without claiming OpenRouter registration',async()=>{
 serve({...registered,provider:'ANTHROPIC',model_id:'claude-haiku-5-5'});
 render(<ConnectionsPage/>);
 await screen.findByRole('heading',{name:'Anthropic'});
 expect(within(provider('Anthropic')).getByText(/등록됨.*미검증/)).toBeInTheDocument();
 expect(within(provider('Anthropic')).getByText('claude-haiku-5-5')).toBeInTheDocument();
 expect(within(provider('OpenRouter')).getByText('제공자 미설정')).toBeInTheDocument();
 expect(provider('OpenRouter')).not.toHaveTextContent('등록됨');
});

it.each([
 ['missing provider',{...connectionFixture.ai,registered:true,status:'REGISTERED_UNVERIFIED'}],
 ['missing credential',{...registered,registered:false,status:'NOT_CONFIGURED',model_id:null}],
 ['invalid configuration',{...registered,status:'INVALID_CONFIGURATION',model_id:null}],
 ['unknown registration status',{...registered,status:'VERIFIED'}],
])('never claims OpenRouter registration for %s',async(_name,ai)=>{
 serve(ai);
 render(<ConnectionsPage/>);
 await screen.findByRole('heading',{name:'OpenRouter'});
 expect(provider('OpenRouter')).not.toHaveTextContent('등록됨');
 expect(provider('OpenRouter')).toHaveTextContent('호출 꺼짐');
});

it('shows an actionable configuration state without echoing server status text',async()=>{
 serve({...registered,status:'INVALID_CONFIGURATION',model_id:null});
 render(<ConnectionsPage/>);
 await screen.findByRole('heading',{name:'OpenRouter'});
 expect(provider('OpenRouter')).toHaveTextContent('설정 확인 필요');
 expect(screen.queryByText('INVALID_CONFIGURATION')).not.toBeInTheDocument();
});

it('retains the unconfigured example cards without fetching live data',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 render(<ConnectionsPage demo/>);
 expect(within(provider('OpenRouter')).getByText('제공자 미설정')).toBeInTheDocument();
 expect(within(provider('NVIDIA')).getByText('제공자 미설정')).toBeInTheDocument();
 expect(screen.queryByRole('heading',{name:'Anthropic'})).not.toBeInTheDocument();
 expect(fetcher).not.toHaveBeenCalled();
});

it.each([
 ['unknown provider',{...registered,provider:'OTHER'}],
 ['null provider',{...registered,provider:null}],
 ['nontext model',{...registered,model_id:123}],
 ['model URL',{...registered,model_id:'https://example.test/model'}],
 ['model query',{...registered,model_id:'anthropic/claude?key=fake'}],
 ['model traversal',{...registered,model_id:'anthropic/../secret'}],
 ['malformed model author',{...registered,model_id:'anthropic.ai/claude-haiku-5.5'}],
 ['unsupported model suffix',{...registered,model_id:'anthropic/claude-haiku-5.5:free'}],
 ['oversized model author',{...registered,model_id:'a'.repeat(65)+'/claude'}],
 ['oversized model slug',{...registered,model_id:'anthropic/'+'a'.repeat(129)}],
 ['model control',{...registered,model_id:'anthropic/claude\n'}],
 ['oversized model',{...registered,model_id:'a/'.padEnd(300,'x')}],
 ['credential in model',{...registered,model_id:'sk-or-v1-fake-test-only'}],
 ['false verification',{...registered,verified_at:'2026-11-02T01:00:00Z'}],
 ['secret extension',{...registered,api_key:'fake-test-only-secret'}],
])('rejects %s before it can be retained or displayed',async(_name,ai)=>{
 expect(validConnection(connection(ai))).toBe(false);
 serve(ai);
 render(<ConnectionsPage/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('연결 상태를 확인하지 못했습니다.');
 expect(screen.queryByRole('heading',{name:'OpenRouter'})).not.toBeInTheDocument();
 expect(screen.queryByText(/fake-test-only/)).not.toBeInTheDocument();
});

it('accepts the legacy fixture and a selected provider without a model or verification field',()=>{
 expect(validConnection(connectionFixture)).toBe(true);
 const ai=Object.fromEntries(Object.entries(registered).filter(([key])=>key!=='verified_at'));
 expect(validConnection(connection({...ai,model_id:null}))).toBe(true);
});

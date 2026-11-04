import {act,cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {DiagnosticsPage} from './DiagnosticsPage';
import {validDiagnostics} from './contracts';
import {diagnosticsFixture} from './fixtures';
import * as reads from './useRead';

const stamp='2026-11-04T10:00:00.000Z';
const native={state:'OBSERVING',mode:'OBSERVATION_ONLY',observed_at:stamp,age_ms:0,
 rest_state:'EXPIRED',rest_remaining_ms:0,trading_enabled:false,financial_authority:false,
 message:'계정 관측 신호를 받고 있습니다. 자동매매는 차단되어 있습니다.'};
function packet(monitor:unknown=native){return {...diagnosticsFixture,components:{
 ...diagnosticsFixture.components,automation:{state:'READY'},native_monitor:monitor}}}
function serve(monitor:unknown=native,overrides:Record<string,unknown>={}){
 vi.spyOn(reads,'useRead').mockImplementation(<T,>()=>({data:packet(monitor) as T,error:false,
  loading:false,lastSuccess:stamp,reload:()=>undefined,...overrides}));
 render(<DiagnosticsPage route={{page:'diagnostics',demo:false,id:''}}/>);
}
beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(new Date(stamp))});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.useRealTimers()});

it('shows a connected observer and expired account read separately while trading stays blocked',()=>{
 serve();
 expect(screen.getByText('관측 신호 수신 중')).toBeInTheDocument();
 expect(screen.getByText('대조 기한 지남 · 관측은 유지')).toBeInTheDocument();
 expect(screen.getByText('차단 · 계정 관측만')).toBeInTheDocument();
});
it.each([['STOPPED','관측 중단'],['CLEANUP_UNCONFIRMED','종료 정리 확인 필요'],
 ['CHECK_REQUIRED','엔진 상태 확인 필요']])('does not call the %s engine healthy',(state,label)=>{
 serve({...native,state,rest_state:'UNAVAILABLE',rest_remaining_ms:null});
 expect(screen.getByText(label)).toBeInTheDocument();
 expect(document.querySelector('.diagnostic-banner')).toHaveClass('warning-box');
 expect(screen.queryByText('관측 신호 수신 중')).not.toBeInTheDocument();
});
it.each([{error:true},{loading:true},{lastSuccess:null}])('does not reuse an observing packet after a failed or pending read',overrides=>{
 serve(native,overrides);
 expect(screen.queryByText('관측 신호 수신 중')).not.toBeInTheDocument();
 expect(screen.getByText('엔진 상태 확인 필요')).toBeInTheDocument();
});
it('expires a displayed heartbeat without treating a stale retained packet as running',()=>{
 serve();
 act(()=>vi.advanceTimersByTime(30001));
 expect(screen.queryByText('관측 신호 수신 중')).not.toBeInTheDocument();
 expect(screen.getByText('엔진 상태 확인 필요')).toBeInTheDocument();
});
it('expires fresh REST evidence sooner than the still-current stream heartbeat',()=>{
 serve({...native,rest_state:'FRESH',rest_remaining_ms:1000});
 expect(screen.getByText('최근 대조 확인')).toBeInTheDocument();
 act(()=>vi.advanceTimersByTime(1001));
 expect(screen.getByText('관측 신호 수신 중')).toBeInTheDocument();
 expect(screen.getByText('대조 기한 지남 · 관측은 유지')).toBeInTheDocument();
});
it('rejects a packet attempting to grant financial authority before rendering it',()=>{
 expect(validDiagnostics(packet({...native,financial_authority:true}))).toBe(false);
 expect(validDiagnostics(packet({...native,trading_enabled:true}))).toBe(false);
 expect(validDiagnostics(packet({...native,run_id:'private-source'}))).toBe(false);
 expect(validDiagnostics(packet(native))).toBe(true);
});
it('preserves the existing fixture layout without claiming a connected server engine',()=>{
 vi.restoreAllMocks();
 render(<DiagnosticsPage route={{page:'diagnostics',demo:true,id:''}}/>);
 expect(screen.queryByText('관측 신호 수신 중')).not.toBeInTheDocument();
 expect(within(screen.getByRole('heading',{name:'구성 요소 상태'}).closest('section')!).getByText('자동 작업')).toBeInTheDocument();
});

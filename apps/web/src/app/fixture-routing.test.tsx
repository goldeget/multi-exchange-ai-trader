import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import App from '../App';
vi.mock('../chart/CandleChart',()=>({default:()=>null}));
const routes=['view=overview','view=market','view=account','view=recommend','view=paper','view=research&tab=run','view=research&tab=create','view=research&tab=history','view=research&tab=backtest','view=research&tab=report','view=settings&tab=connections','view=settings&tab=diagnostics'];
const keys=['paper-pending-command-v1','spot-paper-pending-v2','phase4-uncertain-intent-v1'];
beforeEach(()=>{localStorage.clear();keys.forEach(key=>localStorage.setItem(key,'preserved-operator-request'));vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response('{}',{status:503}))))});
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();window.history.replaceState({},'','/')});
it.each(routes)('renders the actual shared page for %s in isolated fixture mode without requests',async route=>{
 window.history.replaceState({},'','/?'+route+'&demo=1');
 render(<App/>);await act(async()=>{});
 expect(screen.getAllByRole('heading',{level:1})).toHaveLength(1);
 expect(screen.getByText(/디자인 참조 · 고정 예시 데이터/)).toBeInTheDocument();
 expect(fetch).not.toHaveBeenCalled();
 keys.forEach(key=>expect(localStorage.getItem(key)).toBe('preserved-operator-request'));
});
it('reviews a fixture spot order without confirming against any real ledger',async()=>{
 window.history.replaceState({},'','/?view=paper&demo=1');
 render(<App/>);
 fireEvent.click(screen.getByRole('button',{name:'가상 주문 검토'}));
 await screen.findByRole('dialog',{name:'현물 가상 주문 검토'});
 fireEvent.click(screen.getByRole('button',{name:'예시 검토 확인'}));
 await screen.findByText(/고정 예시 검토를 확인했습니다/);
 expect(fetch).not.toHaveBeenCalled();
 keys.forEach(key=>expect(localStorage.getItem(key)).toBe('preserved-operator-request'));
});
it('keeps fixture budget saves local without changing operator settings',async()=>{
 window.history.replaceState({},'','/?view=settings&tab=connections&demo=1');
 render(<App/>);
 fireEvent.click(screen.getByRole('button',{name:'설정 저장'}));
 await screen.findByText(/예시 설정입니다. 서버 예산과 외부 연결은 변경하지 않습니다/);
 expect(fetch).not.toHaveBeenCalled();
 keys.forEach(key=>expect(localStorage.getItem(key)).toBe('preserved-operator-request'));
});

import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import Research from './Research';
vi.mock('../chart/CandleChart',()=>({default:()=>null}));
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();window.history.replaceState({},'','/')});
it('selects a historical fixed snapshot before opening only that selected run',()=>{
 const open=vi.fn(),fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 render(<Research demo polished page="history" onOpenRun={open}/>);
 fireEvent.click(screen.getByRole('button',{name:/저장된 실행 2/}));
 expect(within(screen.getByText('정리한 가상손익').parentElement!).getByRole('definition')).toHaveTextContent('-12.50 USDT');
 expect(screen.getByText('손익 미확인')).toBeVisible();
 expect(open).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'실행 상세 보기'}));
 expect(open).toHaveBeenCalledWith('reference-stopped');expect(fetcher).not.toHaveBeenCalled();
});
it('opens stored report conditions and routes back to configuration',()=>{
 render(<Research demo polished page="report"/>);
 expect(screen.getByText('적용 수수료율')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'검증 상세 보기'}));
 const dialog=screen.getByRole('dialog',{name:'검증 상세'});
 expect(within(dialog).getByText('마지막 미사용 구간')).toBeVisible();
 expect(within(dialog).getAllByText(/KST/).length).toBe(3);
 fireEvent.click(within(dialog).getByRole('button',{name:'닫기'}));
 fireEvent.click(screen.getByRole('button',{name:'조건 다시 설정'}));
 expect(new URL(window.location.href).searchParams.get('tab')).toBe('backtest');
});
it('shows user percentages and Korean-time evaluation inputs',()=>{
 const view=render(<Research demo polished page="create"/>);
 expect(screen.getByRole('textbox',{name:'총손실 한도 %'})).toHaveValue('5');
 expect(screen.getByRole('textbox',{name:'최대 낙폭 한도 %'})).toHaveValue('5');
 view.unmount();render(<Research demo polished page="backtest"/>);
 expect(screen.getByLabelText('시작 (KST) · 포함')).toHaveAttribute('type','datetime-local');
 expect(screen.getByLabelText('종료 (KST) · 제외')).toHaveAttribute('type','datetime-local');
});

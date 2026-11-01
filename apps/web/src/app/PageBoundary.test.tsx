import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import PageBoundary from './PageBoundary';

afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear()});
it('replaces a malformed read-model crash with a visible failure while retaining pending requests',()=>{
 vi.spyOn(console,'error').mockImplementation(()=>{});
 localStorage.setItem('spot-paper-pending-v2','preserved-uncertain-request');
 function Broken():never{throw new TypeError('malformed read model')}
 render(<PageBoundary><Broken/><button>가상 주문 확정</button></PageBoundary>);
 expect(screen.getByRole('alert')).toHaveTextContent('화면 데이터를 확인하지 못했습니다.');
 expect(screen.queryByRole('button',{name:'가상 주문 확정'})).not.toBeInTheDocument();
 expect(localStorage.getItem('spot-paper-pending-v2')).toBe('preserved-uncertain-request');
});

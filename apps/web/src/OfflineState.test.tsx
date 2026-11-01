import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import Research from './research/Research';
import Recommend from './recommend/Recommend';
vi.mock('./chart/CandleChart',()=>({default:()=>null}));
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals()});
const failed=()=>Promise.resolve(new Response('{}',{status:503}));
it.each(['run','history'] as const)('keeps %s records unknown when the list read fails',async page=>{
 vi.stubGlobal('fetch',vi.fn(failed));render(<Research polished page={page} capabilities={null} capabilitiesFailed/>);
 expect(screen.queryByText('저장된 실행이 없습니다.')).not.toBeInTheDocument();
 await screen.findByText('저장된 실행 목록을 확인하지 못했습니다.');
 expect(screen.queryByText('저장된 실행이 없습니다.')).not.toBeInTheDocument();
});
it('reports an empty run list only after successful reads',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response(JSON.stringify({datasets:[],runs:[],evaluations:[]})))));
 render(<Research polished page="history" capabilities={null}/>);
 await screen.findByText('저장된 실행이 없습니다.');
});
it('does not infer V1 evaluation support from a failed capability read',async()=>{
 vi.stubGlobal('fetch',vi.fn(failed));render(<Research polished page="backtest" capabilities={null} capabilitiesFailed/>);
 await screen.findByText('서버 지원 범위를 확인하지 못해 신규 실행을 차단했습니다.');
 expect(screen.queryByText(/서버 V1/)).not.toBeInTheDocument();
 expect(screen.queryByText(/서버가 구성 편집을 지원하지 않습니다/)).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'가상 검증 검토'})).toBeDisabled();
});
it('does not label an unknown creation capability as fixed V1 leverage',async()=>{
 vi.stubGlobal('fetch',vi.fn(failed));render(<Research polished page="create" capabilities={null} capabilitiesFailed/>);
 await screen.findByText('서버 지원 범위를 확인하지 못해 신규 실행을 차단했습니다.');
 expect(screen.queryByText('레버리지 · V1 고정')).not.toBeInTheDocument();
 expect(screen.getByRole('textbox',{name:'레버리지 · 지원 미확인 배'})).toHaveValue('미확인');
});
it('does not describe a failed recommendation lookup as no recommendation',async()=>{
 vi.stubGlobal('fetch',vi.fn(failed));render(<Recommend polished/>);
 await screen.findByText('규칙 추천을 확인하지 못했습니다.');
 expect(screen.queryByText('지금은 검토할 추천이 없습니다.')).not.toBeInTheDocument();
 await waitFor(()=>expect(screen.queryByText('최근 0건')).not.toBeInTheDocument());
});

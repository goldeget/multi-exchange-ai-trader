import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,describe,expect,it} from 'vitest';
import EvaluationCurve from './EvaluationCurve';
import {RESEARCH_REPORT} from './fixture';
import type {Report} from './Research';

afterEach(cleanup);
function reportWith(curve:unknown):Report{return {...RESEARCH_REPORT,test:{...RESEARCH_REPORT.test,initial_equity:'10000.00',final_equity:'9975.50',curve}} as Report}
const points=[{time_ms:Date.parse('2026-10-22T00:00:00Z'),equity:'10000.00'},{time_ms:Date.parse('2026-10-30T00:00:00Z'),equity:'9975.50'}];
describe('EvaluationCurve',()=>{
 it('keeps readable HTML amount and KST time labels outside the SVG',()=>{render(<EvaluationCurve report={reportWith(points)}/>);const plot=screen.getByRole('img',{name:'미사용 구간의 가상자산 변화'});expect(plot.querySelector('path')).not.toBeNull();expect(plot.querySelector('text')).toBeNull();expect(screen.getByText('10,000.00 USDT')).toBeVisible();expect(screen.getByText('9,975.50 USDT')).toBeVisible();expect(screen.getByText('2026. 10. 22. 09:00 KST')).toBeVisible();expect(screen.getByText('2026. 10. 30. 09:00 KST')).toBeVisible();expect(screen.getByLabelText('가상자산 차트 설명')).toHaveClass('evaluation-curve');});
 it.each([[],[{time_ms:0,equity:'NaN'}],[{time_ms:Infinity,equity:'1'},points[1]],[points[1],points[0]],[{time_ms:points[0].time_ms,equity:'1e309'},points[1]]].map(curve=>({curve})))('fails closed for unavailable or invalid points %j',({curve})=>{render(<EvaluationCurve report={reportWith(curve)}/>);expect(screen.getByRole('status')).toHaveTextContent('자산 흐름을 확인할 수 없습니다.');expect(screen.queryByRole('img')).not.toBeInTheDocument();});
 it('retains large Decimal labels and finite plot coordinates without changing the report',()=>{const large='1'+'0'.repeat(100);const report=reportWith(points.map((p,i)=>({...p,equity:i?'1':large})));report.test.initial_equity=large;report.test.final_equity='1';render(<EvaluationCurve report={report}/>);expect(screen.getByText('10'+',000'.repeat(33)+' USDT')).toBeVisible();expect(screen.getByRole('img').innerHTML).not.toMatch(/NaN|Infinity/);expect(report.test.initial_equity).toBe(large);});
});

import {describe,expect,it} from 'vitest';
import {researchAmount} from './ResearchScreen';

describe('stored financial precision',()=>{
 it('groups only the whole amount and preserves every fractional digit',()=>{
  expect(researchAmount('1234567.123456789012345678')).toBe('1,234,567.123456789012345678');
  expect(researchAmount('-0.00001000')).toBe('-0.00001000');
 });
 it('keeps unavailable and malformed amounts distinct from zero',()=>{
  expect(researchAmount(undefined)).toBe('—');
  expect(researchAmount('broken')).toBe('확인 불가');
  expect(researchAmount('0.00')).toBe('0.00');
 });
});

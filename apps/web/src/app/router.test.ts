import {describe,it,expect,vi} from 'vitest';
import {parseLocation,navigate,subscribeRoute,routeUrl} from './router';
describe('12 page URL navigation',()=>{
 it('opens overview by default and keeps legacy research on backtest',()=>{
  expect(parseLocation(new URL('http://localhost/')).page).toBe('overview');
  expect(parseLocation(new URL('http://localhost/?view=research')).page).toBe('backtest');
 });
 it('preserves all distinct pages, selected IDs and explicit demo through URLs',()=>{
  for(const page of ['overview','market','account','recommend','paper','run','create','history','backtest','report','connections','diagnostics'] as const){
   const route={page,demo:true,id:'saved-123'};
   expect(parseLocation(new URL(routeUrl(route),'http://localhost/'))).toEqual(route);
  }
 });
 it('pushes user navigation and responds to browser back/forward events',()=>{
  const callback=vi.fn(),off=subscribeRoute(callback);
  navigate({page:'market',demo:true,id:''});
  expect(callback).toHaveBeenCalledTimes(1);
  window.dispatchEvent(new PopStateEvent('popstate'));
  expect(callback).toHaveBeenCalledTimes(2);
  off();window.dispatchEvent(new PopStateEvent('popstate'));
  expect(callback).toHaveBeenCalledTimes(2);
 });
 it('rejects unsafe identifiers without choosing another stored run',()=>{
  expect(parseLocation(new URL('http://localhost/?view=research&tab=run&run_id=../private')).id).toBe('');
 });
});

export type Page = 'overview'|'market'|'account'|'recommend'|'paper'|'run'|'create'|'history'|'backtest'|'report'|'connections'|'diagnostics';
export type Route={page:Page;demo:boolean;id:string};
export function parseLocation(url:URL):Route {
 const q=url.searchParams,view=q.get('view'); let page:Page='overview';
 if(['overview','market','account','recommend','paper'].includes(view??'')) page=view as Page;
 if(view==='research'){const tab=q.get('tab');page=['run','create','history','backtest','report'].includes(tab??'')?tab as Page:'backtest';}
 if(view==='settings')page=q.get('tab')==='diagnostics'?'diagnostics':'connections';
 const raw=q.get('run_id')??q.get('report_id')??q.get('id')??'';
 return {page,demo:q.get('demo')==='1',id:/^[-a-zA-Z0-9_]{1,128}$/.test(raw)?raw:''};
}
export function routeUrl(route:Route):string {
 const q=new URLSearchParams();
 if(['run','create','history','backtest','report'].includes(route.page)){q.set('view','research');q.set('tab',route.page);}
 else if(['connections','diagnostics'].includes(route.page)){q.set('view','settings');q.set('tab',route.page);}
 else q.set('view',route.page);
 if(route.id)q.set(route.page==='report'?'report_id':route.page==='run'?'run_id':'id',route.id);
 if(route.demo)q.set('demo','1');
 return '?'+q.toString();
}
export function navigate(route:Route,options?:{replace?:boolean}){window.history[options?.replace?'replaceState':'pushState']({},'',routeUrl(route));window.dispatchEvent(new Event('app:navigate'));}
export function subscribeRoute(listener:()=>void){window.addEventListener('popstate',listener);window.addEventListener('app:navigate',listener);return()=>{window.removeEventListener('popstate',listener);window.removeEventListener('app:navigate',listener)}}

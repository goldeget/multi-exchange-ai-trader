import {act,cleanup,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {useRead} from './useRead';

type Value={value:string};
const valid=(v:unknown):v is Value=>!!v&&typeof v==='object'&&'value' in v&&typeof v.value==='string';
const fixture={value:'fixture'};
function Probe({demo=false,enabled=true,refreshMs=5000}:{demo?:boolean;enabled?:boolean;refreshMs?:number}){
 const read=useRead('/overview',valid,demo?fixture:undefined,enabled,refreshMs);
 return <><p>{read.data?.value??'empty'}</p><p>{read.error?'error':read.loading?'loading':'ready'}</p></>;
}
function requests(){
 const pending:Array<{signal:AbortSignal;finish:(value:Response)=>void}>=[];
 const fetcher=vi.fn((_url:string,options:{signal:AbortSignal})=>new Promise<Response>(resolve=>{
  pending.push({signal:options.signal,finish:resolve});
 }));
 vi.stubGlobal('fetch',fetcher);
 return {pending,fetcher};
}
const response=(value:string,status=200)=>new Response(JSON.stringify({value}),{status});
beforeEach(()=>vi.useFakeTimers());
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers()});

it('waits for each read to complete before scheduling the next without overlap',async()=>{
 const {pending,fetcher}=requests();
 const view=render(<Probe/>);
 await act(async()=>vi.advanceTimersByTime(10000));
 expect(fetcher).toHaveBeenCalledOnce();
 await act(async()=>pending[0].finish(response('first')));
 expect(screen.getByText('first')).toBeInTheDocument();
 await act(async()=>vi.advanceTimersByTime(4999));
 expect(fetcher).toHaveBeenCalledOnce();
 await act(async()=>vi.advanceTimersByTime(1));
 expect(fetcher).toHaveBeenCalledTimes(2);
 expect(screen.getByText('loading')).toBeInTheDocument();
 view.unmount();
 expect(pending[1].signal.aborted).toBe(true);
 await act(async()=>{pending[1].finish(response('late'));vi.advanceTimersByTime(30000)});
 expect(fetcher).toHaveBeenCalledTimes(2);
});

it('retains the last data as unavailable after a failed refresh and recovers on a later success',async()=>{
 const {pending}=requests();
 render(<Probe/>);
 await act(async()=>pending[0].finish(response('first')));
 await act(async()=>vi.advanceTimersByTime(5000));
 expect(pending).toHaveLength(2);
 await act(async()=>pending[1].finish(response('failed',503)));
 expect(screen.getByText('first')).toBeInTheDocument();
 expect(screen.getByText('error')).toBeInTheDocument();
 await act(async()=>vi.advanceTimersByTime(5000));
 await act(async()=>pending[2].finish(response('recovered')));
 expect(screen.getByText('recovered')).toBeInTheDocument();
 expect(screen.getByText('ready')).toBeInTheDocument();
});

it('aborts a timed out read, retries after completion delay and ignores late responses',async()=>{
 const {pending,fetcher}=requests();
 render(<Probe/>);
 await act(async()=>vi.advanceTimersByTime(15000));
 expect(pending[0].signal.aborted).toBe(true);
 expect(screen.getByText('error')).toBeInTheDocument();
 expect(fetcher).toHaveBeenCalledOnce();
 await act(async()=>vi.advanceTimersByTime(5000));
 expect(fetcher).toHaveBeenCalledTimes(2);
 await act(async()=>pending[1].finish(response('current')));
 await act(async()=>pending[0].finish(response('obsolete')));
 expect(screen.getByText('current')).toBeInTheDocument();
 expect(screen.queryByText('obsolete')).not.toBeInTheDocument();
});

it.each(['fixture','disabled'] as const)('cancels pending live reads and retries when switching to %s',async(mode)=>{
 const {pending,fetcher}=requests();
 const view=render(<Probe/>);
 view.rerender(<Probe demo={mode==='fixture'} enabled={mode!=='disabled'}/>);
 expect(pending[0].signal.aborted).toBe(true);
 await act(async()=>{pending[0].finish(response('late'));vi.advanceTimersByTime(30000)});
 expect(fetcher).toHaveBeenCalledOnce();
 expect(screen.getByText(mode==='fixture'?'fixture':'empty')).toBeInTheDocument();
 expect(screen.queryByText('late')).not.toBeInTheDocument();
});

it('does not poll callers using the default refresh interval',async()=>{
 const {pending,fetcher}=requests();
 render(<Probe refreshMs={0}/>);
 await act(async()=>pending[0].finish(response('once')));
 await act(async()=>vi.advanceTimersByTime(30000));
 expect(fetcher).toHaveBeenCalledOnce();
 expect(screen.getByText('once')).toBeInTheDocument();
});

it('cancels a scheduled retry when unmounted after a completed read',async()=>{
 const {pending,fetcher}=requests();
 const view=render(<Probe/>);
 await act(async()=>pending[0].finish(response('once')));
 view.unmount();
 await act(async()=>vi.advanceTimersByTime(30000));
 expect(fetcher).toHaveBeenCalledOnce();
});

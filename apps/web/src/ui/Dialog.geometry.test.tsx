import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {useState} from 'react';
import {Dialog} from './components';

let base = {left:100.25,top:109.6};
let observers: Array<{callback:ResizeObserverCallback;observer:ResizeObserver}>;

beforeEach(()=>{
  base={left:100.25,top:109.6};
  observers=[];
  vi.stubGlobal('devicePixelRatio',1);
  // jsdom has no layout engine: emulate only the DOM geometry/observer boundary.
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){
    if(!this.classList.contains('dialog'))return new DOMRect();
    const [x,y]=(this.style.translate||'none').split(' ').map(value=>parseFloat(value)||0);
    return new DOMRect(base.left+x,base.top+(y||0),560,300);
  });
  vi.stubGlobal('ResizeObserver',class {
    constructor(callback:ResizeObserverCallback){observers.push({callback,observer:this as unknown as ResizeObserver})}
    observe(){}
    unobserve(){}
    disconnect(){}
  });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()});

function notifyResize(){for(const {callback,observer} of observers)callback([],observer)}
function mount(){return render(<Dialog title="검토" onClose={()=>{}}><button>확정</button></Dialog>)}

describe('dialog physical pixel geometry',()=>{
  it('aligns the initial fractional centered origin to physical pixels',()=>{
    mount();
    const rect=screen.getByRole('dialog').getBoundingClientRect();
    expect(rect.left).toBeCloseTo(100,8);
    expect(rect.top).toBeCloseTo(110,8);
    expect(screen.getByRole('heading',{name:'검토'})).toHaveFocus();
  });

  it('resnaps size changes without accumulating translation or jitter',()=>{
    mount();
    base={left:131.8,top:87.3};
    act(notifyResize);
    const dialog=screen.getByRole('dialog');
    expect(dialog.getBoundingClientRect().left).toBeCloseTo(132,8);
    expect(dialog.getBoundingClientRect().top).toBeCloseTo(87,8);
    for(let i=0;i<4;i++)act(notifyResize);
    expect(dialog.getBoundingClientRect().left).toBeCloseTo(132,8);
    expect(dialog.getBoundingClientRect().top).toBeCloseTo(87,8);
  });

  it('uses the current DPR on viewport resize instead of rounding CSS pixels',()=>{
    mount();
    vi.stubGlobal('devicePixelRatio',2);
    base={left:100.7,top:109.3};
    fireEvent(window,new Event('resize'));
    const rect=screen.getByRole('dialog').getBoundingClientRect();
    expect(rect.left).toBeCloseTo(100.5,8);
    expect(rect.top).toBeCloseTo(109.5,8);
  });

  it('ignores queued resize callbacks after unmount and removes its viewport listener',()=>{
    const mounted=mount();
    const dialog=screen.getByRole('dialog');
    const measure=vi.spyOn(dialog,'getBoundingClientRect').mockClear();
    mounted.unmount();
    act(notifyResize);
    fireEvent(window,new Event('resize'));
    expect(measure).not.toHaveBeenCalled();
  });
});

function Openable({busy=false}:{busy?:boolean}){
  const [open,setOpen]=useState(false);
  return <><button onClick={()=>setOpen(true)}>검토 열기</button>{open&&<Dialog title="검토" dismissible={!busy} onClose={()=>setOpen(false)}><button>확정</button></Dialog>}</>;
}

describe('dialog keyboard behavior remains intact',()=>{
  it('traps focus, closes with Escape, and returns focus to the trigger',()=>{
    render(<Openable/>);
    const trigger=screen.getByRole('button',{name:'검토 열기'});
    trigger.focus();fireEvent.click(trigger);
    const title=screen.getByRole('heading',{name:'검토'});
    expect(title).toHaveFocus();
    fireEvent.keyDown(title,{key:'Tab',shiftKey:true});
    expect(screen.getByRole('button',{name:'닫기'})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'Tab'});
    expect(screen.getByRole('button',{name:'확정'})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'Escape'});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('retains the busy dismissal guard',()=>{
    render(<Openable busy/>);
    fireEvent.click(screen.getByRole('button',{name:'검토 열기'}));
    expect(screen.getByRole('button',{name:'닫기'})).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('heading',{name:'검토'}),{key:'Escape'});
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

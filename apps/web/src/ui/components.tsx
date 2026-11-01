import {useEffect,useLayoutEffect,useRef,type ReactNode} from 'react';
export function Icon({name='chart',size=22}:{name?:string;size?:number}){const paths:Record<string,ReactNode>={home:<><path d="m3 11 9-8 9 8"/><path d="M5 10v11h5v-7h4v7h5V10"/></>,chart:<><path d="M3 3v18h18M6 15l5-5 4 3 6-8"/></>,wallet:<><rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18m-6 4h6m-3 3h1"/></>,play:<><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></>,bulb:<><path d="M8 15a7 7 0 1 1 8 0l-1 4H9zM9 22h6"/></>,file:<><path d="M5 2h9l5 5v15H5zM14 2v6h5M8 12h8M8 16h8"/></>,settings:<><path d="m9 3 1-2h4l1 2 3 2 3 1v4l-2 2v3l2 2-2 4-3-1-3 1-2 2H9l-1-3-3-1-3 1-2-4 2-2v-3L1 9l2-4 3 1z"/><circle cx="12" cy="12" r="3"/></>,refresh:<><path d="M20 7V2l-4 4a9 9 0 1 0 4 12M20 2h-5"/></>,lock:<><rect x="5" y="10" width="14" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 15v3"/></>,more:<><circle cx="4" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="20" cy="12" r="1"/></>,arrow:<path d="m9 5 7 7-7 7"/>,pause:<><path d="M8 5v14M16 5v14"/></>};return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]??paths.file}</svg>}
export function Badge({children,tone='muted'}:{children:ReactNode;tone?:string}){return <span className={'badge '+tone}>{children}</span>}
export function Panel({title,aside,children,className=''}:{title?:ReactNode;aside?:ReactNode;children:ReactNode;className?:string}){return <section className={'surface '+className}>{title&&<div className="panel-heading"><h2>{title}</h2>{aside}</div>}{children}</section>}
export function Definitions({rows}:{rows:Array<[ReactNode,ReactNode]>}){return <dl className="definitions">{rows.map(([a,b],i)=><div key={i}><dt>{a}</dt><dd className={typeof b==='string'&&/[-+]?\d[\d.,]*\s*(?:USD[T]?|BTC|ETH|%|틱|배|개|회)$/.test(b)?'financial-value':undefined}>{b}</dd></div>)}</dl>}
export function Metrics({items}:{items:Array<[string,string,string?]>}){return <section className="metric-strip surface">{items.map(([a,b,t])=><div key={a}><span>{a}</span><strong className={t??''}>{b}</strong></div>)}</section>}
export function Empty({title='표시할 항목이 없습니다.',detail}:{title?:string;detail?:string}){return <div className="empty"><Icon name="file" size={36}/><strong>{title}</strong>{detail&&<p>{detail}</p>}</div>}
export function Field({label,value,onChange,unit,disabled=false,helper,type='text'}:{label:string;value:string;onChange?:(value:string)=>void;unit?:string;disabled?:boolean;helper?:string;type?:string}){return <label className="field"><span>{label}</span><div className="input-unit"><input type={type} value={value} onChange={e=>onChange?.(e.target.value)} readOnly={!onChange} disabled={disabled} inputMode={unit?'decimal':undefined}/>{unit&&<span>{unit}</span>}</div>{helper&&<small>{helper}</small>}</label>}
export function Select({label,options,value,onChange,disabled=false}:{label:string;options:string[];value?:string;onChange?:(value:string)=>void;disabled?:boolean}){return <label className="field"><span>{label}</span><select value={value} onChange={e=>onChange?.(e.target.value)} disabled={disabled}>{options.map(x=><option key={x}>{x}</option>)}</select></label>}
export function Dialog({title,children,onClose,dismissible=true}:{title:string;children:ReactNode;onClose:()=>void;dismissible?:boolean}){
  const ref=useRef<HTMLDivElement>(null);
  useLayoutEffect(()=>{
    const el=ref.current;
    if(!el)return;
    let active=true;
    const initialTranslate=el.style.translate;
    function snap(){
      if(!active||!el!.isConnected)return;
      // Always measure the centered, untranslated origin: repeated observer
      // notifications must not accumulate a correction or alternate positions.
      el!.style.translate='none';
      const {left,top}=el!.getBoundingClientRect();
      if(!Number.isFinite(left)||!Number.isFinite(top)){
        el!.style.translate=initialTranslate;
        return;
      }
      const currentRatio=window.devicePixelRatio;
      const ratio=Number.isFinite(currentRatio)&&currentRatio>0?currentRatio:1;
      const x=Math.round(left*ratio)/ratio-left;
      const y=Math.round(top*ratio)/ratio-top;
      el!.style.translate=`${x}px ${y}px`;
    }
    snap();
    const observer=typeof ResizeObserver==='function'?new ResizeObserver(snap):null;
    observer?.observe(el);
    window.addEventListener('resize',snap);
    return()=>{
      active=false;
      observer?.disconnect();
      window.removeEventListener('resize',snap);
      el.style.translate=initialTranslate;
    };
  },[]);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;const el=ref.current;el?.querySelector<HTMLElement>('h2')?.focus({preventScroll:true});function key(e:KeyboardEvent){if(e.key==='Escape'&&dismissible)onClose();if(e.key==='Tab'&&el){const items=[...el.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]')];const first=items[0],last=items.at(-1);if(e.shiftKey&&(document.activeElement===first||document.activeElement===el.querySelector('h2'))){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}}document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus()}},[onClose,dismissible]);
  return <div className="dialog-backdrop"><div ref={ref} className="dialog surface" role="dialog" aria-modal="true" aria-label={title}><h2 tabIndex={-1}>{title}</h2>{children}<button className="secondary" disabled={!dismissible} onClick={onClose}>닫기</button></div></div>;
}

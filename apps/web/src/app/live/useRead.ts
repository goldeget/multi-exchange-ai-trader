import {useEffect,useState} from 'react';
export function useRead<T>(url:string,validate:(v:unknown)=>v is T,fixture?:T,enabled=true,refreshMs=0){
 const source=url+(fixture?':fixture':enabled?':live':':off');
 const [state,setState]=useState<{source:string;data:T|null;error:boolean;loading:boolean;at:string|null}>({source,data:fixture??null,error:false,loading:!fixture&&enabled,at:null});
 const [nonce,setNonce]=useState(0);
 // A source change must never briefly expose the previous source's values.
 if(state.source!==source)setState({source,data:fixture??null,error:false,loading:!fixture&&enabled,at:null});
 useEffect(()=>{
  if(fixture||!enabled)return;
  let disposed=false,active:AbortController|undefined,next:number|undefined,deadline:number|undefined;
  async function read(){
   const controller=new AbortController();active=controller;
   if(refreshMs>0)setState(old=>({source,data:old.source===source?old.data:null,error:false,loading:true,at:old.source===source?old.at:null}));
   try{
    const {response,value}=await Promise.race([
     (async()=>{const response=await fetch(url,{signal:controller.signal});const value:unknown=await response.json();return {response,value}})(),
     new Promise<never>((_resolve,reject)=>{deadline=window.setTimeout(()=>{controller.abort();reject(new Error('read_timeout'))},15000)}),
    ]);
    if(disposed||controller.signal.aborted)return;
    if(!response.ok||!validate(value))throw new Error('read_contract');
    setState({source,data:value,error:false,loading:false,at:new Date().toISOString()});
   }catch{
    if(!disposed)setState(old=>({source,data:old.source===source?old.data:null,error:true,loading:false,at:old.source===source?old.at:null}));
   }finally{
    window.clearTimeout(deadline);
    if(!disposed&&refreshMs>0)next=window.setTimeout(()=>{void read()},refreshMs);
   }
  }
  void read();
  return()=>{disposed=true;active?.abort();window.clearTimeout(deadline);window.clearTimeout(next)};
 },[source,url,validate,fixture,nonce,enabled,refreshMs]);
 if(fixture)return {data:fixture,error:false,loading:false,lastSuccess:'2026-11-01T00:10:03Z',reload:()=>undefined};
 if(!enabled)return {data:null,error:false,loading:false,lastSuccess:null,reload:()=>undefined};
 const current=state.source===source?state:null;
 return {data:current?.data??null,error:current?.error??false,loading:current?.loading??true,lastSuccess:current?.at??null,reload:()=>setNonce(n=>n+1)};
}

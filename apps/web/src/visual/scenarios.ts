import type {Candle} from '../api';
export const fixtureClock='2026-11-01T00:10:03Z';
export const fixtureCandles:Candle[]=Array.from({length:96},(_,i)=>{
 const base= i<32?63800+i*96:i<70?66900-(i-32)*78:63950+(i-70)*49;
 const open=base+Math.sin(i*2.1)*145,close=i===95?65210.40:base+Math.sin(i*3.7+1)*160;
 return {timestamp:new Date(Date.parse('2026-10-28T01:00:00Z')+i*3600000).toISOString(),open:open.toFixed(2),close:close.toFixed(2),high:(Math.max(open,close)+110).toFixed(2),low:(Math.min(open,close)-100).toFixed(2),volume:(300+Math.abs(Math.sin(i*2.3))*1200).toFixed(2),is_closed:i<95};
});

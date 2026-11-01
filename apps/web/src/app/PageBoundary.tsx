import {Component,type ReactNode} from 'react';

export default class PageBoundary extends Component<{children:ReactNode},{failed:boolean}> {
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true}}
 render(){
  if(this.state.failed)return <section className="surface" role="alert"><h2>화면 데이터를 확인하지 못했습니다.</h2><p>새로고침 후 저장된 미완료 요청부터 대조하세요. 기존 요청 기록은 유지됩니다.</p><button className="secondary" onClick={()=>window.location.reload()}>화면 새로고침</button></section>;
  return this.props.children;
 }
}

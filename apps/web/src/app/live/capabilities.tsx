import {validCapabilities,type Capabilities} from './contracts';
import {useRead} from './useRead';
export function useCapabilities(enabled=true){const read=useRead('/api/v1/capabilities',validCapabilities,undefined,enabled);return {data:read.error?null:read.data,failed:read.error}}
export function CapabilitiesNotice({capabilities,failed}:{capabilities:Capabilities|null;failed:boolean}){
 if(!capabilities)return <p role={failed?'alert':'status'} className="production-note">{failed?'서버 지원 범위를 확인하지 못해 신규 진입을 차단했습니다.':'서버 지원 범위를 확인하는 중입니다.'}</p>;
 return !capabilities.manual_paper.available?<p role="status" className="production-note">현재 PC에서 가상 거래 엔진을 실행할 수 없습니다. 검증된 Linux 환경이 필요합니다. 저장된 기록은 조회할 수 있습니다.</p>:null;
}

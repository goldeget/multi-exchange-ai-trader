import type {Page} from './router';
export const pages:Record<Page,{title:string;mobile:string;description:string;group:number}>={
 overview:{title:'종합 현황',mobile:'종합 현황',description:'계좌와 가상 실행 상태를 한눈에 확인하세요.',group:0},
 market:{title:'시장 · 차트',mobile:'시장 · 차트',description:'주요 거래소의 시세와 차트를 확인할 수 있습니다.',group:1},
 account:{title:'계좌 · 포지션',mobile:'계좌 · 포지션',description:'연동된 거래소의 잔고, 외부 포지션 및 미체결 주문을 조회합니다.',group:2},
 recommend:{title:'분석 · 추천',mobile:'분석 · 추천',description:'근거와 반대 조건을 확인한 뒤 검토하세요.',group:3},
 paper:{title:'수동 가상매매',mobile:'수동 가상매매',description:'합성 시세로 가상 주문을 입력하고, 체결 결과를 확인할 수 있습니다.',group:4},
 run:{title:'자동실행 모니터',mobile:'자동실행',description:'설정한 전략으로 가상매매를 실행하고, 현재 상태를 확인합니다.',group:4},
 create:{title:'새 가상 실행',mobile:'새 가상 실행',description:'선택한 전략과 조건으로 가상매매 실행을 생성합니다. 생성 후 설정은 고정됩니다.',group:4},
 history:{title:'실행 기록',mobile:'실행 기록',description:'과거에 실행했던 가상매매의 목록과 상세 정보를 확인할 수 있습니다.',group:4},
 backtest:{title:'백테스트 설정',mobile:'백테스트 설정',description:'과거 데이터를 사용해 전략의 성과를 검증합니다.',group:5},
 report:{title:'검증 결과',mobile:'검증 결과',description:'설정한 조건과 실제 적용 결과를 함께 확인하세요.',group:5},
 connections:{title:'연결 · 예산 설정',mobile:'연결 · 예산',description:'거래소와 AI 서비스의 연결 상태 및 사용 한도를 확인합니다.',group:6},
 diagnostics:{title:'진단 · 복구',mobile:'진단 · 복구',description:'기능별 상태를 점검하고, 문제 발생 시 복구 방법을 확인합니다.',group:6}};
export const sidebar=[['overview','종합 현황','home'],['market','시장 · 차트','chart'],['account','계좌 · 포지션','wallet'],['recommend','분석 · 추천','bulb'],['run','매매 · 자동실행','play'],['backtest','전략 · 검증','file'],['connections','설정 · 진단','settings']] as const;

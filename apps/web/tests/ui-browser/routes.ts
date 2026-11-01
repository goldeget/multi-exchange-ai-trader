export const FIXED_TIME = "2026-11-01T00:10:03Z";
export const FIXTURE_RUN_ID = "reference-run";
export const FIXTURE_REPORT_ID = "b".repeat(64);

export const demoRoutes = [
  { key: "01-overview", query: "view=overview", title: "종합 현황" },
  { key: "02-market", query: "view=market", title: "시장 · 차트" },
  { key: "03-account", query: "view=account", title: "계좌 · 포지션" },
  { key: "04-recommend", query: "view=recommend", title: "분석 · 추천" },
  { key: "05-paper", query: "view=paper", title: "수동 가상매매" },
  { key: "06-run", query: `view=research&tab=run&run_id=${FIXTURE_RUN_ID}`, title: "자동실행" },
  { key: "07-create", query: "view=research&tab=create", title: "새 가상 실행" },
  { key: "08-history", query: "view=research&tab=history", title: "실행 기록" },
  { key: "09-backtest", query: "view=research&tab=backtest", title: "백테스트 설정" },
  { key: "10-report", query: `view=research&tab=report&report_id=${FIXTURE_REPORT_ID}`, title: "검증 결과" },
  { key: "11-connections", query: "view=settings&tab=connections", title: "연결 · 예산" },
  { key: "12-diagnostics", query: "view=settings&tab=diagnostics", title: "진단 · 복구" },
] as const;

export const demoUrl = (query: string) => `/?${query}&demo=1`;

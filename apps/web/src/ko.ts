export const intervalOptions = [
  ["1m", "1분"],
  ["5m", "5분"],
  ["15m", "15분"],
  ["1h", "1시간"],
  ["4h", "4시간"],
  ["1d", "1일"],
] as const;

const serverNotes: Record<string, string> = {
  "자동 loop 검증 실패": "자동 진행 확인에 실패했습니다",
  "저장 검증 실패 · 자동 실행 중단": "저장 확인에 실패해서 자동 진행을 멈췄습니다",
  "native/저장 오류 · 포지션을 보존하고 일시정지":
    "저장 오류가 나서 포지션은 그대로 두고 일시정지했습니다",
  "상태 확인 실패 · 자동 실행 중단": "상태 확인에 실패해서 자동 진행을 멈췄습니다",
  "로컬 synthetic PAPER 전용": "가상 거래만 자동으로 진행합니다",
  "명시적 재개": "다시 시작했습니다",
  "loop 상태 조회 중": "자동 진행 상태를 확인하는 중입니다",
};

const jargon =
  /\b(PAPER|DEMO|LIVE|OOS|HALTED|BLOCKED|seccomp|pytest|snapshot|native|synthetic|reconciliation|idempotency|mainnet|testnet|WebSocket|fallback|HMAC|UNIFIED|SHA|scheduler|equity|reduce-only|walk-forward|UNRUN|PHASE|Nautilus|FAIL_CLOSED|RUNNING|PAUSED|API|REST|LONG|SHORT|UTC)\b/i;

export function intervalLabel(value: string): string {
  return intervalOptions.find(([key]) => key === value)?.[1] ?? value;
}

export function instrumentLabel(symbol: string): string {
  if (symbol === "BTC-PAPER") return "BTC 가상";
  if (symbol === "ETH-PAPER") return "ETH 가상";
  if (symbol.endsWith("USDT")) return `${symbol.slice(0, -4)} / USDT`;
  return symbol;
}

export function sideLabel(side: string): string {
  const key = side.toLowerCase();
  if (key === "long" || key === "buy") return "오르면 이익";
  if (key === "short" || key === "sell") return "내리면 이익";
  return "방향 확인";
}

export function roleLabel(role: string): string {
  const table: Record<string, string> = {
    sl: "손절",
    tp: "익절",
    liquidation: "강제 정리(대략)",
    entry: "진입",
    exit: "정리",
    close: "정리",
    open: "진입",
  };
  return table[role] ?? "체결";
}

export function runStatusLabel(status: string): string {
  const table: Record<string, string> = {
    RUNNING: "진행 중",
    PAUSED: "일시정지",
    PAUSED_RECOVERY: "다시 시작을 기다리는 중",
    PAUSED_ERROR: "오류로 일시정지",
    STOPPED: "종료",
    HALTED: "긴급 정지",
    CREATED: "만들어 둠",
  };
  return table[status] ?? "상태 확인 중";
}

export function schedulerLabel(state: string | undefined): string {
  const table: Record<string, string> = {
    READY: "자동 진행 준비됨",
    FAIL_CLOSED: "자동 진행이 멈춤",
    UNAVAILABLE: "자동 진행을 확인하지 못함",
    UNKNOWN: "자동 진행 확인 중",
    PAUSED_ERROR: "오류로 자동 진행이 멈춤",
  };
  return table[state ?? ""] ?? "자동 진행 확인 중";
}

export function reasonLabel(reason: string): string {
  const table: Record<string, string> = {
    loss_limit: "총손실 한도에 걸림",
    drawdown_limit: "낙폭 한도에 걸림",
    exposure_limit: "금액 한도에 걸림",
    consecutive_loss_limit: "연속 손실 한도에 걸림",
    operator_stop: "종료함",
    operator_pause: "일시정지함",
    source_exhausted: "준비한 가상 시세가 끝남",
    process_restart_requires_explicit_resume: "프로그램을 다시 켠 뒤라서 다시 시작이 필요함",
    native_or_storage_failure: "저장 오류로 일시정지",
    created_requires_explicit_start: "만들어 둠. 시작 버튼을 눌러야 함",
    explicit_resume: "다시 시작함",
    event_budget: "횟수 한도에 걸림",
  };
  return table[reason] ?? (/[A-Za-z]/.test(reason) ? "이유 확인 중" : reason);
}

export function screenText(value: string | null | undefined): string {
  if (!value || !value.trim()) return "—";
  const known = serverNotes[value.trim()];
  if (known) return known;
  const swaps: Array<[RegExp, string]> = [
    [/\bBLOCKED\b/g, "연결이 막힘"],
    [/\bPAPER_AUTO_SYNTHETIC\b/g, "가상 자동 진행"],
    [/\bPAPER\b/g, "가상 거래"],
    [/\bDEMO\b/g, "연습"],
    [/\bOOS\b/g, "마지막 확인"],
    [/\bnative\b/gi, "프로그램"],
    [/\bsynthetic\b/gi, "가상"],
    [/\bSHA256\b/gi, ""],
    [/\bUTC\b/g, "세계 표준시"],
    [/\bAPI\b/g, ""],
    [/\bmainnet\b/gi, "실계좌"],
    [/\btestnet\b/gi, "시험용 거래소"],
    [/\bUNIFIED\b/g, ""],
    [/\bHALTED\b/g, "긴급 정지"],
    [/\breduce-only\b/gi, "줄이기만"],
    [/\bhedge\b/gi, "양쪽"],
    [/\bone-way\b/gi, "한 방향"],
    [/\bWebSocket\b/gi, "실시간 연결"],
    [/\bfallback\b/gi, "대신 연결"],
  ];
  let next = value;
  for (const [pattern, replacement] of swaps) next = next.replace(pattern, replacement);
  next = next.replace(/\s{2,}/g, " ").replace(/^[\s.·,]+/, "").trim();
  if (jargon.test(next)) return "자세한 상태는 숨겼습니다.";
  return next;
}

export function environmentSentences(
  code: "mainnet" | "demo" | "testnet" | null,
): string[] {
  const current =
    code === "mainnet"
      ? "실계좌입니다. 연습 계좌가 아닙니다."
      : code === "demo"
        ? "연습 계좌입니다. 실계좌가 아닙니다."
        : code === "testnet"
          ? "시험용 거래소입니다. 실계좌가 아닙니다."
          : "어떤 계좌인지 아직 모릅니다.";
  return [
    current,
    "가상 거래가 아닙니다. 가상 거래 화면의 돈과 다릅니다.",
    "주문은 보내지 않습니다. 보이는 포지션과 주문은 모두 외부에서 연 것입니다.",
  ];
}

export function connectionLabel(code: string): string {
  const table: Record<string, string> = {
    ok: "연결됨",
    BLOCKED: "연결이 막혔습니다",
    error: "연결 안 됨",
    missing_credentials: "비밀키가 없습니다",
    config: "설정이 잘못되었습니다",
  };
  return table[code] ?? "연결 안 됨";
}

export function quoteSource(source: string, venue: string): string {
  const lower = source.toLowerCase();
  if (lower.includes("bybit")) return "바이비트 공개 시세";
  if (lower.includes("binance")) return "바이낸스 공개 시세";
  if (
    lower.includes("합성") ||
    lower.includes("sample") ||
    lower.includes("synthetic") ||
    lower.includes("로컬")
  ) {
    return "예시 숫자";
  }
  if (/[A-Za-z]{3,}/.test(source)) return `${venue} 공개 시세`;
  return source;
}

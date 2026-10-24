import type { Tone } from "@/components/ui/tone";
import type { PaperTradingAccount } from "@/types/paper";
import type { TradingAccount, TradingCapitalBalance } from "@/types/trading";

export type AccountType = "paper" | "live";

export type AccountOption =
  | { accountType: "paper"; key: string; label: string; paper: PaperTradingAccount; live?: never }
  | { accountType: "live"; key: string; label: string; live: TradingAccount; paper?: never };

export type AccountState = "trading" | "stopped" | "exit_only" | "entries_paused" | "disabled";

/** Headline figures for one account, with the same PnL semantics for paper and live. */
export type AccountSummary = {
  accountType: AccountType;
  equityUsd: number;
  feeUsd: number;
  fundingUsd: number;
  grossRealizedPnlUsd: number;
  key: string;
  label: string;
  netRealizedPnlUsd: number;
  openMarginUsd: number;
  openNotionalUsd: number;
  openPositionCount: number;
  returnPct: number | null;
  returnLabel: string;
  state: AccountState;
  stateDetail: string | null;
  stateLabel: string;
  stateTone: Tone;
  totalPnlUsd: number;
  unrealizedPnlUsd: number;
};

export type PositionRow = {
  accountKey: string;
  accountType: AccountType;
  addFillCount: number;
  closeDisabledReason: string | null;
  closeFillCount: number;
  coin: string;
  entryExecutionDelayMs: number | null;
  entryPrice: string | number | null;
  fundingUsd: number | null;
  id: string;
  isExchange: boolean;
  leverage: string | number | null;
  marginMode: "cross" | "isolated" | null;
  marginUsd: number;
  markPrice: string | number | null;
  notionalUsd: number;
  openedAt: string;
  priceUpdatedAt: string | null;
  realizedPnlUsd: number;
  side: "long" | "short";
  size: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
  sourceWallet: string;
  unrealizedPnlPct: number | null;
  unrealizedPnlUsd: number | null;
};

export type TradeFlag = { label: string; tone: Tone };

export type ClosedTradeRow = {
  closeTypeLabel: string;
  closedAt: string;
  coin: string;
  durationMs: number | null;
  entryPrice: string | number | null;
  exitPrice: string | number | null;
  feeUsd: number;
  flags: TradeFlag[];
  fundingUsd: number | null;
  id: string;
  netPnlUsd: number;
  notionalUsd: number | null;
  openedAt: string | null;
  realizedPnlUsd: number;
  side: "long" | "short" | null;
  size: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
  sourceWallet: string;
};

export type ExecutionRow = {
  action: string;
  at: string;
  coin: string;
  detail: string | null;
  feeUsd: number | null;
  id: string;
  notionalDetail: string | null;
  notionalUsd: string | number | null;
  price: string | number | null;
  priceDetail: string | null;
  realizedPnlUsd: number | null;
  result: { label: string; tone: Tone };
  side: string | null;
  sourceHref: string | null;
  sourceLabel: string;
  sourceWallet: string;
};

export type SourceMonitorStatus = "monitored" | "connecting" | "offline" | "waiting";

export type SourceState =
  | "trading"
  | "retained"
  | "entries_paused"
  | "waiting_for_trades"
  | "waiting_for_slot";

export type CopySourceRow = {
  allocationPct: number | null;
  allocationUsd: number;
  canOpenNewPositions: boolean;
  closedTradeCount: number;
  copiedFillCount: number;
  firstMonitoredAt: string | null;
  hasRealtimeSlot: boolean;
  lastActivityAt: string | null;
  monitorStatus: SourceMonitorStatus;
  monitoredSeconds: number;
  openMarginUsd: number;
  openNotionalUsd: number;
  openPaperPositionCount: number;
  openPositionCount: number;
  poolRank: number | null;
  rank: number | null;
  realizedPnlPerHourUsd: number | null;
  /** Paper: net of fees and funding since the account's last reset. Live: gross realized PnL. */
  realizedPnlUsd: number;
  remainingAllocationUsd: number;
  score: string | null;
  skippedFillCount: number;
  sourceLabel: string | null;
  sourceState: SourceState;
  sourceStateDetail: string;
  sourceWallet: string;
  totalPnlUsd: number;
  unrealizedPnlUsd: number;
  usedPct: number | null;
};

export type MarketRow = {
  coin: string;
  longCount: number;
  longNotionalUsd: number;
  marginUsd: number;
  notionalUsd: number;
  positionCount: number;
  shortCount: number;
  shortNotionalUsd: number;
  unrealizedPnlUsd: number;
};

export type AccountNotice = {
  detail: string;
  title: string;
  tone: Tone;
};

export type AccountView = {
  accountKey: string;
  accountType: AccountType;
  capitalBalances: TradingCapitalBalance[];
  cashFlowsVersion: string | null;
  closedTrades: ClosedTradeRow[];
  executions: ExecutionRow[];
  marketRows: MarketRow[];
  notices: AccountNotice[];
  positions: PositionRow[];
  sources: CopySourceRow[];
  summary: AccountSummary;
};

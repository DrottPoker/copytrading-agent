import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import type { AccountPerformancePoint } from "../AccountPerformanceChart";
import type {
  PaperCopyAllocation,
  PaperTradingAccount,
} from "@/types/paper";
import type {
  TradingAccount,
  TradingCapitalBalance,
} from "@/types/trading";

export type Tone = "positive" | "warning" | "danger" | "neutral";

export type LiveAccountNotice = {
  detail: string;
  title: string;
  tone: "danger" | "neutral" | "warning";
};

export type AccountOption =
  | {
      accountType: "paper";
      key: string;
      label: string;
      paper: PaperTradingAccount;
      live?: never;
    }
  | {
      accountType: "live";
      key: string;
      label: string;
      live: TradingAccount;
      paper?: never;
    };

export type AccountMetrics = {
  allocationUsd: number;
  allocationUsedPct: number | null;
  averageClosedPnlUsd: number;
  closedNetPnlUsd: number;
  copiedFillCount: number;
  exposureRatio: number | null;
  feeUsd: number;
  netEquityUsd: number;
  openMarginUsd: number;
  openNotionalUsd: number;
  realizedPnlUsd: number;
  remainingAllocationUsd: number;
  returnPct: number | null;
  skippedFillCount: number;
  unrealizedPnlUsd: number;
  winRate: number | null;
};

export type SourceRow = {
  allocationUsd: number;
  closedNetPnlUsd: number;
  closedTradeCount: number;
  copiedFillCount: number;
  lastActivityAt: string | null;
  openMarginUsd: number;
  openNotionalUsd: number;
  openPositionCount: number;
  poolRank: number | null;
  remainingAllocationUsd: number;
  score: string | null;
  skippedFillCount: number;
  sourceLabel: string | null;
  sourceStatus: PaperCopyAllocation["sourceStatus"] | "history";
  sourceWallet: string;
  totalPnlUsd: number;
  unrealizedPnlUsd: number;
  winRate: number | null;
};

export type AccountPositionRow = {
  accountType: "paper" | "live";
  coin: string;
  detail: string;
  entryDetail: string;
  entryPrice: string | number | null;
  executionDetail: string;
  executionValue: string;
  id: string;
  leverage: string | number | null;
  marginMode: "cross" | "isolated" | null;
  notionalUsd: string | number | null;
  side: "long" | "short";
  sourceHref: string | null;
  sourceLabel: string;
  unrealizedPnlUsd: string | number | null;
};

export type AccountClosedTradeRow = {
  badges: RowPill[];
  closedAt: string;
  coin: string;
  detail: string;
  exitDetail: string;
  exitPrice: string | number | null;
  id: string;
  netPnlUsd: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
};

export type AccountExecutionRow = {
  badges: RowPill[];
  coin: string;
  detail: string;
  id: string;
  notionalDetail?: string;
  notionalUsd: string | number | null;
  price: string | number | null;
  priceDetail?: string;
  realizedPnlUsd: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
};

export type AccountDetailSection = {
  icon: LucideIcon;
  rows: Array<{ label: string; value: string }>;
  title: string;
};

export type MetricTileView = {
  action?: ReactNode;
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: Tone;
  value: string;
};

export type MetricLineView = {
  label: string;
  tone?: Tone;
  value: number;
  valueLabel: string;
};

export type RowPill = {
  label: string;
  tone: Tone;
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

export type AccountView = {
  accountType: "paper" | "live";
  allocations: PaperCopyAllocation[];
  balanceLines: MetricLineView[];
  capitalBalances: TradingCapitalBalance[];
  closedTrades: AccountClosedTradeRow[];
  detailSections: AccountDetailSection[];
  marketRows: MarketRow[];
  metrics: AccountMetrics;
  metricTiles: MetricTileView[];
  positions: AccountPositionRow[];
  recentActivity: AccountExecutionRow[];
  sourceRows: SourceRow[];
  timeline: AccountPerformancePoint[];
};

export type AccountDataTab = "positions" | "trades" | "activity";

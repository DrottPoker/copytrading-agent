import { BarChart3, ShieldAlert } from "lucide-react";

import type { AccountPerformanceAnalytics } from "@/components/AccountPerformanceChart";
import { DashboardPanel } from "@/components/DashboardSurface";
import {
  formatCurrency,
  formatInteger,
  formatPercent,
} from "@/lib/format";
import type { TradingAccountsResponse } from "@/types/trading";

import type { AccountView, LiveAccountNotice, Tone } from "./types";

export function AccountAnalyticsSidebar({
  accountView,
  analytics,
  liveAccountNotice,
  liveTradingEnabled,
  riskLimits,
}: {
  accountView: AccountView;
  analytics: AccountPerformanceAnalytics;
  liveAccountNotice: LiveAccountNotice | null;
  liveTradingEnabled: boolean;
  riskLimits: TradingAccountsResponse["riskLimits"];
}) {
  return (
    <div className="grid content-start gap-3">
      <PerformanceAnalyticsPanel analytics={analytics} />
      <RiskExposurePanel
        accountView={accountView}
        liveAccountNotice={liveAccountNotice}
        liveTradingEnabled={liveTradingEnabled}
        riskLimits={riskLimits}
      />
    </div>
  );
}

function PerformanceAnalyticsPanel({
  analytics,
}: {
  analytics: AccountPerformanceAnalytics;
}) {
  return (
    <DashboardPanel
      bodyClassName="p-3"
      icon={BarChart3}
      meta={`${formatPercent(analytics.winRate)} win rate`}
      title="Performance analytics"
    >
      <div className="grid grid-cols-2 gap-2">
        <AnalyticsStat
          label="Profit factor"
          value={formatMultiple(analytics.profitFactor)}
        />
        <AnalyticsStat
          label="Max drawdown"
          tone={analytics.maxDrawdownUsd > 0 ? "danger" : "neutral"}
          value={formatCurrency(-analytics.maxDrawdownUsd)}
        />
        <AnalyticsStat
          label="Avg win"
          tone="positive"
          value={formatCurrency(analytics.averageWinUsd)}
        />
        <AnalyticsStat
          label="Avg loss"
          tone={analytics.averageLossUsd < 0 ? "danger" : "neutral"}
          value={formatCurrency(analytics.averageLossUsd)}
        />
        <AnalyticsStat
          label="Payoff ratio"
          value={formatMultiple(analytics.payoffRatio)}
        />
        <AnalyticsStat
          label="Expectancy"
          tone={analytics.expectancyUsd >= 0 ? "positive" : "danger"}
          value={formatCurrency(analytics.expectancyUsd)}
        />
        <AnalyticsStat
          label="Best / worst"
          value={`${formatCurrency(analytics.bestTradeUsd)} / ${formatCurrency(
            analytics.worstTradeUsd,
          )}`}
        />
        <AnalyticsStat
          label="Current streak"
          tone={
            analytics.currentStreak.endsWith("W")
              ? "positive"
              : analytics.currentStreak.endsWith("L")
                ? "danger"
                : "neutral"
          }
          value={analytics.currentStreak}
        />
      </div>
      <p className="mt-2 text-[11px] text-muted">
        Longest streaks: {formatInteger(analytics.longestWinStreak)} wins,{" "}
        {formatInteger(analytics.longestLossStreak)} losses, recovery{" "}
        {formatMultiple(analytics.recoveryFactor)}
      </p>
    </DashboardPanel>
  );
}

function RiskExposurePanel({
  accountView,
  liveAccountNotice,
  liveTradingEnabled,
  riskLimits,
}: {
  accountView: AccountView;
  liveAccountNotice: LiveAccountNotice | null;
  liveTradingEnabled: boolean;
  riskLimits: TradingAccountsResponse["riskLimits"];
}) {
  const grossNotional = accountView.marketRows.reduce(
    (total, row) => total + row.notionalUsd,
    0,
  );
  const longNotional = accountView.marketRows.reduce(
    (total, row) => total + row.longNotionalUsd,
    0,
  );
  const shortNotional = accountView.marketRows.reduce(
    (total, row) => total + row.shortNotionalUsd,
    0,
  );
  const topMarket = accountView.marketRows[0];
  const averageLeverage =
    accountView.metrics.openMarginUsd > 0
      ? accountView.metrics.openNotionalUsd / accountView.metrics.openMarginUsd
      : null;
  const longShare = grossNotional > 0 ? longNotional / grossNotional : 0.5;

  return (
    <DashboardPanel
      bodyClassName="p-3"
      icon={ShieldAlert}
      meta={
        accountView.accountType === "live"
          ? liveTradingEnabled
            ? "Live guardrails enabled"
            : "Live execution disabled"
          : "Current paper exposure"
      }
      title="Risk & exposure"
    >
      {liveAccountNotice ? <LiveAccountHealthNotice notice={liveAccountNotice} /> : null}
      <div className="grid grid-cols-2 gap-2">
        <AnalyticsStat
          label="Gross notional"
          value={formatCurrency(accountView.metrics.openNotionalUsd)}
        />
        <AnalyticsStat
          label="Open margin"
          value={formatCurrency(accountView.metrics.openMarginUsd)}
        />
        <AnalyticsStat
          label="Equity exposure"
          tone={(accountView.metrics.exposureRatio ?? 0) > 1 ? "warning" : "neutral"}
          value={formatPercent(accountView.metrics.exposureRatio)}
        />
        <AnalyticsStat
          label="Avg leverage"
          value={formatMultiple(averageLeverage)}
        />
        <AnalyticsStat
          label="Top market"
          value={
            topMarket
              ? `${topMarket.coin} ${formatPercent(
                  grossNotional > 0 ? topMarket.notionalUsd / grossNotional : 0,
                )}`
              : "-"
          }
        />
        <AnalyticsStat
          label="Open PnL"
          tone={accountView.metrics.unrealizedPnlUsd >= 0 ? "positive" : "danger"}
          value={formatCurrency(accountView.metrics.unrealizedPnlUsd)}
        />
      </div>

      <div className="mt-3">
        <div className="flex items-center justify-between text-[11px] font-medium text-muted">
          <span>Long {formatCurrency(longNotional)}</span>
          <span>Short {formatCurrency(shortNotional)}</span>
        </div>
        <div className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-line">
          <div
            className="bg-positive"
            style={{ width: `${clamp(longShare, 0, 1) * 100}%` }}
          />
          <div
            className="bg-danger"
            style={{ width: `${clamp(1 - longShare, 0, 1) * 100}%` }}
          />
        </div>
      </div>

      {accountView.accountType === "live" ? (
        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-3 text-[11px]">
          <RiskLimit
            label="Weekly loss"
            value={formatPercent(riskLimits.maxWeeklyLossPct)}
          />
          <RiskLimit
            label="Order rate"
            value={`${formatInteger(riskLimits.maxOrdersPerMinute)}/min`}
          />
          <RiskLimit
            label="Reconcile max age"
            value={`${formatInteger(riskLimits.reconciliationMaxSnapshotAgeSeconds)}s`}
          />
          <RiskLimit
            label="Entry intent TTL"
            value={`${formatInteger(riskLimits.entryIntentTtlSeconds)}s`}
          />
          <RiskLimit
            label="Stopped exits"
            value={riskLimits.reduceOnlyWhenStopped ? "allowed" : "blocked"}
          />
          <RiskLimit
            label="Environment"
            value={liveTradingEnabled ? "enabled" : "disabled"}
          />
        </div>
      ) : null}
    </DashboardPanel>
  );
}

function AnalyticsStat({
  label,
  tone = "neutral",
  value,
}: {
  label: string;
  tone?: Tone;
  value: string;
}) {
  const valueClass =
    tone === "positive"
      ? "text-positive"
      : tone === "danger"
        ? "text-danger"
        : tone === "warning"
          ? "text-warning"
          : "text-ink";
  return (
    <div className="rounded-lg border border-line bg-subtle px-2.5 py-2">
      <p className="truncate text-[10px] font-semibold uppercase tracking-[0.05em] text-muted">
        {label}
      </p>
      <p className={`mt-1 truncate font-mono text-xs font-semibold ${valueClass}`}>
        {value}
      </p>
    </div>
  );
}

function RiskLimit({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-2">
      <span className="truncate text-muted">{label}</span>
      <span className="shrink-0 font-mono font-semibold text-ink">{value}</span>
    </div>
  );
}

function LiveAccountHealthNotice({ notice }: { notice: LiveAccountNotice }) {
  const classes =
    notice.tone === "danger"
      ? "border-danger/25 bg-danger-soft text-danger"
      : notice.tone === "warning"
        ? "border-warning/25 bg-warning-soft text-warning"
        : "border-line bg-subtle text-secondary";

  return (
    <div className={`mb-3 flex items-start gap-2 rounded-md border px-2.5 py-2 ${classes}`}>
      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs font-semibold">{notice.title}</p>
        <p className="mt-0.5 break-words text-[11px] leading-4 opacity-90">
          {notice.detail}
        </p>
      </div>
    </div>
  );
}

function formatMultiple(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (!Number.isFinite(value)) {
    return "Infinity";
  }
  return `${value.toLocaleString("sv-SE", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  })}x`;
}

function clamp(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

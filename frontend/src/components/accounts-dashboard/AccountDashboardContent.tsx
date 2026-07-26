"use client";

import { LineChart, RotateCw } from "lucide-react";
import { useMemo } from "react";

import {
  AccountPerformanceChart,
  calculateAccountPerformance,
} from "@/components/AccountPerformanceChart";
import { DashboardMetric, DashboardPanel } from "@/components/DashboardSurface";
import {
  formatCurrency,
  formatInteger,
} from "@/lib/format";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type { TradingAccountsResponse } from "@/types/trading";

import { AccountActivityPanel } from "./AccountActivityPanel";
import { AccountAnalyticsSidebar } from "./AccountAnalyticsSidebar";
import { AccountDiagnosticsSection } from "./AccountDiagnosticsSection";
import { AccountPortfolioSection } from "./AccountPortfolioSection";
import type { AccountView, LiveAccountNotice } from "./types";

export function AccountDashboardContent({
  accountView,
  isReconciling,
  lastRefreshAt,
  liveAccountNotice,
  liveTradingEnabled,
  marketDataStatus,
  onReconcile,
  riskLimits,
}: {
  accountView: AccountView;
  isReconciling: boolean;
  lastRefreshAt: Date | null;
  liveAccountNotice: LiveAccountNotice | null;
  liveTradingEnabled: boolean;
  marketDataStatus: PaperTradingSummaryResponse["marketDataStatus"];
  onReconcile: (() => Promise<void>) | null;
  riskLimits: TradingAccountsResponse["riskLimits"];
}) {
  const performance = useMemo(
    () => calculateAccountPerformance(accountView.timeline),
    [accountView.timeline],
  );
  const metricTiles = onReconcile
    ? accountView.metricTiles.map((tile) =>
        tile.label === "Reconciled"
          ? {
              ...tile,
              action: (
                <button
                  type="button"
                  onClick={() => void onReconcile()}
                  disabled={isReconciling}
                  className="ui-icon-button h-7 w-7 disabled:cursor-not-allowed disabled:opacity-60"
                  title="Reconcile live account"
                  aria-label="Reconcile live account"
                >
                  <RotateCw
                    className={`h-3.5 w-3.5 ${isReconciling ? "animate-spin" : ""}`}
                    aria-hidden="true"
                  />
                </button>
              ),
            }
          : tile,
      )
    : accountView.metricTiles;

  return (
    <>
      <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-8">
        {metricTiles.map((tile) => (
          <DashboardMetric
            key={tile.label}
            action={tile.action}
            compact
            detail={tile.detail}
            icon={tile.icon}
            label={tile.label}
            tone={tile.tone}
            value={tile.value}
          />
        ))}
      </section>

      <section className="grid gap-3 2xl:grid-cols-[minmax(0,1.65fr)_minmax(340px,0.75fr)]">
        <DashboardPanel
          bodyClassName="p-3"
          icon={LineChart}
          meta={`${formatInteger(performance.tradeCount)} loaded closed trades`}
          title="Performance curve"
        >
          <AccountPerformanceChart points={accountView.timeline} />
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <SmallMetric
              label="Closed net"
              value={formatCurrency(accountView.metrics.closedNetPnlUsd)}
            />
            <SmallMetric
              label="Gross profit"
              value={formatCurrency(performance.grossProfitUsd)}
            />
            <SmallMetric
              label="Gross loss"
              value={formatCurrency(-performance.grossLossUsd)}
            />
            <SmallMetric
              label="Recorded fees"
              value={formatCurrency(accountView.metrics.feeUsd)}
            />
          </div>
        </DashboardPanel>

        <AccountAnalyticsSidebar
          accountView={accountView}
          analytics={performance}
          liveAccountNotice={liveAccountNotice}
          liveTradingEnabled={liveTradingEnabled}
          riskLimits={riskLimits}
        />
      </section>

      <AccountPortfolioSection
        marketDataStatus={marketDataStatus}
        marketRows={accountView.marketRows}
        sourceRows={accountView.sourceRows}
      />

      <AccountActivityPanel
        closedTrades={accountView.closedTrades}
        positions={accountView.positions}
        recentActivity={accountView.recentActivity}
      />

      <AccountDiagnosticsSection
        accountView={accountView}
        lastRefreshAt={lastRefreshAt}
      />
    </>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-subtle px-2.5 py-2">
      <p className="truncate text-[10px] font-medium uppercase text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-xs font-semibold text-ink">{value}</p>
    </div>
  );
}

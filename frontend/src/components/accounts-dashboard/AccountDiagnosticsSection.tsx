import { Activity, Layers } from "lucide-react";

import { DashboardPanel } from "@/components/DashboardSurface";
import { StatusPill } from "@/components/StatusPill";
import { formatCurrency, formatInteger } from "@/lib/format";
import type { TradingCapitalBalance } from "@/types/trading";

import type { AccountView, MetricLineView, Tone } from "./types";
import { AccountTransactionsPanel } from "./AccountTransactionsPanel";

export function AccountDiagnosticsSection({
  accountView,
  isReconciling,
  lastRefreshAt,
  onReconcile,
}: {
  accountView: AccountView;
  isReconciling: boolean;
  lastRefreshAt: Date | null;
  onReconcile: (() => Promise<void>) | null;
}) {
  return (
    <>
      <section
        className={
          accountView.accountType === "live"
            ? "grid gap-3 xl:grid-cols-2 2xl:grid-cols-[0.8fr_1fr_1.2fr]"
            : "grid gap-3 xl:grid-cols-[0.8fr_1.2fr]"
        }
      >
        <DashboardPanel
          bodyClassName="p-3"
          icon={Activity}
          meta={`Refreshed ${lastRefreshAt?.toLocaleTimeString("sv-SE") ?? "-"}`}
          title="Balance diagnostics"
        >
          <BalanceBreakdown rows={accountView.balanceLines} />
        </DashboardPanel>

        {accountView.accountType === "live" ? (
          <AccountTransactionsPanel
            accountKey={accountView.accountKey}
            cashFlowsVersion={accountView.cashFlowsVersion}
            isReconciling={isReconciling}
            onReconcile={onReconcile}
          />
        ) : null}

        <DashboardPanel
          bodyClassName="p-3"
          className={
            accountView.accountType === "live"
              ? "xl:col-span-2 2xl:col-span-1"
              : ""
          }
          icon={Layers}
          title="Account details"
        >
          {accountView.detailSections.length > 0 ? (
            <div className="grid gap-4 lg:grid-cols-2">
              {accountView.detailSections.map((section) => (
                <div key={section.title}>
                  <SectionLabel label={section.title} />
                  <div className="grid grid-cols-2 gap-2">
                    {section.rows.map((row) => (
                      <SmallMetric key={row.label} label={row.label} value={row.value} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No account details are available." />
          )}
        </DashboardPanel>
      </section>

      {accountView.capitalBalances.length > 0 ? (
        <DashboardPanel
          bodyClassName="p-3"
          icon={Layers}
          meta={`${formatInteger(accountView.capitalBalances.length)} capital scopes`}
          title="Capital balances"
        >
          <CapitalBalanceRows balances={accountView.capitalBalances} />
        </DashboardPanel>
      ) : null}
    </>
  );
}

function CapitalBalanceRows({ balances }: { balances: TradingCapitalBalance[] }) {
  return (
    <div className="grid gap-2">
      {balances.map((balance) => (
        <div
          key={balance.key}
          className="grid gap-2 rounded-md border border-line bg-subtle px-3 py-2 sm:grid-cols-[minmax(180px,1fr)_150px_150px_110px] sm:items-center"
        >
          <div className="min-w-0">
            <p className="break-words text-sm font-semibold text-ink">{balance.label}</p>
            <p className="break-words font-mono text-xs text-muted">{balance.key}</p>
            {balance.stale ? (
              <p className="mt-1 text-xs font-medium text-warning">
                Stale snapshot{balance.error ? `: ${balance.error}` : ""}
              </p>
            ) : null}
          </div>
          <SmallMetric label="Equity" value={formatCurrency(balance.equityUsd)} />
          <SmallMetric label="Available" value={formatCurrency(balance.availableUsd)} />
          <StatusPill
            label={
              balance.stale
                ? "stale"
                : balance.tradable
                  ? "tradable"
                  : "not tradable"
            }
            tone={balance.stale ? "warning" : balance.tradable ? "positive" : "neutral"}
          />
        </div>
      ))}
    </div>
  );
}

function BalanceBreakdown({ rows }: { rows: MetricLineView[] }) {
  return (
    <div className="grid gap-2.5">
      {rows.map((row) => (
        <MetricLine
          key={row.label}
          label={row.label}
          tone={row.tone}
          value={row.value}
          valueLabel={row.valueLabel}
        />
      ))}
    </div>
  );
}

function MetricLine({
  label,
  tone = "neutral",
  value,
  valueLabel,
}: {
  label: string;
  tone?: Tone;
  value: number;
  valueLabel: string;
}) {
  return (
    <div className="grid grid-cols-[120px_1fr_100px] items-center gap-2">
      <p className="truncate text-xs font-medium text-secondary">{label}</p>
      <Bar value={value} tone={tone} />
      <p className="truncate text-right font-mono text-xs font-semibold text-ink">
        {valueLabel}
      </p>
    </div>
  );
}

function Bar({ tone = "neutral", value }: { tone?: Tone; value: number }) {
  const color =
    tone === "positive"
      ? "bg-positive"
      : tone === "danger"
        ? "bg-danger"
        : tone === "warning"
          ? "bg-warning"
          : "bg-muted";
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-line">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${clamp(value, 0, 1) * 100}%` }}
      />
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-subtle px-2.5 py-2">
      <p className="truncate text-[10px] font-medium uppercase text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-xs font-semibold text-ink" title={value}>
        {value}
      </p>
    </div>
  );
}

function SectionLabel({ label }: { label: string }) {
  return (
    <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.05em] text-muted">
      {label}
    </p>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-muted">{text}</div>;
}

function clamp(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

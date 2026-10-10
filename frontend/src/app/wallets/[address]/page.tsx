import { ArrowLeft, ExternalLink } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Address, RefreshControl, TimeAgo } from "@/components/ui/client";
import {
  Badge,
  EmptyState,
  KeyValues,
  Meter,
  Panel,
  Pnl,
  PnlPercent,
  scoreTone,
  SectionLabel,
  Side,
  Stat,
  StatStrip,
  StatusDot,
  Usd,
} from "@/components/ui/primitives";
import { toneText } from "@/components/ui/tone";
import { ScoreDetailsDialog } from "@/components/wallets/ScoreDetailsDialog";
import { WalletDetailActions, WalletHistory } from "@/components/wallets/WalletDetailClient";
import {
  getPaperTradingSummary,
  getWallet,
  getWalletCopyTrades,
  getWalletFills,
  getWalletScoreDetail,
  getWalletSourceTrades,
  getWalletStats,
} from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  decimal,
  formatDuration,
  formatInteger,
  formatLeverage,
  formatMultiple,
  formatPercent,
  formatPrice,
  formatScore,
  formatSize,
  formatUsd,
  humanize,
  shortAddress,
} from "@/lib/format";
import { SCORE_COMPONENTS, walletDisplayName, walletStatus } from "@/lib/wallets";
import type {
  SourceTradeWindowStats,
  WalletCurrentStateStats,
  WalletScore,
  WalletScoreDetail,
  WalletStats,
} from "@/types/wallet";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ address: string }> }): Promise<Metadata> {
  const { address } = await params;
  return { title: shortAddress(address) };
}

export default async function WalletDetailPage({ params }: { params: Promise<{ address: string }> }) {
  const { address } = await params;
  const wallet = await getWallet(address);
  if (!wallet) {
    notFound();
  }
  const [stats, fills, sourceTrades, copyTrades, scoreDetail, summary] = await Promise.all([
    getWalletStats(wallet.address),
    getWalletFills(wallet.address),
    getWalletSourceTrades(wallet.address),
    getWalletCopyTrades(wallet.address),
    getWalletScoreDetail(wallet.address),
    getPaperTradingSummary({ closedTradeLimit: 1, includeMarketPrices: false, recentFillLimit: 1 }),
  ]);
  const followedLive = new Set(
    [...summary.realtimeMonitoring.desiredWallets, ...summary.realtimeMonitoring.monitoredWallets].map((item) =>
      item.toLowerCase(),
    ),
  );
  const status = walletStatus(wallet, followedLive);
  const score = wallet.score;
  const state = stats?.currentState ?? null;
  const windowDays = scoreDetail?.windowDays ?? 60;

  return (
    <>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Link href="/wallets" className="inline-flex items-center gap-1 text-xs text-fg-3 hover:text-fg">
            <ArrowLeft className="h-3 w-3" aria-hidden="true" /> Wallets
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-[-0.01em]">{walletDisplayName(wallet)}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-3">
            <Address address={wallet.address} head={10} tail={8} />
            <a
              href={`https://app.hyperliquid.xyz/explorer/address/${wallet.address}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 hover:text-fg"
            >
              Hyperliquid explorer <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
            <StatusDot tone={status.tone} label={<span className="text-fg-2">{status.label}</span>} title={status.detail} />
            <span>pool rank {wallet.poolRank ? `#${wallet.poolRank}` : "none"}</span>
            <span>
              last trade <TimeAgo value={wallet.lastSeenFillAt} fallback="never" />
            </span>
            <span>
              polled <TimeAgo value={wallet.lastPolledAt} fallback="never" />
            </span>
          </div>
          {wallet.notes ? (
            <p className="mt-1 line-clamp-2 max-w-3xl text-xs text-fg-3" title={wallet.notes}>
              {wallet.notes}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-start gap-2">
          <RefreshControl updatedAt={new Date().toISOString()} />
          <WalletDetailActions wallet={wallet} />
        </div>
      </div>

      <StatStrip className="lg:grid-cols-6">
        <Stat
          label="Score"
          tone={scoreTone(score?.score)}
          value={score ? formatScore(score.score) : "Unscored"}
          sub={score ? <>scored <TimeAgo value={score.updatedAt} /></> : "no score yet"}
        />
        <Stat
          label="30D return"
          hint="Time-weighted return on the wallet's own equity over 30 days, from Hyperliquid portfolio history"
          value={<PnlPercent value={score?.equityReturnPct} />}
          sub={`max drawdown ${formatPercent(score?.maxDrawdownPct)}`}
        />
        <Stat
          label="Realized PnL"
          hint={`The wallet's own realized PnL from closed trades in the ${windowDays} day scoring window`}
          value={score ? <Pnl value={score.copyablePnlUsd} /> : "-"}
          sub={`${windowDays} day window`}
        />
        <Stat
          label="Win rate"
          value={formatPercent(score?.winRate)}
          sub={`profit factor ${formatMultiple(score?.profitFactor)}`}
        />
        <Stat
          label="Closed trades"
          value={formatInteger(score?.tradeCount ?? sourceTrades.summary.closedTradeCount)}
          sub={`${formatInteger(sourceTrades.summary.openTradeCount)} open now`}
        />
        <Stat
          label="Account value"
          value={state ? formatUsd(state.accountValueUsd) : "-"}
          sub={state ? <>open PnL <Pnl value={state.totalUnrealizedPnlUsd} /></> : "no snapshot"}
        />
      </StatStrip>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ScorePanel score={score} scoreDetail={scoreDetail} windowDays={windowDays} />
        <CurrentStatePanel state={state} />
      </div>

      <WindowsPanel windows={sourceTrades.windows} />

      {stats ? <FillStatsPanel stats={stats} /> : null}

      <WalletHistory
        coins={stats?.topCoins ?? []}
        copyTradeTotal={copyTrades.total}
        copyTrades={copyTrades.items}
        fillTotal={fills.total}
        fills={fills.items}
        sourceTradeDays={sourceTrades.days}
        sourceTradeSummary={sourceTrades.summary}
        sourceTrades={sourceTrades.items}
        spotBalances={state?.spotBalances ?? []}
      />
    </>
  );
}

function ScorePanel({
  score,
  scoreDetail,
  windowDays,
}: {
  score: WalletScore | null;
  scoreDetail: WalletScoreDetail | null;
  windowDays: number;
}) {
  if (!score) {
    return (
      <Panel title="Score">
        <EmptyState title="Not scored yet">Import fills and run wallet scoring to rank this wallet.</EmptyState>
      </Panel>
    );
  }
  const penalty = decimal(scoreDetail?.penaltyScore ?? score.penaltyScore);
  const activePenalties = (scoreDetail?.penaltyItems ?? []).filter((item) => item.active);
  const caps = [
    scoreDetail?.sampleCap
      ? `Capped at ${formatScore(scoreDetail.sampleCap)}: fewer than ${formatInteger(scoreDetail.minTrades)} closed trades in ${windowDays} days.`
      : null,
    scoreDetail?.liveRiskScoreCap
      ? `Capped at ${formatScore(scoreDetail.liveRiskScoreCap)} while the wallet is in a deep drawdown.`
      : null,
  ].filter(Boolean);

  return (
    <Panel
      title="Score"
      meta={`${windowDays} day window, ${formatInteger(score.tradeCount)} closed trades`}
      actions={<ScoreDetailsDialog score={score} scoreDetail={scoreDetail} />}
    >
      <div className="flex items-baseline gap-3 border-b border-line px-3.5 py-3">
        <span className={cx("text-2xl font-semibold tabular-nums", toneText[scoreTone(score.score)])}>
          {formatScore(score.score)}
        </span>
        <span className="text-sm text-fg-2">{scoreVerdict(decimal(score.score))}</span>
      </div>
      <ul className="divide-y divide-line">
        {SCORE_COMPONENTS.map((component) => {
          const value = decimal(component.value(score));
          return (
            <li key={component.key} className="grid grid-cols-[minmax(0,1fr)_120px_44px] items-center gap-3 px-3.5 py-2">
              <div className="min-w-0">
                <p className="text-sm font-medium">{component.label}</p>
                <p className="truncate text-xs text-fg-3" title={component.description}>
                  {component.description}
                </p>
              </div>
              <Meter value={value / 100} tone={scoreTone(value)} />
              <span className={cx("text-right text-sm font-medium tabular-nums", toneText[scoreTone(value)])}>
                {formatScore(value)}
              </span>
            </li>
          );
        })}
        <li className="px-3.5 py-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium">Penalties</p>
            <span className={cx("text-sm font-medium tabular-nums", penalty > 0 ? "text-negative" : "text-fg-3")}>
              {penalty > 0 ? `-${formatScore(penalty)}` : "none"}
            </span>
          </div>
          {activePenalties.length > 0 ? (
            <ul className="mt-1 grid gap-1">
              {activePenalties.map((item) => (
                <li key={item.key} className="flex items-start justify-between gap-3 text-xs">
                  <span className="text-fg-2">
                    {item.label}
                    <span className="block text-fg-3">{item.detail}</span>
                  </span>
                  <span className="shrink-0 tabular-nums text-negative">-{formatScore(item.value)}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </li>
        {caps.length > 0 ? (
          <li className="px-3.5 py-2 text-xs text-warning">
            {caps.map((cap) => (
              <p key={cap}>{cap}</p>
            ))}
          </li>
        ) : null}
      </ul>
      <div className="border-t border-line px-3.5 py-2.5">
        <SectionLabel>Risk signals</SectionLabel>
        <KeyValues
          items={[
            {
              hint: "Largest fall from a peak in realized PnL over the window",
              label: "Realized drawdown",
              value: formatPercent(scoreDetail?.realizedDrawdownPct ?? score.realizedDrawdownPct),
            },
            {
              label: "Current drawdown",
              value: `${formatPercent(scoreDetail?.currentDrawdownPct ?? score.currentDrawdownPct)}${drawdownStatus(scoreDetail?.currentDrawdownStatus ?? score.currentDrawdownStatus)}`,
            },
            {
              hint: "Open losses and margin use relative to the account",
              label: "Open position stress",
              value: formatPercent(scoreDetail?.openPositionStressPct ?? score.openPositionStressPct),
            },
            {
              hint: "Share of closed trades that still reach the $10 minimum order at our copy size",
              label: "Trades copyable at our size",
              value: formatPercent(scoreDetail?.copySizeRatio ?? score.copySizeRatio),
            },
            {
              hint: "Share of adds made while the position was losing",
              label: "Adds into losers",
              value: formatPercent(scoreDetail?.underwaterAddRatio ?? score.underwaterAddRatio),
            },
            {
              label: "Liquidations",
              tone: (scoreDetail?.liquidationEventCount ?? 0) > 0 ? "negative" : "neutral",
              value: formatInteger(scoreDetail?.liquidationEventCount ?? scoreDetail?.liquidationCount ?? 0),
            },
          ]}
        />
      </div>
    </Panel>
  );
}

function scoreVerdict(score: number) {
  if (score >= 70) {
    return "Strong. Ranks among the wallets worth copying.";
  }
  if (score >= 45) {
    return "Mixed. Some components hold it back.";
  }
  if (score > 0) {
    return "Weak. Unlikely to be copied.";
  }
  return "Not copyable on current data.";
}

function drawdownStatus(status: string | null | undefined) {
  if (!status || status === "ok") {
    return "";
  }
  return ` (${humanize(status).toLowerCase()})`;
}

function CurrentStatePanel({ state }: { state: WalletCurrentStateStats | null }) {
  if (!state) {
    return (
      <Panel title="Account right now">
        <EmptyState title="No account snapshot">The current positions of the wallet are read from Hyperliquid on request.</EmptyState>
      </Panel>
    );
  }
  const accountValue = decimal(state.accountValueUsd);
  const unrealized = decimal(state.totalUnrealizedPnlUsd);
  const drawdown = accountValue > 0 && unrealized < 0 ? Math.abs(unrealized) / accountValue : 0;
  return (
    <Panel
      title="Account right now"
      meta={<>snapshot <TimeAgo value={state.stateTimeMs} /></>}
    >
      {state.error ? <p className="px-3.5 py-3 text-sm text-negative">{state.error}</p> : null}
      <div className="grid gap-x-8 px-3.5 py-2.5 sm:grid-cols-2">
        <KeyValues
          items={[
            { label: "Account value", value: formatUsd(state.accountValueUsd) },
            { label: "Perp equity", value: formatUsd(state.perpEquityUsd ?? state.accountValueUsd) },
            { label: "Withdrawable", value: formatUsd(state.withdrawableUsd) },
            { label: "Spot USDC", value: formatUsd(state.spotUsdcBalance) },
          ]}
        />
        <KeyValues
          items={[
            { label: "Open PnL", value: <Pnl value={unrealized} /> },
            {
              hint: "Open losses as a share of account value",
              label: "Drawdown on open positions",
              tone: drawdown > 0.1 ? "negative" : "neutral",
              value: formatPercent(drawdown),
            },
            { label: "Position value", value: formatUsd(state.totalPositionNotionalUsd) },
            { label: "Margin used", value: formatUsd(state.totalMarginUsedUsd) },
          ]}
        />
      </div>
      {state.positions.length === 0 ? (
        <p className="border-t border-line px-3.5 py-3 text-sm text-fg-3">No open perp positions.</p>
      ) : (
        <div className="max-h-80 overflow-auto border-t border-line">
          <table className="data-table">
            <thead>
              <tr>
                <th>Market</th>
                <th className="num">Size</th>
                <th className="num">Value</th>
                <th className="num">Open PnL</th>
                <th className="num">Held</th>
              </tr>
            </thead>
            <tbody>
              {state.positions.map((position) => (
                <tr key={position.coin}>
                  <td>
                    <span className="font-medium">{position.coin}</span>
                    <span className="sub">
                      <Side
                        side={position.side === "flat" ? null : position.side}
                        suffix={position.leverageValue ? formatLeverage(position.leverageValue, position.leverageType) : undefined}
                      />
                    </span>
                  </td>
                  <td className="num">
                    {formatSize(position.size)}
                    <span className="sub">at {formatPrice(position.entryPrice)}</span>
                  </td>
                  <td className="num">
                    <Usd value={position.positionValueUsd} />
                    <span className="sub">liq. {formatPrice(position.liquidationPrice)}</span>
                  </td>
                  <td className="num">
                    <Pnl value={position.unrealizedPnlUsd} />
                    <span className="sub">{formatPercent(position.returnOnEquity, { signed: true })} on margin</span>
                  </td>
                  <td className="num text-fg-2">
                    {position.openedAtMs ? formatDuration(Date.now() - position.openedAtMs) : "-"}
                    {position.liquidationFillCount > 0 ? (
                      <span className="sub text-negative">{position.liquidationFillCount} liquidation fills</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function WindowsPanel({ windows }: { windows: SourceTradeWindowStats[] }) {
  return (
    <Panel
      title="Performance by period"
      description="From the wallet's own rebuilt trades. Close-only and pre-existing fills are left out."
    >
      {windows.length === 0 ? (
        <EmptyState title="No trades rebuilt yet" />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Period</th>
                <th className="num">Net PnL</th>
                <th className="num" title="Net PnL divided by entry volume">
                  ROI on volume
                </th>
                <th className="num">Win rate</th>
                <th className="num">Closed</th>
                <th className="num">Open</th>
                <th className="num">Entry volume</th>
                <th className="num">Fees</th>
              </tr>
            </thead>
            <tbody>
              {windows.map((window) => (
                <tr key={window.label}>
                  <td className="font-medium">{window.label}</td>
                  <td className="num">
                    <Pnl value={window.netPnlUsd} />
                  </td>
                  <td className="num">
                    <PnlPercent value={window.roiPct} />
                  </td>
                  <td className="num">{formatPercent(window.winRate)}</td>
                  <td className="num">{formatInteger(window.closedTradeCount)}</td>
                  <td className="num text-fg-2">{formatInteger(window.openTradeCount)}</td>
                  <td className="num">
                    <Usd value={window.entryNotionalUsd} compact />
                  </td>
                  <td className="num text-fg-2">{formatUsd(window.feeUsd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function FillStatsPanel({ stats }: { stats: WalletStats }) {
  return (
    <details className="group min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <summary className="flex min-h-10 list-none items-center justify-between gap-3 px-3.5 py-2 text-sm font-semibold hover:bg-surface-2">
        <span>
          Stored history{" "}
          <span className="font-normal text-fg-3">
            {formatInteger(stats.fillCount)} fills, {formatInteger(stats.uniqueCoinCount)} coins
          </span>
        </span>
        <span className="text-xs font-normal text-fg-3">
          <span className="group-open:hidden">Show</span>
          <span className="hidden group-open:inline">Hide</span>
        </span>
      </summary>
      <div className="grid gap-x-8 border-t border-line px-3.5 py-2.5 sm:grid-cols-2 lg:grid-cols-3">
        <KeyValues
          items={[
            { label: "Fills", value: formatInteger(stats.fillCount) },
            { label: "From snapshots", value: formatInteger(stats.snapshotFillCount) },
            { label: "From the realtime feed", value: formatInteger(stats.realtimeFillCount) },
          ]}
        />
        <KeyValues
          items={[
            { label: "Volume", value: formatUsd(stats.totalNotionalUsd, { compact: true }) },
            { label: "Average fill", value: formatUsd(stats.averageFillNotionalUsd) },
            {
              hint: "Sum of the PnL reported on each fill. Diagnostic only, not used for scoring.",
              label: "Raw fill PnL",
              value: <Pnl value={stats.totalPnlUsd} />,
            },
          ]}
        />
        <KeyValues
          items={[
            { label: "Fees", value: formatUsd(stats.totalFeeUsd) },
            { label: "Buys and sells", value: `${formatInteger(stats.buyCount)} / ${formatInteger(stats.sellCount)}` },
            {
              label: "Ingest delay",
              value: stats.averageIngestLatencyMs
                ? `${formatDuration(decimal(stats.averageIngestLatencyMs))} avg, ${formatDuration(stats.maxIngestLatencyMs)} max`
                : "-",
            },
          ]}
        />
      </div>
      <p className="border-t border-line px-3.5 py-2 text-xs text-fg-3">
        First fill <TimeAgo value={stats.firstFillTimeMs} />, latest fill <TimeAgo value={stats.lastFillTimeMs} />.{" "}
        <Badge>{formatInteger(stats.profitableFillCount)} profitable</Badge>{" "}
        <Badge>{formatInteger(stats.losingFillCount)} losing</Badge>
      </p>
    </details>
  );
}

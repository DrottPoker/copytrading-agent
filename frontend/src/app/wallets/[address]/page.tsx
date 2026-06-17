import Link from "next/link";
import { notFound } from "next/navigation";

import { ImportFillsButton } from "@/components/ImportFillsButton";
import { StatusPill } from "@/components/StatusPill";
import {
  getWallet,
  getWalletCopyTrades,
  getWalletFills,
  getWalletScoreDetail,
  getWalletSourceTrades,
  getWalletStats,
} from "@/lib/api";
import type {
  CopyTrade,
  SourceTrade,
  SourceTradeListResponse,
  WalletCoinStats,
  WalletCurrentStateStats,
  WalletFill,
  WalletPerpPositionStats,
  WalletScore,
  WalletScoreDetail,
  WalletScorePenaltyItem,
  WalletSpotBalanceStats,
  WalletStats,
  WalletWindowStats,
} from "@/types/wallet";

export default async function WalletDetailPage({
  params,
}: {
  params: Promise<{ address: string }>;
}) {
  const { address } = await params;
  const wallet = await getWallet(address);
  if (!wallet) {
    notFound();
  }

  const [stats, fills, sourceTrades, copyTrades, scoreDetail] = await Promise.all([
    getWalletStats(wallet.address),
    getWalletFills(wallet.address),
    getWalletSourceTrades(wallet.address),
    getWalletCopyTrades(wallet.address),
    getWalletScoreDetail(wallet.address),
  ]);

  return (
    <>
      <header className="rounded-lg border border-line bg-panel p-5 shadow-sm">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <Link href="/wallets" className="text-sm font-medium text-[#5b6770] hover:text-ink">
              Wallet Pool
            </Link>
            <h1 className="mt-1 truncate text-2xl font-semibold tracking-normal">
              {wallet.label || shortAddress(wallet.address)}
            </h1>
            <p className="mt-2 break-all font-mono text-sm text-[#5b6770]">{wallet.address}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <StatusPill
                label={wallet.enabled ? "enabled" : "disabled"}
                tone={wallet.enabled ? "positive" : "warning"}
              />
              <StatusPill label={wallet.pollingTier} tone="neutral" />
              <StatusPill label={`${stats?.fillCount ?? fills.total} fills`} tone="neutral" />
              {wallet.copyEnabled ? <StatusPill label="copy enabled" tone="positive" /> : null}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 xl:min-w-[520px]">
            <HeaderMetric
              label="Final score"
              value={wallet.score ? formatScore(wallet.score.score) : "-"}
              tone={scoreTone(wallet.score?.score)}
            />
            <HeaderMetric
              label="Copyable PnL"
              value={wallet.score ? formatCurrency(wallet.score.copyablePnlUsd) : "-"}
              tone={numberValue(wallet.score?.copyablePnlUsd ?? 0) >= 0 ? "positive" : "danger"}
            />
            <HeaderMetric
              label="Source trades"
              value={`${sourceTrades.summary.closedTradeCount}/${sourceTrades.summary.openTradeCount}`}
              detail="closed / open"
            />
          </div>
        </div>
      </header>

      <section className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
        <div className="rounded-lg border border-line bg-panel p-4 shadow-sm">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between lg:flex-col lg:items-stretch">
            <div>
              <h2 className="text-base font-semibold">History Import</h2>
              <p className="mt-2 text-sm leading-6 text-[#5b6770]">
                Pulls recent perp fills and updates wallet stats after dedupe.
              </p>
            </div>
            <ImportFillsButton address={wallet.address} />
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <InfoBlock
            title="Last poll"
            value={wallet.lastPolledAt ? formatDate(wallet.lastPolledAt) : "Never"}
          />
          <InfoBlock
            title="Last fill"
            value={wallet.lastSeenFillAt ? formatDate(wallet.lastSeenFillAt) : "No fills"}
          />
          <InfoBlock
            title="First fill"
            value={stats?.firstFillTimeMs ? formatMs(stats.firstFillTimeMs) : "-"}
          />
        </div>
      </section>

      {stats ? <WalletStatsPanel stats={stats} /> : <EmptyStatsPanel />}

      <ScoreBreakdownSection score={wallet.score} scoreDetail={scoreDetail} />

      {stats?.currentState ? <CurrentStateSection state={stats.currentState} /> : null}

      {stats ? <WindowStatsSection windows={stats.windows} /> : null}

      <SourceTradesSection sourceTrades={sourceTrades} />

      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        {stats ? <CoinStatsSection coins={stats.topCoins} /> : null}
        <CopyTradesSection trades={copyTrades.items} total={copyTrades.total} />
      </section>

      <FillsSection fills={fills.items} total={fills.total} />
    </>
  );
}

function WalletStatsPanel({ stats }: { stats: WalletStats }) {
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile label="Total fills" value={formatInteger(stats.fillCount)} detail="Stored source fills" />
      <StatTile
        label="Total notional"
        value={formatCurrency(stats.totalNotionalUsd)}
        detail={`${formatInteger(stats.uniqueCoinCount)} coins traded`}
      />
      <StatTile
        label="Realized PnL"
        value={formatCurrency(stats.totalPnlUsd)}
        detail={`${formatPercent(stats.winRate)} profitable fill rate`}
        tone={numberValue(stats.totalPnlUsd) >= 0 ? "positive" : "danger"}
      />
      <StatTile
        label="Fees"
        value={formatCurrency(stats.totalFeeUsd)}
        detail={`Avg fill ${formatCurrency(stats.averageFillNotionalUsd)}`}
      />
      <StatTile
        label="Buy / sell"
        value={`${formatInteger(stats.buyCount)} / ${formatInteger(stats.sellCount)}`}
        detail="Fill side distribution"
      />
      <StatTile
        label="Snapshots"
        value={formatInteger(stats.snapshotFillCount)}
        detail={`${formatInteger(stats.realtimeFillCount)} realtime fills`}
      />
      <StatTile
        label="Latency"
        value={
          stats.averageIngestLatencyMs ? `${formatInteger(stats.averageIngestLatencyMs)} ms` : "-"
        }
        detail={
          stats.maxIngestLatencyMs ? `Max ${formatInteger(stats.maxIngestLatencyMs)} ms` : "No latency data"
        }
      />
      <StatTile
        label="Last fill"
        value={stats.lastFillTimeMs ? formatMs(stats.lastFillTimeMs) : "-"}
        detail="Most recent stored fill"
      />
    </section>
  );
}

function HeaderMetric({
  detail,
  label,
  tone = "neutral",
  value,
}: {
  detail?: string;
  label: string;
  tone?: "positive" | "warning" | "danger" | "neutral";
  value: string;
}) {
  const toneClass =
    tone === "positive"
      ? "border-[#9ccfc0] bg-[#f2fbf7]"
      : tone === "warning"
        ? "border-[#e7c174] bg-[#fff9e8]"
        : tone === "danger"
          ? "border-[#efb1aa] bg-[#fff5f3]"
          : "border-line bg-[#f8fafb]";

  return (
    <div className={`rounded-lg border p-3 ${toneClass}`}>
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 truncate text-xl font-semibold">{value}</p>
      {detail ? <p className="mt-1 text-xs text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function ScoreBreakdownSection({
  score,
  scoreDetail,
}: {
  score: WalletScore | null;
  scoreDetail: WalletScoreDetail | null;
}) {
  if (!score) {
    return (
      <section className="rounded-lg border border-line bg-panel px-4 py-10 text-center text-sm text-[#526070]">
        No wallet score available yet.
      </section>
    );
  }

  const components = scoreComponents(score);
  const strengths = components.filter((component) => component.value >= 70);
  const weakSpots = components.filter((component) => component.value < 45);
  const penaltyItems = scoreDetail?.penaltyItems ?? [];
  const penalty = numberValue(scoreDetail?.penaltyScore ?? score.penaltyScore);
  const liquidationEvents = scoreDetail?.liquidationEventCount ?? scoreDetail?.liquidationCount ?? 0;
  const currentDrawdownPct = scoreDetail?.currentDrawdownPct ?? score.currentDrawdownPct;
  const currentDrawdownStatus =
    scoreDetail?.currentDrawdownStatus ?? score.currentDrawdownStatus;

  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-base font-semibold">Score Breakdown</h2>
          <p className="mt-1 text-sm text-[#526070]">
            Last scored {formatDate(score.updatedAt)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill label={`score ${formatScore(score.score)}`} tone={scoreTone(score.score)} />
          <StatusPill label={`${formatInteger(score.tradeCount)} trades`} tone="neutral" />
          <StatusPill
            label={`penalty ${formatPenaltyScore(penalty)}`}
            tone={penalty >= 35 ? "danger" : penalty > 0 ? "warning" : "positive"}
          />
          {liquidationEvents > 0 ? (
            <StatusPill
              label={`${formatInteger(liquidationEvents)} liq events`}
              tone={liquidationEvents >= 2 ? "danger" : "warning"}
            />
          ) : null}
        </div>
      </div>

      <div className="grid gap-0 divide-y divide-line xl:grid-cols-[0.9fr_1.1fr] xl:divide-x xl:divide-y-0">
        <div className="p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-medium uppercase text-[#526070]">Final score</p>
              <p className={`mt-2 text-5xl font-semibold ${scoreTextClass(score.score)}`}>
                {formatScore(score.score)}
              </p>
            </div>
            <div className="grid gap-2 text-sm text-[#526070] sm:text-right">
              <p>Copyable PnL {formatCurrency(score.copyablePnlUsd)}</p>
              <p>Win rate {formatPercent(score.winRate)}</p>
              <p>Profit factor {formatNullableNumber(score.profitFactor)}</p>
              <p>Historical max drawdown {formatPercent(score.maxDrawdownPct)}</p>
              <p>
                Current drawdown {formatPercent(currentDrawdownPct)}
                {formatCurrentDrawdownStatus(currentDrawdownStatus)}
              </p>
            </div>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-3">
            <MiniScoreTile label="24H" value={score.last24hScore} />
            <MiniScoreTile label="7D" value={score.last7dScore} />
            <MiniScoreTile label="30D" value={score.last30dScore} />
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <ScoreSignalList title="Shines" items={strengths} empty="No component above 70." />
            <ScoreSignalList title="Falls" items={weakSpots} empty="No component below 45." />
          </div>
        </div>

        <div className="divide-y divide-line">
          {components.map((component) => (
            <ScoreComponentRow key={component.key} component={component} />
          ))}
          <ScoreComponentRow
            component={{
              detail: activePenaltySummary(penaltyItems),
              key: "penalty",
              label: "Penalty",
              value: penalty,
            }}
            inverted
          />
          <PenaltyBreakdownList items={penaltyItems} />
        </div>
      </div>
    </section>
  );
}

function MiniScoreTile({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="border-l-2 border-line pl-3">
      <p className="text-xs font-medium uppercase text-[#526070]">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${scoreTextClass(value)}`}>
        {value ? formatScore(value) : "-"}
      </p>
    </div>
  );
}

function ScoreSignalList({
  title,
  items,
  empty,
}: {
  title: string;
  items: ScoreComponent[];
  empty: string;
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-[#526070]">{title}</p>
      <div className="mt-2 grid gap-2">
        {items.length === 0 ? (
          <p className="text-sm text-[#526070]">{empty}</p>
        ) : (
          items.map((item) => (
            <div key={item.key} className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate text-[#526070]">{item.label}</span>
              <span className={`font-semibold ${scoreTextClass(item.value)}`}>
                {formatScore(item.value)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ScoreComponentRow({
  component,
  inverted = false,
}: {
  component: ScoreComponent;
  inverted?: boolean;
}) {
  const toneClass = inverted
    ? component.value >= 35
      ? "bg-danger"
      : component.value > 0
        ? "bg-warning"
        : "bg-positive"
    : component.value >= 70
      ? "bg-positive"
      : component.value >= 45
        ? "bg-warning"
        : "bg-danger";

  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{component.label}</p>
          <p className="mt-1 text-sm text-[#526070]">{component.detail}</p>
        </div>
        <span className={`shrink-0 text-lg font-semibold ${scoreTextClass(component.value, inverted)}`}>
          {inverted ? formatPenaltyScore(component.value) : formatScore(component.value)}
        </span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#e4e9ef]">
        <div
          className={`h-full rounded-full ${toneClass}`}
          style={{ width: `${Math.max(0, Math.min(100, component.value))}%` }}
        />
      </div>
    </div>
  );
}

function PenaltyBreakdownList({ items }: { items: WalletScorePenaltyItem[] }) {
  if (items.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-[#526070]">
        Penalty source details are not available yet.
      </div>
    );
  }

  return (
    <div className="px-4 py-3">
      <p className="text-xs font-medium uppercase text-[#526070]">Penalty sources</p>
      <div className="mt-3 grid gap-3">
        {items.map((item) => (
          <PenaltyItemRow key={item.key} item={item} />
        ))}
      </div>
    </div>
  );
}

function PenaltyItemRow({ item }: { item: WalletScorePenaltyItem }) {
  const value = numberValue(item.value);
  const maxValue = Math.max(numberValue(item.maxValue), 1);
  const width = Math.max(0, Math.min(100, (value / maxValue) * 100));
  const toneClass = value >= 20 ? "bg-danger" : value > 0 ? "bg-warning" : "bg-positive";

  return (
    <div className={item.active ? "" : "opacity-70"}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{item.label}</p>
          <p className="mt-1 text-sm text-[#526070]">{item.detail}</p>
        </div>
        <span className={`shrink-0 font-semibold ${scoreTextClass(value, true)}`}>
          {formatPenaltyScore(value)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e4e9ef]">
        <div className={`h-full rounded-full ${toneClass}`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function CurrentStateSection({ state }: { state: WalletCurrentStateStats }) {
  const currentDrawdownPct = currentUnrealizedDrawdownPct(state);

  return (
    <section className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
      <div className="min-w-0 rounded-lg border border-line bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">Perp State</h2>
          <StatusPill
            label={`${formatInteger(state.openPositionCount)} open`}
            tone={state.openPositionCount > 0 ? "positive" : "neutral"}
          />
        </div>
        {state.error ? (
          <div className="px-4 py-6 text-sm text-danger">{state.error}</div>
        ) : (
          <>
            <div className="grid gap-0 divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-3">
              <StateMetric label="Perp equity" value={formatCurrency(perpEquityUsd(state))} />
              <StateMetric label="Withdrawable" value={formatCurrency(state.withdrawableUsd)} />
              <StateMetric
                label="Unrealized PnL"
                value={formatCurrency(state.totalUnrealizedPnlUsd)}
                tone={numberValue(state.totalUnrealizedPnlUsd) >= 0 ? "positive" : "danger"}
              />
              <StateMetric
                label="Current drawdown"
                value={formatPercent(currentDrawdownPct)}
                tone={currentDrawdownPct > 0 ? "danger" : "neutral"}
              />
              <StateMetric
                label="Position notional"
                value={formatCurrency(state.totalPositionNotionalUsd)}
              />
              <StateMetric label="Margin used" value={formatCurrency(state.totalMarginUsedUsd)} />
            </div>
            <PerpPositionsTable positions={state.positions} />
          </>
        )}
      </div>

      <div className="min-w-0 rounded-lg border border-line bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">Spot Exposure</h2>
          <StatusPill label={`${formatInteger(state.spotBalanceCount)} balances`} tone="neutral" />
        </div>
        <div className="grid gap-0 divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0">
          <StateMetric label="USDC balance" value={formatCurrency(state.spotUsdcBalance)} />
          <StateMetric label="Entry notional" value={formatCurrency(state.spotEntryNotionalUsd)} />
        </div>
        <SpotBalancesTable balances={state.spotBalances} />
      </div>
    </section>
  );
}

function StateMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "positive" | "danger" | "neutral";
}) {
  const toneClass =
    tone === "positive" ? "text-positive" : tone === "danger" ? "text-danger" : "text-ink";

  return (
    <div className="p-4">
      <p className="text-xs font-medium uppercase text-[#526070]">{label}</p>
      <p className={`mt-2 text-lg font-semibold ${toneClass}`}>{value}</p>
    </div>
  );
}

function PerpPositionsTable({ positions }: { positions: WalletPerpPositionStats[] }) {
  return (
    <div className="overflow-x-auto border-t border-line">
      <table className="w-full min-w-[820px] border-collapse text-left text-sm">
        <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
          <tr>
            <th className="px-4 py-3 font-semibold">Coin</th>
            <th className="px-4 py-3 font-semibold">Side</th>
            <th className="px-4 py-3 font-semibold">Size</th>
            <th className="px-4 py-3 font-semibold">Entry</th>
            <th className="px-4 py-3 font-semibold">Notional</th>
            <th className="px-4 py-3 font-semibold">Unrealized</th>
            <th className="px-4 py-3 font-semibold">Liq</th>
            <th className="px-4 py-3 font-semibold">Lev</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 ? (
            <tr>
              <td colSpan={8} className="px-4 py-10 text-center text-[#526070]">
                No open perp positions.
              </td>
            </tr>
          ) : (
            positions.map((position) => <PerpPositionRow key={position.coin} position={position} />)
          )}
        </tbody>
      </table>
    </div>
  );
}

function PerpPositionRow({ position }: { position: WalletPerpPositionStats }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-semibold">{position.coin}</td>
      <td className="px-4 py-3">{position.side}</td>
      <td className="px-4 py-3 font-mono">{formatCompactNumber(position.size)}</td>
      <td className="px-4 py-3 font-mono">{formatPrice(position.entryPrice)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(position.positionValueUsd)}</td>
      <td className={pnlClass(position.unrealizedPnlUsd)}>
        {position.unrealizedPnlUsd ? formatCurrency(position.unrealizedPnlUsd) : "-"}
      </td>
      <td className="px-4 py-3 font-mono">{formatPrice(position.liquidationPrice)}</td>
      <td className="px-4 py-3">
        {position.leverageValue ? `${position.leverageValue}x ${position.leverageType ?? ""}` : "-"}
      </td>
    </tr>
  );
}

function SpotBalancesTable({ balances }: { balances: WalletSpotBalanceStats[] }) {
  return (
    <div className="overflow-x-auto border-t border-line">
      <table className="w-full min-w-[620px] border-collapse text-left text-sm">
        <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
          <tr>
            <th className="px-4 py-3 font-semibold">Coin</th>
            <th className="px-4 py-3 font-semibold">Total</th>
            <th className="px-4 py-3 font-semibold">Hold</th>
            <th className="px-4 py-3 font-semibold">Entry notional</th>
          </tr>
        </thead>
        <tbody>
          {balances.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-4 py-10 text-center text-[#526070]">
                No spot balances.
              </td>
            </tr>
          ) : (
            balances.map((balance) => (
              <SpotBalanceRow key={`${balance.coin}-${balance.token}`} balance={balance} />
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function SpotBalanceRow({ balance }: { balance: WalletSpotBalanceStats }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-semibold">{balance.coin}</td>
      <td className="px-4 py-3 font-mono">{formatCompactNumber(balance.total)}</td>
      <td className="px-4 py-3 font-mono">{formatCompactNumber(balance.hold)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(balance.entryNotionalUsd)}</td>
    </tr>
  );
}

function WindowStatsSection({ windows }: { windows: WalletWindowStats[] }) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold">Time Windows</h2>
      </div>
      <div className="grid gap-0 divide-y divide-line md:grid-cols-3 md:divide-x md:divide-y-0">
        {windows.map((window) => (
          <div key={window.label} className="p-4">
            <p className="text-xs font-medium uppercase text-[#526070]">{window.label}</p>
            <p className="mt-2 text-lg font-semibold">{formatCurrency(window.pnlUsd)}</p>
            <div className="mt-3 grid gap-1 text-sm text-[#526070]">
              <p>{formatInteger(window.fillCount)} fills</p>
              <p>{formatCurrency(window.notionalUsd)} notional</p>
              <p>{formatCurrency(window.feeUsd)} fees</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function CoinStatsSection({ coins }: { coins: WalletCoinStats[] }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold">Top Coins</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left text-sm">
          <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
            <tr>
              <th className="px-4 py-3 font-semibold">Coin</th>
              <th className="px-4 py-3 font-semibold">Fills</th>
              <th className="px-4 py-3 font-semibold">Buy / sell</th>
              <th className="px-4 py-3 font-semibold">Notional</th>
              <th className="px-4 py-3 font-semibold">PnL</th>
            </tr>
          </thead>
          <tbody>
            {coins.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-[#526070]">
                  No coin stats yet.
                </td>
              </tr>
            ) : (
              coins.map((coin) => (
                <tr key={coin.coin} className="border-b border-line last:border-b-0">
                  <td className="px-4 py-3 font-semibold">{coin.coin}</td>
                  <td className="px-4 py-3">{formatInteger(coin.fillCount)}</td>
                  <td className="px-4 py-3">
                    {formatInteger(coin.buyCount)} / {formatInteger(coin.sellCount)}
                  </td>
                  <td className="px-4 py-3 font-mono">{formatCurrency(coin.notionalUsd)}</td>
                  <td className={pnlClass(coin.pnlUsd)}>{formatCurrency(coin.pnlUsd)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SourceTradesSection({ sourceTrades }: { sourceTrades: SourceTradeListResponse }) {
  const { summary } = sourceTrades;
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-base font-semibold">Source Trades</h2>
          <p className="mt-1 text-sm text-[#526070]">
            Reconstructed from observed open and close fills over {sourceTrades.days}D.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill label={`${summary.closedTradeCount} closed`} tone="positive" />
          <StatusPill label={`${summary.openTradeCount} open`} tone="neutral" />
          <StatusPill label={`${summary.unmatchedCloseFillCount} close-only`} tone="warning" />
          <StatusPill label={`${summary.preexistingOpenFillCount} pre-existing adds`} tone="warning" />
        </div>
      </div>
      <div className="grid gap-0 divide-y divide-line sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <StateMetric label="Entry notional" value={formatCurrency(summary.totalEntryNotionalUsd)} />
        <StateMetric
          label="Net PnL"
          value={formatCurrency(summary.netPnlUsd)}
          tone={numberValue(summary.netPnlUsd) >= 0 ? "positive" : "danger"}
        />
        <StateMetric label="Realized PnL" value={formatCurrency(summary.realizedPnlUsd)} />
        <StateMetric label="Fees" value={formatCurrency(summary.feeUsd)} />
      </div>
      <div className="overflow-x-auto border-t border-line">
        <table className="w-full min-w-[1180px] border-collapse text-left text-sm">
          <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
            <tr>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Coin</th>
              <th className="px-4 py-3 font-semibold">Side</th>
              <th className="px-4 py-3 font-semibold">Opened</th>
              <th className="px-4 py-3 font-semibold">Closed</th>
              <th className="px-4 py-3 font-semibold">Duration</th>
              <th className="px-4 py-3 font-semibold">Entry</th>
              <th className="px-4 py-3 font-semibold">Exit</th>
              <th className="px-4 py-3 font-semibold">Size</th>
              <th className="px-4 py-3 font-semibold">Notional</th>
              <th className="px-4 py-3 font-semibold">Net PnL</th>
              <th className="px-4 py-3 font-semibold">Fills</th>
            </tr>
          </thead>
          <tbody>
            {sourceTrades.items.length === 0 ? (
              <tr>
                <td colSpan={12} className="px-4 py-10 text-center text-[#526070]">
                  No complete source trades reconstructed for this window.
                </td>
              </tr>
            ) : (
              sourceTrades.items.map((trade) => (
                <SourceTradeRow key={trade.id} trade={trade} />
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SourceTradeRow({ trade }: { trade: SourceTrade }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3">
        <StatusPill label={trade.status} tone={trade.status === "closed" ? "positive" : "neutral"} />
      </td>
      <td className="px-4 py-3 font-semibold">{trade.coin}</td>
      <td className="px-4 py-3">{trade.side}</td>
      <td className="px-4 py-3 text-[#526070]">{formatMs(trade.openedAtMs)}</td>
      <td className="px-4 py-3 text-[#526070]">
        {trade.closedAtMs ? formatMs(trade.closedAtMs) : "-"}
      </td>
      <td className="px-4 py-3 text-[#526070]">{formatDuration(trade.durationMs)}</td>
      <td className="px-4 py-3 font-mono">{formatPrice(trade.averageEntryPrice)}</td>
      <td className="px-4 py-3 font-mono">{formatPrice(trade.averageExitPrice)}</td>
      <td className="px-4 py-3 font-mono">{formatCompactNumber(trade.closedSize)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(trade.entryNotionalUsd)}</td>
      <td className={pnlClass(trade.netPnlUsd)}>{formatCurrency(trade.netPnlUsd)}</td>
      <td className="px-4 py-3 text-[#526070]">
        {trade.entryFillCount} / {trade.closeFillCount}
      </td>
    </tr>
  );
}

function CopyTradesSection({ trades, total }: { trades: CopyTrade[]; total: number }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold">Copy Trades</h2>
        <StatusPill label={`${total} trades`} tone="neutral" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
            <tr>
              <th className="px-4 py-3 font-semibold">Status</th>
              <th className="px-4 py-3 font-semibold">Coin</th>
              <th className="px-4 py-3 font-semibold">Side</th>
              <th className="px-4 py-3 font-semibold">Size</th>
              <th className="px-4 py-3 font-semibold">Entry</th>
              <th className="px-4 py-3 font-semibold">PnL</th>
              <th className="px-4 py-3 font-semibold">Opened</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-[#526070]">
                  No paper or live copy trades have been created for this wallet yet.
                </td>
              </tr>
            ) : (
              trades.map((trade) => <CopyTradeRow key={trade.id} trade={trade} />)
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CopyTradeRow({ trade }: { trade: CopyTrade }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3">
        <StatusPill label={trade.status} tone={trade.status === "open" ? "positive" : "neutral"} />
      </td>
      <td className="px-4 py-3 font-semibold">{trade.coin}</td>
      <td className="px-4 py-3">{trade.side}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(trade.sizeUsd)}</td>
      <td className="px-4 py-3 font-mono">{trade.ourEntryPrice ?? trade.sourceEntryPrice ?? "-"}</td>
      <td className={pnlClass(trade.pnlUsd)}>{trade.pnlUsd ? formatCurrency(trade.pnlUsd) : "-"}</td>
      <td className="px-4 py-3 text-[#526070]">
        {trade.openedAt ? formatDate(trade.openedAt) : "-"}
      </td>
    </tr>
  );
}

function FillsSection({ fills, total }: { fills: WalletFill[]; total: number }) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold">Source Fills</h2>
        <StatusPill label={`${total} stored`} tone="neutral" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1020px] border-collapse text-left text-sm">
          <thead className="border-b border-line bg-[#f7f9fb] text-xs uppercase text-[#526070]">
            <tr>
              <th className="px-4 py-3 font-semibold">Time</th>
              <th className="px-4 py-3 font-semibold">Coin</th>
              <th className="px-4 py-3 font-semibold">Side</th>
              <th className="px-4 py-3 font-semibold">Price</th>
              <th className="px-4 py-3 font-semibold">Size</th>
              <th className="px-4 py-3 font-semibold">Notional</th>
              <th className="px-4 py-3 font-semibold">PnL</th>
              <th className="px-4 py-3 font-semibold">Source</th>
            </tr>
          </thead>
          <tbody>
            {fills.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-[#526070]">
                  No fills imported yet.
                </td>
              </tr>
            ) : (
              fills.map((fill) => <FillRow fill={fill} key={fill.id} />)
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FillRow({ fill }: { fill: WalletFill }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 text-[#526070]">{formatMs(fill.timestampMs)}</td>
      <td className="px-4 py-3 font-semibold">{fill.coin}</td>
      <td className="px-4 py-3">{fill.side}</td>
      <td className="px-4 py-3 font-mono">{fill.price}</td>
      <td className="px-4 py-3 font-mono">{fill.size}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(fill.notionalUsd)}</td>
      <td className={pnlClass(fill.pnlUsd)}>{fill.pnlUsd ? formatCurrency(fill.pnlUsd) : "-"}</td>
      <td className="px-4 py-3">
        <StatusPill label={fill.isSnapshot ? "snapshot" : "realtime"} tone="neutral" />
      </td>
    </tr>
  );
}

function EmptyStatsPanel() {
  return (
    <section className="rounded-lg border border-line bg-panel px-4 py-10 text-center text-sm text-[#526070]">
      No wallet stats available yet. Import fills to populate this page.
    </section>
  );
}

function InfoBlock({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <p className="text-xs font-medium uppercase text-[#526070]">{title}</p>
      <p className="mt-2 text-sm font-semibold">{value}</p>
    </div>
  );
}

function StatTile({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "positive" | "danger" | "neutral";
}) {
  const toneClass =
    tone === "positive"
      ? "border-[#a7d8c4] bg-[#eefaf5]"
      : tone === "danger"
        ? "border-[#f2aaa5] bg-[#fff2f0]"
        : "border-line bg-panel";

  return (
    <article className={`rounded-lg border p-4 ${toneClass}`}>
      <p className="text-xs font-medium uppercase text-[#526070]">{label}</p>
      <p className="mt-2 truncate text-xl font-semibold text-ink">{value}</p>
      <p className="mt-2 min-h-5 text-sm text-[#526070]">{detail}</p>
    </article>
  );
}

type ScoreComponent = {
  key: string;
  label: string;
  value: number;
  detail: string;
};

function scoreComponents(score: WalletScore): ScoreComponent[] {
  return [
    {
      detail: "Net realized PnL against traded notional.",
      key: "pnl",
      label: "PnL",
      value: numberValue(score.pnlScore),
    },
    {
      detail: "Win rate, profit factor, and active trading days.",
      key: "consistency",
      label: "Consistency",
      value: numberValue(score.consistencyScore),
    },
    {
      detail: "Loss ratio, historical drawdown, current drawdown, and losing trade rate.",
      key: "risk",
      label: "Risk",
      value: numberValue(score.riskScore),
    },
    {
      detail: "Trade count, trade size, coin spread, and concentration.",
      key: "copyability",
      label: "Copyability",
      value: numberValue(score.copyabilityScore),
    },
    {
      detail: "Age of the latest reconstructed source trade.",
      key: "recency",
      label: "Recency",
      value: numberValue(score.recencyScore),
    },
  ];
}

function activePenaltySummary(items: WalletScorePenaltyItem[]) {
  if (items.length === 0) {
    return "Subtracts from the weighted component score.";
  }

  const activeItems = items.filter((item) => item.active);
  if (activeItems.length === 0) {
    return "No active penalty sources.";
  }

  return activeItems.map((item) => item.label).join(", ");
}

function pnlClass(value: string | null) {
  const base = "px-4 py-3 font-mono";
  if (!value) {
    return base;
  }
  return numberValue(value) >= 0 ? `${base} text-positive` : `${base} text-danger`;
}

function scoreTextClass(
  value: string | number | null | undefined,
  inverted = false,
) {
  if (value === null || value === undefined) {
    return "text-[#526070]";
  }

  const score = numberValue(value);
  if (inverted) {
    if (score >= 35) {
      return "text-danger";
    }
    if (score > 0) {
      return "text-warning";
    }
    return "text-positive";
  }

  if (score >= 70) {
    return "text-positive";
  }
  if (score >= 45) {
    return "text-warning";
  }
  return "text-danger";
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("sv-SE", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatMs(value: number) {
  return formatDate(new Date(value).toISOString());
}

function formatInteger(value: string | number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 }).format(numberValue(value));
}

function formatCurrency(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    currency: "USD",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(numberValue(value));
}

function formatPrice(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 8,
  }).format(numberValue(value));
}

function formatCompactNumber(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 6,
  }).format(numberValue(value));
}

function formatDuration(value: number | null) {
  if (value === null) {
    return "-";
  }
  const totalMinutes = Math.max(0, Math.round(value / 60_000));
  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 48) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `${days}d ${restHours}h` : `${days}d`;
}

function formatPercent(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 1,
    style: "percent",
  }).format(numberValue(value));
}

function formatCurrentDrawdownStatus(status: string | null | undefined) {
  if (!status || status === "ok") {
    return "";
  }
  const labels: Record<string, string> = {
    disabled: "disabled",
    unavailable: "unavailable",
    zero_equity: "zero equity",
  };
  return ` (${labels[status] ?? status})`;
}

function formatNullableNumber(value: string | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(numberValue(value));
}

function formatScore(value: string | number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(numberValue(value));
}

function formatPenaltyScore(value: string | number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(numberValue(value));
}

function scoreTone(value: string | null | undefined): "positive" | "warning" | "danger" | "neutral" {
  if (!value) {
    return "neutral";
  }
  const score = numberValue(value);
  if (score >= 70) {
    return "positive";
  }
  if (score >= 45) {
    return "warning";
  }
  return "danger";
}

function currentUnrealizedDrawdownPct(state: WalletCurrentStateStats) {
  const perpEquity = numberValue(perpEquityUsd(state));
  const unrealizedPnlUsd = numberValue(state.totalUnrealizedPnlUsd);

  if (perpEquity <= 0 || unrealizedPnlUsd >= 0) {
    return 0;
  }

  return Math.abs(unrealizedPnlUsd) / perpEquity;
}

function perpEquityUsd(state: WalletCurrentStateStats) {
  return state.perpEquityUsd ?? state.accountValueUsd;
}

function numberValue(value: string | number) {
  return typeof value === "number" ? value : Number(value);
}

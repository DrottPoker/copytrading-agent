"use client";

import { X } from "lucide-react";
import { useCallback, useState } from "react";

import { Pagination, Tabs, usePagination, type TabItem } from "@/components/ui/client";
import { Badge, buttonClass, Callout, EmptyState, Pnl, Side, StatusDot, Usd } from "@/components/ui/primitives";
import {
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPrice,
  formatSize,
  formatUsd,
} from "@/lib/format";
import type {
  CopyTrade,
  SourceTrade,
  SourceTradeSummary,
  Wallet,
  WalletCoinStats,
  WalletFill,
  WalletSpotBalanceStats,
} from "@/types/wallet";

import { ImportFillsButton, useWalletActions, WalletMenu, type Notice } from "./walletActions";

export function WalletDetailActions({ wallet }: { wallet: Wallet }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const onNotice = useCallback((next: Notice) => setNotice(next), []);
  const { busyAddress, menuItems } = useWalletActions(onNotice);
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <ImportFillsButton wallet={wallet} onNotice={onNotice} />
        <WalletMenu busy={busyAddress === wallet.address} items={menuItems(wallet).slice(1)} />
      </div>
      {notice ? (
        <Callout
          className="max-w-md"
          tone={notice.tone}
          title={notice.text}
          action={
            <button
              type="button"
              onClick={() => setNotice(null)}
              className={buttonClass({ size: "xs", variant: "ghost" })}
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          }
        />
      ) : null}
    </div>
  );
}

type HistoryTab = "trades" | "copies" | "coins" | "fills" | "spot";

const PAGE_SIZE = 30;

export function WalletHistory({
  coins,
  copyTrades,
  copyTradeTotal,
  fills,
  fillTotal,
  sourceTradeDays,
  sourceTradeSummary,
  sourceTrades,
  spotBalances,
}: {
  coins: WalletCoinStats[];
  copyTrades: CopyTrade[];
  copyTradeTotal: number;
  fills: WalletFill[];
  fillTotal: number;
  sourceTradeDays: number | null;
  sourceTradeSummary: SourceTradeSummary;
  sourceTrades: SourceTrade[];
  spotBalances: WalletSpotBalanceStats[];
}) {
  const [tab, setTab] = useState<HistoryTab>("trades");
  const tabs: Array<TabItem<HistoryTab>> = [
    { count: sourceTrades.length, id: "trades", label: "Trades" },
    { count: copyTradeTotal, id: "copies", label: "Our copies" },
    { count: coins.length, id: "coins", label: "Coins" },
    { count: fillTotal, id: "fills", label: "Raw fills" },
    { count: spotBalances.length, id: "spot", label: "Spot balances" },
  ];

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <div className="border-b border-line px-3.5">
        <Tabs label="Wallet history" items={tabs} value={tab} onChange={setTab} />
      </div>
      {tab === "trades" ? (
        <SourceTrades days={sourceTradeDays} summary={sourceTradeSummary} trades={sourceTrades} />
      ) : null}
      {tab === "copies" ? <CopyTrades trades={copyTrades} /> : null}
      {tab === "coins" ? <Coins coins={coins} /> : null}
      {tab === "fills" ? <Fills fills={fills} total={fillTotal} /> : null}
      {tab === "spot" ? <SpotBalances balances={spotBalances} /> : null}
    </section>
  );
}

function SourceTrades({
  days,
  summary,
  trades,
}: {
  days: number | null;
  summary: SourceTradeSummary;
  trades: SourceTrade[];
}) {
  const { page, pageCount, pageItems, setPage } = usePagination(trades, PAGE_SIZE);
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-line px-3.5 py-2 text-xs text-fg-3">
        <span>
          Rebuilt from {days ? `the last ${days} days of` : "all"} stored fills.
        </span>
        <span>
          {formatInteger(summary.closedTradeCount)} closed, {formatInteger(summary.openTradeCount)} open
        </span>
        <span>
          net <Pnl value={summary.netPnlUsd} />, fees {formatUsd(summary.feeUsd)}
        </span>
        <span>entry volume {formatUsd(summary.totalEntryNotionalUsd, { compact: true })}</span>
        {summary.liquidationTradeCount > 0 ? (
          <Badge tone="negative">{formatInteger(summary.liquidationTradeCount)} liquidated</Badge>
        ) : null}
        <span title="Close fills without a matching open, and adds to positions opened before the history starts. Both are left out of the trades.">
          ignored: {formatInteger(summary.unmatchedCloseFillCount)} close-only, {formatInteger(summary.preexistingOpenFillCount)} pre-existing
        </span>
      </div>
      {trades.length === 0 ? (
        <EmptyState title="No trades rebuilt yet">Import fills for this wallet to rebuild its trades.</EmptyState>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Opened</th>
                  <th>Market</th>
                  <th>Status</th>
                  <th className="num">Held</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">Size</th>
                  <th className="num">Entry value</th>
                  <th className="num">Fees</th>
                  <th className="num">Net PnL</th>
                  <th className="num" title="Entry fills and close fills">
                    Fills
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((trade) => (
                  <tr key={trade.id}>
                    <td className="tabular-nums text-fg-2">{formatDateTime(trade.openedAtMs)}</td>
                    <td>
                      <span className="font-medium">{trade.coin}</span> <Side side={trade.side} />
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot
                          tone={trade.status === "closed" ? "neutral" : "info"}
                          label={trade.status === "closed" ? `Closed ${formatDateTime(trade.closedAtMs)}` : "Open"}
                        />
                        {trade.hasLiquidation ? <Badge tone="negative">Liquidated</Badge> : null}
                      </span>
                    </td>
                    <td className="num text-fg-2">{formatDuration(trade.durationMs)}</td>
                    <td className="num">{formatPrice(trade.averageEntryPrice)}</td>
                    <td className="num">{formatPrice(trade.averageExitPrice)}</td>
                    <td className="num">{formatSize(trade.closedSize)}</td>
                    <td className="num">
                      <Usd value={trade.entryNotionalUsd} />
                    </td>
                    <td className="num text-fg-2">{formatUsd(trade.feeUsd)}</td>
                    <td className="num font-medium">
                      <Pnl value={trade.netPnlUsd} />
                    </td>
                    <td className="num text-fg-2">
                      {trade.entryFillCount} / {trade.closeFillCount}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageCount={pageCount} pageSize={PAGE_SIZE} total={trades.length} onPageChange={setPage} />
        </>
      )}
    </>
  );
}

function CopyTrades({ trades }: { trades: CopyTrade[] }) {
  if (trades.length === 0) {
    return <EmptyState title="Not copied yet">No paper or live copy trade has been created from this wallet.</EmptyState>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Opened</th>
            <th>Market</th>
            <th>Mode</th>
            <th>Status</th>
            <th className="num">Size</th>
            <th className="num">Source entry</th>
            <th className="num">Our entry</th>
            <th className="num">PnL</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={trade.id}>
              <td className="tabular-nums text-fg-2">{formatDateTime(trade.openedAt ?? trade.createdAt)}</td>
              <td>
                <span className="font-medium">{trade.coin}</span> <Side side={trade.side} />
              </td>
              <td className="text-fg-2">{trade.mode === "paper" ? "Paper" : "Live"}</td>
              <td>
                <StatusDot
                  tone={trade.status === "open" ? "info" : trade.status === "error" ? "negative" : "neutral"}
                  label={trade.status.charAt(0).toUpperCase() + trade.status.slice(1)}
                />
              </td>
              <td className="num">
                <Usd value={trade.sizeUsd} />
              </td>
              <td className="num">{formatPrice(trade.sourceEntryPrice)}</td>
              <td className="num">{formatPrice(trade.ourEntryPrice)}</td>
              <td className="num">{trade.pnlUsd === null ? "-" : <Pnl value={trade.pnlUsd} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Coins({ coins }: { coins: WalletCoinStats[] }) {
  if (coins.length === 0) {
    return <EmptyState title="No coin statistics yet" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Coin</th>
            <th className="num">Fills</th>
            <th className="num">Buys / sells</th>
            <th className="num">Volume</th>
            <th className="num">Fees</th>
            <th className="num" title="Sum of the PnL reported on each fill">
              Fill PnL
            </th>
            <th className="num">Last fill</th>
          </tr>
        </thead>
        <tbody>
          {coins.map((coin) => (
            <tr key={coin.coin}>
              <td className="font-medium">{coin.coin}</td>
              <td className="num">{formatInteger(coin.fillCount)}</td>
              <td className="num text-fg-2">
                {formatInteger(coin.buyCount)} / {formatInteger(coin.sellCount)}
              </td>
              <td className="num">
                <Usd value={coin.notionalUsd} compact />
              </td>
              <td className="num text-fg-2">{formatUsd(coin.feeUsd)}</td>
              <td className="num">
                <Pnl value={coin.pnlUsd} />
              </td>
              <td className="num text-fg-3">{formatDateTime(coin.lastFillTimeMs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fills({ fills, total }: { fills: WalletFill[]; total: number }) {
  if (fills.length === 0) {
    return <EmptyState title="No fills imported yet" />;
  }
  return (
    <>
      <p className="border-b border-line px-3.5 py-2 text-xs text-fg-3">
        The latest {formatInteger(fills.length)} of {formatInteger(total)} stored fills.
      </p>
      <div className="max-h-[560px] overflow-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Coin</th>
              <th>Side</th>
              <th className="num">Price</th>
              <th className="num">Size</th>
              <th className="num">Value</th>
              <th className="num">PnL</th>
              <th className="num" title="Time from the trade on the exchange to it being stored">
                Ingest delay
              </th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {fills.map((fill) => (
              <tr key={fill.id}>
                <td className="tabular-nums text-fg-2">{formatDateTime(fill.timestampMs, { seconds: true })}</td>
                <td className="font-medium">{fill.coin}</td>
                <td>
                  <Side side={fill.side} />
                </td>
                <td className="num">{formatPrice(fill.price)}</td>
                <td className="num">{formatSize(fill.size)}</td>
                <td className="num">
                  <Usd value={fill.notionalUsd} />
                </td>
                <td className="num">{fill.pnlUsd === null ? "-" : <Pnl value={fill.pnlUsd} />}</td>
                <td className="num text-fg-2">{formatDuration(fill.ingestLatencyMs)}</td>
                <td className="text-fg-3">{fill.isSnapshot ? "Snapshot" : "Realtime"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SpotBalances({ balances }: { balances: WalletSpotBalanceStats[] }) {
  if (balances.length === 0) {
    return <EmptyState title="No spot balances" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Token</th>
            <th className="num">Total</th>
            <th className="num">On hold</th>
            <th className="num">Entry value</th>
          </tr>
        </thead>
        <tbody>
          {balances.map((balance) => (
            <tr key={`${balance.coin}:${balance.token}`}>
              <td className="font-medium">{balance.coin}</td>
              <td className="num">{formatSize(balance.total)}</td>
              <td className="num text-fg-2">{formatSize(balance.hold)}</td>
              <td className="num">
                <Usd value={balance.entryNotionalUsd} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

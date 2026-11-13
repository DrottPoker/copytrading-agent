"use client";

import Link from "next/link";

import { TimeAgo } from "@/components/ui/client";
import { buttonClass, EmptyState, Panel, Pnl, Side, Spinner, Usd } from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import {
  formatDuration,
  formatInteger,
  formatLeverage,
  formatPercent,
  formatPrice,
  formatSignedUsd,
  formatSize,
} from "@/lib/format";
import type { PositionRow } from "@/lib/trading/types";

export function PositionsPanel({
  busy,
  closingAll,
  closingId,
  onClose,
  onCloseAll,
  positions,
  stopsTrading,
}: {
  busy: boolean;
  closingAll: boolean;
  closingId: string | null;
  onClose: (position: PositionRow) => void;
  onCloseAll: () => void;
  positions: PositionRow[];
  /** Closing all also stops a running account, so it cannot reopen what it just closed. */
  stopsTrading: boolean;
}) {
  const unrealized = positions.reduce((total, position) => total + (position.unrealizedPnlUsd ?? 0), 0);
  return (
    <Panel
      title="Open positions"
      meta={
        positions.length > 0 ? (
          <>
            {formatInteger(positions.length)} open, unrealized{" "}
            <span className={cx(unrealized > 0 ? "text-positive" : unrealized < 0 ? "text-negative" : "")}>
              {formatSignedUsd(unrealized)}
            </span>
          </>
        ) : undefined
      }
      description="Oldest first. Each position follows its source wallet out; closing by hand overrides that."
      actions={
        positions.length > 0 ? (
          <button
            type="button"
            onClick={onCloseAll}
            disabled={busy}
            title={
              stopsTrading
                ? "Close every open position at market and stop trading this account"
                : "Close every open position at market"
            }
            className={buttonClass({ size: "xs", variant: "danger" })}
          >
            {closingAll ? <Spinner className="h-3 w-3" /> : null}
            {stopsTrading ? "Close all and stop" : "Close all"}
          </button>
        ) : undefined
      }
    >
      {positions.length === 0 ? (
        <EmptyState title="No open positions">
          Positions appear here when a copied wallet opens a trade that passes the sizing and risk checks.
        </EmptyState>
      ) : (
        <div className="max-h-[32.5rem] overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Market</th>
                <th>Source wallet</th>
                <th className="num">Size</th>
                <th className="num">Entry</th>
                <th className="num">Mark</th>
                <th className="num">Value</th>
                <th className="num">Unrealized</th>
                <th className="num" title="Realized PnL from partial closes, with funding paid or received below">
                  Realized
                </th>
                <th className="num" title="Add fills and close fills copied into this position">
                  Fills
                </th>
                <th className="num">Opened</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.id}>
                  <td>
                    <span className="font-medium">{position.coin}</span>
                    <span className="sub">
                      <Side side={position.side} suffix={formatLeverage(position.leverage, position.marginMode)} />
                    </span>
                  </td>
                  <td>
                    {position.sourceHref ? (
                      <Link href={position.sourceHref} className="block max-w-[11.25rem] truncate hover:underline" title={position.sourceWallet}>
                        {position.sourceLabel}
                      </Link>
                    ) : (
                      <span className="text-fg-3">{position.sourceLabel}</span>
                    )}
                    {position.accountType === "live" && position.isExchange ? (
                      <span className="sub">Exchange position</span>
                    ) : null}
                  </td>
                  <td className="num">{formatSize(position.size)}</td>
                  <td className="num">{formatPrice(position.entryPrice)}</td>
                  <td className="num">
                    {formatPrice(position.markPrice)}
                    <span className="sub">
                      <TimeAgo value={position.priceUpdatedAt} />
                    </span>
                  </td>
                  <td className="num">
                    <Usd value={position.notionalUsd} />
                    <span className="sub">
                      <Usd value={position.marginUsd} /> margin
                    </span>
                  </td>
                  <td className="num">
                    <Pnl value={position.unrealizedPnlUsd} />
                    <span className="sub">{formatPercent(position.unrealizedPnlPct, { signed: true })}</span>
                  </td>
                  <td className="num">
                    <Pnl value={position.realizedPnlUsd} />
                    {position.fundingUsd !== null && position.fundingUsd !== 0 ? (
                      <span className="sub">funding {formatSignedUsd(position.fundingUsd)}</span>
                    ) : null}
                  </td>
                  <td className="num text-fg-2">
                    {formatInteger(position.addFillCount)} / {formatInteger(position.closeFillCount)}
                  </td>
                  <td className="num">
                    <TimeAgo value={position.openedAt} />
                    {position.entryExecutionDelayMs !== null ? (
                      <span className="sub" title="Time from the source wallet's fill to our entry">
                        copied in {formatDuration(position.entryExecutionDelayMs)}
                      </span>
                    ) : null}
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      onClick={() => onClose(position)}
                      disabled={busy || position.closeDisabledReason !== null}
                      title={position.closeDisabledReason ?? `Close ${position.coin} at market`}
                      className={buttonClass({ size: "xs", variant: "danger" })}
                    >
                      {closingId === position.id ? <Spinner className="h-3 w-3" /> : null}
                      Close
                    </button>
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

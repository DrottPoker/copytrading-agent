"use client";

import { useCallback, useEffect, useState } from "react";

import { EmptyState, Pnl, Spinner } from "@/components/ui/primitives";
import { formatDateTime, formatInteger, formatUsd, shortId } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import type { TradingCashFlow, TradingCashFlowsResponse } from "@/types/trading";

const FLOW_LABELS: Record<TradingCashFlow["flowType"], string> = {
  deposit: "Deposit",
  internal_transfer_in: "Internal transfer in",
  internal_transfer_out: "Internal transfer out",
  send_in: "Transfer in",
  send_out: "Transfer out",
  spot_transfer_in: "Spot transfer in",
  spot_transfer_out: "Spot transfer out",
  sub_account_transfer_in: "Sub-account transfer in",
  sub_account_transfer_out: "Sub-account transfer out",
  withdrawal: "Withdrawal",
};

/** External deposits, withdrawals, and transfers that reconciliation found on the exchange. */
export function AccountTransactions({
  accountKey,
  cashFlowsVersion,
}: {
  accountKey: string;
  cashFlowsVersion: string | null;
}) {
  const [data, setData] = useState<TradingCashFlowsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const payload = await apiRequest<TradingCashFlowsResponse>(
          `/trading/accounts/${encodeURIComponent(accountKey)}/cash-flows`,
          { fallback: "Transaction history request failed", signal },
        );
        if (!signal.aborted) {
          setData(payload);
        }
      } catch (requestError) {
        if (!signal.aborted) {
          setError(errorMessage(requestError, "Transaction history is unavailable."));
        }
      } finally {
        if (!signal.aborted) {
          setLoading(false);
        }
      }
    },
    [accountKey],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [cashFlowsVersion, load]);

  if (error) {
    return <p className="px-3.5 py-4 text-sm text-negative">{error}</p>;
  }
  if (!data) {
    return (
      <div className="flex items-center gap-2 px-3.5 py-4 text-sm text-fg-3" aria-label="Loading transactions">
        <Spinner /> Loading the ledger
      </div>
    );
  }

  return (
    <div>
      <dl className="grid grid-cols-3 gap-px border-b border-line bg-line">
        <Total label="Deposits" value={<Pnl value={data.depositsUsd} />} />
        <Total label="Withdrawals" value={<Pnl value={-Number(data.withdrawalsUsd)} />} />
        <Total label="Net external flow" value={<Pnl value={data.netExternalFlowsUsd} />} />
      </dl>
      {data.items.length === 0 ? (
        <EmptyState title="No deposits or withdrawals">
          Automatic reconciliation records external cash flows here as soon as they happen.
        </EmptyState>
      ) : (
        <div className="max-h-80 overflow-auto">
          <table className="data-table" aria-label="Deposit and withdrawal history">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th className="num">Amount</th>
                <th className="num">Fee</th>
                <th>Exchange event</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((flow) => (
                <tr key={flow.id}>
                  <td className="tabular-nums text-fg-2">{formatDateTime(flow.occurredAt)}</td>
                  <td>{FLOW_LABELS[flow.flowType]}</td>
                  <td className="num">
                    <Pnl value={flow.amountUsd} />
                  </td>
                  <td className="num text-fg-2">{Number(flow.feeUsd) > 0 ? formatUsd(flow.feeUsd) : "-"}</td>
                  <td className="font-mono text-xs text-fg-3" title={flow.exchangeEventId}>
                    {shortId(flow.exchangeEventId)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {loading ? <p className="px-3.5 py-1 text-2xs text-fg-4">Refreshing</p> : null}
      <p className="sr-only">{formatInteger(data.items.length)} transactions</p>
    </div>
  );
}

function Total({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-surface px-3.5 py-2">
      <dt className="text-xs text-fg-3">{label}</dt>
      <dd className="text-sm font-medium tabular-nums">{value}</dd>
    </div>
  );
}

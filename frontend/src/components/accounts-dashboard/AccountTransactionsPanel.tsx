"use client";

import {
  ArrowDownToLine,
  ArrowUpFromLine,
  ReceiptText,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { DashboardPanel } from "@/components/DashboardSurface";
import { getPublicApiBaseUrl } from "@/lib/config";
import { formatCurrency, formatDate, formatInteger, numberValue } from "@/lib/format";
import type {
  TradingCashFlow,
  TradingCashFlowsResponse,
} from "@/types/trading";

export function AccountTransactionsPanel({
  accountKey,
  cashFlowsVersion,
}: {
  accountKey: string;
  cashFlowsVersion: string | null;
}) {
  const [transactions, setTransactions] =
    useState<TradingCashFlowsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTransactions = useCallback(
    async (signal?: AbortSignal) => {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/trading/accounts/${encodeURIComponent(
            accountKey,
          )}/cash-flows`,
          {
            cache: "no-store",
            signal,
          },
        );
        if (!response.ok) {
          throw new Error(`Transaction history request failed with ${response.status}.`);
        }
        const payload = (await response.json()) as TradingCashFlowsResponse;
        if (!signal?.aborted) {
          setTransactions(payload);
        }
      } catch (requestError) {
        if (signal?.aborted) {
          return;
        }
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Transaction history is unavailable.",
        );
      } finally {
        if (!signal?.aborted) {
          setIsLoading(false);
        }
      }
    },
    [accountKey],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadTransactions(controller.signal);
    return () => controller.abort();
  }, [cashFlowsVersion, loadTransactions]);

  const meta = isLoading
    ? "Loading ledger"
    : error
      ? "Ledger unavailable"
      : `${formatInteger(transactions?.items.length ?? 0)} transactions`;

  return (
    <DashboardPanel
      action={
        <button
          type="button"
          aria-label="Refresh transactions"
          className="ui-icon-button h-7 w-7 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isLoading}
          onClick={() => void loadTransactions()}
          title="Refresh transactions"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
        </button>
      }
      bodyClassName="p-3"
      icon={ReceiptText}
      meta={meta}
      title="Transactions"
    >
      {error ? (
        <div className="rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-xs font-medium text-danger">
          {error}
        </div>
      ) : transactions ? (
        <>
          <TransactionTotals transactions={transactions} />
          <TransactionRows transactions={transactions.items} />
        </>
      ) : (
        <TransactionLoadingState />
      )}
    </DashboardPanel>
  );
}

function TransactionTotals({
  transactions,
}: {
  transactions: TradingCashFlowsResponse;
}) {
  const netExternalFlows = numberValue(transactions.netExternalFlowsUsd);
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-2">
      <TransactionTotal
        label="Deposits"
        tone="positive"
        value={formatCurrency(transactions.depositsUsd)}
      />
      <TransactionTotal
        label="Withdrawals"
        tone="danger"
        value={formatCurrency(-numberValue(transactions.withdrawalsUsd))}
      />
      <TransactionTotal
        label="Net"
        tone={netExternalFlows >= 0 ? "positive" : "danger"}
        value={formatCurrency(netExternalFlows)}
      />
    </div>
  );
}

function TransactionTotal({
  label,
  tone,
  value,
}: {
  label: string;
  tone: "danger" | "positive";
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-subtle px-2 py-1.5">
      <p className="truncate text-[9px] font-semibold uppercase tracking-[0.04em] text-muted">
        {label}
      </p>
      <p
        className={`mt-0.5 truncate font-mono text-[11px] font-semibold ${
          tone === "positive" ? "text-positive" : "text-danger"
        }`}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}

function TransactionRows({
  transactions,
}: {
  transactions: TradingCashFlow[];
}) {
  if (transactions.length === 0) {
    return (
      <div className="mt-3 rounded-md border border-dashed border-line py-6 text-center text-xs text-muted">
        No deposits or withdrawals have been recorded.
      </div>
    );
  }

  return (
    <div
      aria-label="Deposit and withdrawal history"
      className="mt-3 max-h-64 divide-y divide-line overflow-y-auto pr-1"
    >
      {transactions.map((transaction) => (
        <TransactionRow key={transaction.id} transaction={transaction} />
      ))}
    </div>
  );
}

function TransactionRow({ transaction }: { transaction: TradingCashFlow }) {
  const isDeposit = transaction.flowType === "deposit";
  const Icon = isDeposit ? ArrowDownToLine : ArrowUpFromLine;
  const feeUsd = numberValue(transaction.feeUsd);
  return (
    <div className="flex items-center gap-2.5 py-2">
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
          isDeposit
            ? "bg-positive-soft text-positive"
            : "bg-danger-soft text-danger"
        }`}
      >
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold text-ink">
            {isDeposit ? "Deposit" : "Withdrawal"}
          </p>
          <p
            className={`shrink-0 font-mono text-xs font-semibold ${
              isDeposit ? "text-positive" : "text-danger"
            }`}
          >
            {formatCurrency(transaction.amountUsd)}
          </p>
        </div>
        <div className="mt-0.5 flex min-w-0 items-center justify-between gap-2 text-[10px] text-muted">
          <span>{formatDate(transaction.occurredAt)}</span>
          <span
            className="truncate font-mono"
            title={transaction.exchangeEventId}
          >
            {shortTransactionId(transaction.exchangeEventId)}
            {feeUsd > 0 ? ` | fee ${formatCurrency(feeUsd)}` : ""}
          </span>
        </div>
      </div>
    </div>
  );
}

function TransactionLoadingState() {
  return (
    <div className="mt-1 grid gap-2" aria-label="Loading transactions">
      {[0, 1, 2].map((index) => (
        <div
          key={index}
          className="h-11 animate-pulse rounded-md border border-line bg-subtle"
        />
      ))}
    </div>
  );
}

function shortTransactionId(value: string) {
  if (value.length <= 16) {
    return value;
  }
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

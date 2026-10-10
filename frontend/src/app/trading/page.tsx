import type { Metadata } from "next";

import { TradingWorkspace } from "@/components/trading/TradingWorkspace";
import { getPaperTradingSummary, getTradingAccounts } from "@/lib/api";

export const metadata: Metadata = { title: "Trading" };
export const dynamic = "force-dynamic";

export default async function TradingPage({
  searchParams,
}: {
  searchParams?: Promise<{ account?: string | string[]; create?: string | string[] }>;
}) {
  const params = await searchParams;
  const [summary, tradingAccounts] = await Promise.all([
    getPaperTradingSummary({ closedTradeLimit: 250, includeMarketPrices: false, recentFillLimit: 250 }),
    getTradingAccounts(),
  ]);
  return (
    <TradingWorkspace
      initialAccountKey={firstParam(params?.account)}
      initialCreate={firstParam(params?.create) === "paper"}
      initialSummary={summary}
      initialTradingAccounts={tradingAccounts}
    />
  );
}

function firstParam(value: string | string[] | undefined) {
  const resolved = Array.isArray(value) ? value[0] : value;
  return resolved?.trim() || null;
}

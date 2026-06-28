import { TradingDashboard } from "@/components/TradingDashboard";
import { getPaperTradingSummary, getTradingAccounts } from "@/lib/api";

export default async function TradingPage() {
  const [summary, tradingAccounts] = await Promise.all([
    getPaperTradingSummary({ includeMarketPrices: false }),
    getTradingAccounts(),
  ]);
  return <TradingDashboard initialSummary={summary} initialTradingAccounts={tradingAccounts} />;
}

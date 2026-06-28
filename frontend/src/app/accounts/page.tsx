import { AccountsDashboard } from "@/components/AccountsDashboard";
import { getPaperTradingSummary, getTradingAccounts } from "@/lib/api";

export default async function AccountsPage() {
  const [summary, tradingAccounts] = await Promise.all([
    getPaperTradingSummary({
      closedTradeLimit: 250,
      includeMarketPrices: false,
      recentFillLimit: 250,
    }),
    getTradingAccounts(),
  ]);
  return <AccountsDashboard initialSummary={summary} initialTradingAccounts={tradingAccounts} />;
}

import { AccountsDashboard } from "@/components/AccountsDashboard";
import { getPaperTradingSummary } from "@/lib/api";

export default async function AccountsPage() {
  const summary = await getPaperTradingSummary({
    closedTradeLimit: 250,
    recentFillLimit: 250,
  });
  return <AccountsDashboard initialSummary={summary} />;
}

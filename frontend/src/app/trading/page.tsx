import { TradingDashboard } from "@/components/TradingDashboard";
import { getPaperTradingSummary } from "@/lib/api";

export default async function TradingPage() {
  const summary = await getPaperTradingSummary();
  return <TradingDashboard initialSummary={summary} />;
}

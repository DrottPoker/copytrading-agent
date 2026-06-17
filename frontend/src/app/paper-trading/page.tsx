import { PaperTradingDashboard } from "@/components/PaperTradingDashboard";
import { getPaperTradingSummary } from "@/lib/api";

export default async function PaperTradingPage() {
  const summary = await getPaperTradingSummary();
  return <PaperTradingDashboard initialSummary={summary} />;
}

"use client";

import { BarChart3, X } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type {
  WalletScore,
  WalletScoreComponentDetail,
  WalletScoreDetail,
  WalletScoreDetailItem,
} from "@/types/wallet";

type ScoreDetailsModalProps = {
  score: WalletScore;
  scoreDetail: WalletScoreDetail | null;
};

export function ScoreDetailsModal({ score, scoreDetail }: ScoreDetailsModalProps) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const componentDetails = scoreDetail?.componentDetails ?? [];
  const canOpen = componentDetails.length > 0;

  useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        disabled={!canOpen}
        onClick={() => setOpen(true)}
        className="ui-button-secondary h-7 gap-1.5 px-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
      >
        <BarChart3 className="h-4 w-4" aria-hidden="true" />
        Detailed scoring
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/60 px-3 py-6 backdrop-blur-sm sm:px-6"
          role="presentation"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="mx-auto flex min-h-full w-full max-w-5xl items-center"
          >
            <div
              className="w-full overflow-hidden rounded-xl border border-line bg-panel shadow-raised"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-4 sm:px-5">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase text-muted">
                    Wallet scoring
                  </p>
                  <h2 id={titleId} className="mt-1 text-xl font-semibold">
                    Detailed scoring
                  </h2>
                  <p className="mt-1 break-all text-sm text-muted">
                    {score.walletAddress}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="ui-icon-button rounded-full"
                  aria-label="Close detailed scoring"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>

              <div className="max-h-[calc(100vh-10rem)] overflow-y-auto">
                <div className="grid gap-0 divide-y divide-line border-b border-line md:grid-cols-5 md:divide-x md:divide-y-0">
                  <ScoreSummaryMetric
                    label="Final score"
                    value={formatScore(score.score)}
                    tone={scoreTone(score.score)}
                    detail="Stored rank score"
                  />
                  <ScoreSummaryMetric
                    label="Gross score"
                    value={formatScore(scoreDetail?.grossScore ?? score.score)}
                    tone={scoreTone(scoreDetail?.grossScore ?? score.score)}
                    detail="Weighted components"
                  />
                  <ScoreSummaryMetric
                    label="Penalty"
                    value={formatSignedScore(scoreDetail?.penaltyScore ?? score.penaltyScore, "subtract")}
                    tone={numberValue(scoreDetail?.penaltyScore ?? score.penaltyScore) > 0 ? "danger" : "positive"}
                    detail="Subtracted after weights"
                  />
                  <ScoreSummaryMetric
                    label="Live cap"
                    value={scoreDetail?.liveRiskScoreCap ? formatScore(scoreDetail.liveRiskScoreCap) : "None"}
                    tone={scoreDetail?.liveRiskScoreCap ? "danger" : "neutral"}
                    detail="Current drawdown cap"
                  />
                  <ScoreSummaryMetric
                    label="Sample cap"
                    value={scoreDetail?.sampleCap ? formatScore(scoreDetail.sampleCap) : "None"}
                    tone={scoreDetail?.sampleCap ? "warning" : "neutral"}
                    detail="Applied when trades are below minimum"
                  />
                </div>

                <div className="px-4 py-4 sm:px-5">
                  <div className="grid gap-4">
                    {componentDetails.map((component) => (
                      <ScoreDetailComponent key={component.key} component={component} />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function ScoreSummaryMetric({
  detail,
  label,
  tone,
  value,
}: {
  detail: string;
  label: string;
  tone: "danger" | "neutral" | "positive" | "warning";
  value: string;
}) {
  const toneClass =
    tone === "positive"
      ? "text-positive"
      : tone === "warning"
        ? "text-warning"
        : tone === "danger"
          ? "text-danger"
          : "text-ink";

  return (
    <div className="p-4">
      <p className="text-xs font-medium uppercase text-muted">{label}</p>
      <p className={`mt-2 text-2xl font-semibold ${toneClass}`}>{value}</p>
      <p className="mt-1 text-sm text-muted">{detail}</p>
    </div>
  );
}

function ScoreDetailComponent({ component }: { component: WalletScoreComponentDetail }) {
  const isPenalty = component.key === "penalty";
  const progress = Math.max(0, Math.min(100, numberValue(component.score)));
  const toneClass = isPenalty
    ? progress >= 35
      ? "bg-danger"
      : progress > 0
        ? "bg-warning"
        : "bg-positive"
    : progress >= 70
      ? "bg-positive"
      : progress >= 45
        ? "bg-warning"
        : "bg-danger";

  return (
    <section className="overflow-hidden rounded-lg border border-line">
      <div className="grid gap-4 border-b border-line bg-subtle px-4 py-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-semibold">{component.label}</h3>
            {component.weight ? (
              <span className="rounded-md border border-line bg-white px-2 py-0.5 text-xs font-medium text-muted">
                Weight {formatPercent(component.weight)}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted">{component.detail}</p>
        </div>
        <div className="grid gap-1 text-sm lg:min-w-[190px] lg:text-right">
          <p className="font-semibold">
            {isPenalty ? "Penalty" : "Score"}{" "}
            <span className={scoreTextClass(component.score, isPenalty)}>
              {isPenalty ? formatPenaltyScore(component.score) : formatScore(component.score)}
            </span>
          </p>
          <p className="text-muted">
            Weighted {component.weightedScore ? formatScore(component.weightedScore) : "-"}
          </p>
        </div>
      </div>
      <div className="h-1.5 bg-line">
        <div className={`h-full ${toneClass}`} style={{ width: `${progress}%` }} />
      </div>
      <div className="overflow-x-auto">
        <table className="ui-table min-w-[760px] text-sm">
          <thead className="ui-table-head">
            <tr>
              <th scope="col" className="px-3 py-2.5 font-semibold">Input</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Value</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Score</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Weight</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">Contribution</th>
              <th scope="col" className="px-3 py-2.5 font-semibold">How it is used</th>
            </tr>
          </thead>
          <tbody>
            {component.items.map((item) => (
              <ScoreDetailItemRow key={item.key} item={item} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ScoreDetailItemRow({ item }: { item: WalletScoreDetailItem }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-3 py-2.5 font-medium">{item.label}</td>
      <td className="px-3 py-2.5 font-mono">{formatDetailValue(item.value, item.valueKind)}</td>
      <td className="px-3 py-2.5 font-mono">
        {item.score ? formatScore(item.score) : "-"}
      </td>
      <td className="px-3 py-2.5 font-mono">{item.weight ? formatPercent(item.weight) : "-"}</td>
      <td
        className={`px-4 py-3 font-mono font-semibold ${scoreTextClass(
          item.contribution,
          item.effect === "subtract",
        )}`}
      >
        {formatSignedScore(item.contribution, item.effect)}
      </td>
      <td className="max-w-[320px] px-3 py-2.5 text-muted">{item.detail}</td>
    </tr>
  );
}

function formatDetailValue(value: string | null, kind: string) {
  if (value === null) {
    return "-";
  }

  if (kind === "currency") {
    return formatCurrency(value);
  }
  if (kind === "days") {
    return `${formatDecimal(value, 1)} days`;
  }
  if (kind === "integer") {
    return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 }).format(numberValue(value));
  }
  if (kind === "penalty") {
    return formatPenaltyScore(value);
  }
  if (kind === "percent") {
    return formatPercent(value);
  }
  if (kind === "score") {
    return formatScore(value);
  }
  return formatDecimal(value, 4);
}

function formatCurrency(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    currency: "USD",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(numberValue(value));
}

function formatPercent(value: string | number | null) {
  if (value === null) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 1,
    style: "percent",
  }).format(numberValue(value));
}

function formatDecimal(value: string | number, maximumFractionDigits: number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits }).format(numberValue(value));
}

function formatScore(value: string | number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(numberValue(value));
}

function formatPenaltyScore(value: string | number) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(numberValue(value));
}

function formatSignedScore(value: string | number | null, effect: string) {
  if (value === null) {
    return "-";
  }
  const prefix = effect === "subtract" && numberValue(value) > 0 ? "-" : "";
  return `${prefix}${formatPenaltyScore(value)}`;
}

function scoreTone(value: string | number): "danger" | "neutral" | "positive" | "warning" {
  const scoreValue = numberValue(value);
  if (scoreValue >= 70) {
    return "positive";
  }
  if (scoreValue >= 45) {
    return "warning";
  }
  return "danger";
}

function scoreTextClass(value: string | number | null, inverted = false) {
  if (value === null) {
    return "text-muted";
  }

  const scoreValue = numberValue(value);
  if (inverted) {
    if (scoreValue >= 35) {
      return "text-danger";
    }
    if (scoreValue > 0) {
      return "text-warning";
    }
    return "text-positive";
  }

  if (scoreValue >= 70) {
    return "text-positive";
  }
  if (scoreValue >= 45) {
    return "text-warning";
  }
  return "text-danger";
}

function numberValue(value: string | number) {
  return typeof value === "number" ? value : Number(value);
}

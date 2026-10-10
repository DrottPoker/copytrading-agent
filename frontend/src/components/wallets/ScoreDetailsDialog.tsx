"use client";

import { useState } from "react";

import { Dialog } from "@/components/ui/client";
import { buttonClass, Meter, scoreTone } from "@/components/ui/primitives";
import { toneText, type Tone } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import { decimal, formatInteger, formatNumber, formatPercent, formatScore, formatUsd } from "@/lib/format";
import type { WalletScore, WalletScoreDetail, WalletScoreDetailItem } from "@/types/wallet";

/** Every scoring input, its value, its weight, and how much it adds to the final score. */
export function ScoreDetailsDialog({
  score,
  scoreDetail,
}: {
  score: WalletScore;
  scoreDetail: WalletScoreDetail | null;
}) {
  const [open, setOpen] = useState(false);
  const components = scoreDetail?.componentDetails ?? [];
  return (
    <>
      <button
        type="button"
        className={buttonClass({ size: "xs", variant: "ghost" })}
        disabled={components.length === 0}
        onClick={() => setOpen(true)}
      >
        Show every input
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        size="xl"
        title="How this score is calculated"
        description={<span className="font-mono">{score.walletAddress}</span>}
      >
        <div className="overflow-hidden rounded-md border border-line">
          <dl className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-5">
            <Summary label="Final score" tone={scoreTone(score.score)} value={formatScore(score.score)} detail="Stored rank score" />
            <Summary
              label="Weighted components"
              tone={scoreTone(scoreDetail?.grossScore ?? score.score)}
              value={formatScore(scoreDetail?.grossScore ?? score.score)}
              detail="Before penalties and caps"
            />
            <Summary
              label="Penalty"
              tone={decimal(scoreDetail?.penaltyScore ?? score.penaltyScore) > 0 ? "negative" : "positive"}
              value={`-${formatScore(scoreDetail?.penaltyScore ?? score.penaltyScore)}`}
              detail="Subtracted after weighting"
            />
            <Summary
              label="Drawdown cap"
              tone={scoreDetail?.liveRiskScoreCap ? "negative" : "neutral"}
              value={scoreDetail?.liveRiskScoreCap ? formatScore(scoreDetail.liveRiskScoreCap) : "None"}
              detail="Applied while the wallet is in a deep drawdown"
            />
            <Summary
              label="Sample cap"
              tone={scoreDetail?.sampleCap ? "warning" : "neutral"}
              value={scoreDetail?.sampleCap ? formatScore(scoreDetail.sampleCap) : "None"}
              detail={`Applied below ${formatInteger(scoreDetail?.minTrades)} closed trades`}
            />
          </dl>
        </div>

        <div className="mt-4 grid gap-4">
          {components.map((component) => {
            const isPenalty = component.key === "penalty";
            return (
              <section key={component.key} className="overflow-hidden rounded-md border border-line">
                <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface-2 px-3 py-2">
                  <h3 className="text-sm font-semibold">{component.label}</h3>
                  {component.weight ? (
                    <span className="text-xs text-fg-3">weight {formatPercent(component.weight, { decimals: 0 })}</span>
                  ) : null}
                  <span className="ml-auto flex items-center gap-2 text-sm">
                    <span className={cx("font-semibold tabular-nums", toneText[isPenalty ? (decimal(component.score) > 0 ? "negative" : "positive") : scoreTone(component.score)])}>
                      {isPenalty ? `-${formatScore(component.score)}` : formatScore(component.score)}
                    </span>
                    {component.weightedScore ? (
                      <span className="text-xs text-fg-3">adds {formatScore(component.weightedScore)}</span>
                    ) : null}
                  </span>
                  <p className="basis-full text-xs text-fg-3">{component.detail}</p>
                </header>
                {!isPenalty ? (
                  <Meter value={decimal(component.score) / 100} tone={scoreTone(component.score)} className="rounded-none" />
                ) : null}
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Input</th>
                        <th className="num">Value</th>
                        <th className="num">Score</th>
                        <th className="num">Weight</th>
                        <th className="num">Contribution</th>
                        <th>How it is used</th>
                      </tr>
                    </thead>
                    <tbody>
                      {component.items.map((item) => (
                        <tr key={item.key}>
                          <td className="font-medium">{item.label}</td>
                          <td className="num">{formatDetailValue(item)}</td>
                          <td className="num">{item.score ? formatScore(item.score) : "-"}</td>
                          <td className="num text-fg-2">{item.weight ? formatPercent(item.weight, { decimals: 0 }) : "-"}</td>
                          <td
                            className={cx(
                              "num font-medium",
                              item.effect === "subtract" && decimal(item.contribution) > 0 ? "text-negative" : "text-fg",
                            )}
                          >
                            {item.contribution === null
                              ? "-"
                              : `${item.effect === "subtract" && decimal(item.contribution) > 0 ? "-" : ""}${formatNumber(item.contribution, 2)}`}
                          </td>
                          <td className="wrap min-w-[17.5rem] text-xs text-fg-3">{item.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            );
          })}
        </div>
      </Dialog>
    </>
  );
}

function Summary({
  detail,
  label,
  tone,
  value,
}: {
  detail: string;
  label: string;
  tone: Tone;
  value: string;
}) {
  return (
    <div className="border-b border-r border-line px-3 py-2">
      <dt className="text-xs text-fg-3">{label}</dt>
      <dd className={cx("text-lg font-semibold tabular-nums", tone === "neutral" ? "text-fg" : toneText[tone])}>{value}</dd>
      <dd className="text-2xs text-fg-3">{detail}</dd>
    </div>
  );
}

function formatDetailValue(item: WalletScoreDetailItem) {
  if (item.value === null) {
    return "-";
  }
  switch (item.valueKind) {
    case "currency":
      return formatUsd(item.value);
    case "days":
      return `${formatNumber(item.value, 1)} days`;
    case "integer":
      return formatInteger(item.value);
    case "percent":
      return formatPercent(item.value);
    case "penalty":
    case "score":
      return formatScore(item.value);
    default:
      return formatNumber(item.value, 4);
  }
}

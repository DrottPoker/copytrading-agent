"""Run the wallet score backtest against the configured database.

Usage:
    python -m app.cli.score_backtest --horizon-days 14 --step-days 7 --top 5
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.services.score_backtest_service import (
    BacktestConfig,
    BacktestReport,
    run_score_backtest,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether the wallet score predicted later wallet performance.",
    )
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Defaults to the copied source count.",
    )
    parser.add_argument("--samples", type=int, default=1000, help="Random selections per cutoff.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--source",
        choices=("auto", "snapshots", "recompute"),
        default="auto",
        help="Stored daily snapshots, a recompute from trades, or snapshots when present.",
    )
    parser.add_argument("--json", type=Path, default=None, help="Also write the report as JSON.")
    return parser.parse_args(argv)


def percent(value: Decimal | None) -> str:
    return f"{value:.2%}" if value is not None else "n/a"


def number(value: Decimal | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def format_report(report: BacktestReport) -> str:
    config = report.config
    lines = [
        f"Score backtest: top {config.top_k}, horizon {config.horizon_days}d, "
        f"step {config.step_days}d, source {config.source}",
    ]
    if not report.cutoffs:
        lines.append(
            "No cutoff had a full scoring window and horizon of data. Let the workers "
            "collect history and daily score snapshots, then run again."
        )
        return "\n".join(lines)
    lines.append(
        f"{'cutoff':<12}{'source':<11}{'wallets':>8}{'top':>10}{'all':>10}"
        f"{'vs random':>11}{'rank corr':>11}"
    )
    for item in report.cutoffs:
        lines.append(
            f"{item.cutoff.isoformat():<12}{item.source:<11}"
            f"{item.evaluated_wallets:>8}{percent(item.top_mean_return):>10}"
            f"{percent(item.universe_mean_return):>10}"
            f"{percent(item.random_percentile):>11}{number(item.rank_correlation):>11}"
        )
    lines.extend(
        [
            "",
            f"Mean top return:        {percent(report.mean_top_return)}",
            f"Mean all-wallet return: {percent(report.mean_universe_return)}",
            f"Mean random percentile: {percent(report.mean_random_percentile)}",
            f"Mean rank correlation:  {number(report.mean_rank_correlation)}",
            f"Cutoffs above median random selection: "
            f"{report.cutoffs_beating_median_random} of {len(report.cutoffs)}",
        ]
    )
    return "\n".join(lines)


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    config = BacktestConfig(
        horizon_days=args.horizon_days,
        step_days=args.step_days,
        top_k=args.top or settings.trading_copy_top_wallet_count,
        random_samples=args.samples,
        seed=args.seed,
        start=args.start,
        end=args.end,
        source=args.source,
    )
    async with sessionmaker() as session:
        report = await run_score_backtest(session, settings=settings, config=config)
    print(format_report(report))
    if args.json is not None:
        args.json.write_text(
            json.dumps(asdict(report), default=json_default, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()

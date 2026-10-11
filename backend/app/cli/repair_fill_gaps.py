"""Fill gaps in stored wallet fill history left by earlier imports.

Usage:
    python -m app.cli.repair_fill_gaps --dry-run
    python -m app.cli.repair_fill_gaps
    python -m app.cli.repair_fill_gaps --include-archived
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_info_budget import (
    close_shared_info_budget,
    configure_shared_info_budget,
)
from app.services.fill_gap_repair_service import (
    FillGap,
    FillGapRepair,
    fill_gap_window_start_ms,
    find_fill_gaps,
    repair_fill_gaps,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read the longest gaps in each pool wallet's stored fills again and store "
        "the missing fills.",
    )
    parser.add_argument("--min-gap-hours", type=float, default=24.0)
    parser.add_argument("--gaps-per-wallet", type=int, default=3)
    parser.add_argument("--max-pages-per-gap", type=int, default=10)
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Also repair archived wallets, so a later readmission judges complete history.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List the gaps without reading.")
    return parser.parse_args(argv)


def describe(gap: FillGap) -> str:
    start = datetime.fromtimestamp(gap.start_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
    end = datetime.fromtimestamp(gap.end_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
    archived = f", archived: {gap.archive_reason or 'yes'}" if gap.archive_reason else ""
    return f"{gap.wallet_address} {start} to {end} UTC ({gap.hours:.1f} h{archived})"


def print_repair(repair: FillGapRepair) -> None:
    status = "" if repair.complete else ", more remain: run again"
    print(f"{describe(repair.gap)}: {repair.inserted} new of {repair.fetched} read{status}")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2
    async with sessionmaker() as session:
        gaps = await find_fill_gaps(
            session,
            since_ms=fill_gap_window_start_ms(settings),
            min_gap_ms=int(args.min_gap_hours * 3_600_000),
            gaps_per_wallet=args.gaps_per_wallet,
            include_archived=args.include_archived,
        )
        print(f"{len(gaps)} gaps of at least {args.min_gap_hours:g} h found.")
        if args.dry_run:
            for gap in gaps:
                print(describe(gap))
            return 0
        # Share the request budget with the running workers.
        configure_shared_info_budget(settings)
        try:
            async with HyperliquidClient(settings) as client:
                result = await repair_fill_gaps(
                    session,
                    gaps=gaps,
                    client=client,
                    settings=settings,
                    max_pages_per_gap=args.max_pages_per_gap,
                    on_repair=print_repair,
                )
        finally:
            await close_shared_info_budget()
    print(f"Stored {result.inserted} missing fills. The next scoring run rebuilds their trades.")
    archived = sorted(
        {
            repair.gap.wallet_address
            for repair in result.repairs
            if repair.gap.archive_reason and repair.inserted > 0
        }
    )
    if archived:
        print(
            "These archived wallets had missing fills and may have been archived on incomplete "
            "history. Review them on the Wallets page and readmit the ones you want back:"
        )
        for address in archived:
            print(f"  {address}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CLI entry point for tracking Royal Caribbean Club Royale casino offers."""

from __future__ import annotations

import argparse

# Core casino imports
from royal_caribbean.core.casino import *
from royal_caribbean.core.casino import __all__ as _casino_all

# Explicit, deliberate re-exports required by tests, configuration, or execution
from royal_caribbean.config.loaders import load_config_file
from royal_caribbean.utils.logging import (
    log,
    setup_hybrid_logging,
)

from royal_caribbean.utils.notify import build_apprise


# Tight, controlled public surface contract
_public_utilities = [
    "log",
    "setup_hybrid_logging",
]

_local_all = ["main"]

# Deduplicate names while maintaining deterministic order
__all__ = sorted(list(set(
    _casino_all +
    _public_utilities +
    _local_all
)))


def main() -> None:
    """Loads config, authenticates, fetches Club Royale offers, and reports deadlines."""
    parser = argparse.ArgumentParser(
        description="Check Royal Caribbean Casino Offers"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--warn-days",
        type=int,
        default=14,
        help="Alert when an offer's reserve-by date is within this many days (default: 14)",
    )
    args = parser.parse_args()

    data = load_config_file(args.config)
    setup_hybrid_logging(data.get("logFile"))

    apobj = build_apprise(data.get("apprise") or [])
    account_info = build_account(data)
    offers = fetch_casino_offers(account_info)
    report_offers(offers, args.warn_days, apobj)


if __name__ == "__main__":
    main()

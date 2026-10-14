#!/usr/bin/env python3
"""CLI entry point for tracking Royal Caribbean Club Royale casino offers."""

from __future__ import annotations

import argparse

# Clean star imports driven strictly by submodule __all__ definitions
from royal_caribbean.models import *
from royal_caribbean.config.loaders import *
from royal_caribbean.core.casino import *
from royal_caribbean.utils.helpers import *
from royal_caribbean.utils.logging import *
from royal_caribbean.utils.notify import *

# Submodule __all__ exports for dynamic facade surface aggregation
from royal_caribbean.models import __all__ as _models_all
from royal_caribbean.config.loaders import __all__ as _loaders_all
from royal_caribbean.core.casino import __all__ as _casino_all
from royal_caribbean.utils.helpers import __all__ as _helpers_all
from royal_caribbean.utils.logging import __all__ as _logging_all
from royal_caribbean.utils.notify import __all__ as _notify_all

# Deduplicate names while maintaining deterministic order
__all__ = list(dict.fromkeys(
    _casino_all +
    _helpers_all +
    _loaders_all +
    _logging_all +
    _models_all +
    _notify_all +
    ["main"])
)


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

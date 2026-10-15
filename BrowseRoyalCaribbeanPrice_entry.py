"""BrowseRoyalCaribbeanPrice_entry.py: CLI entrypoint for browsing Royal Caribbean prices and itineraries."""

from __future__ import annotations

import argparse
import locale
import sys

# Core browsing imports
from royal_caribbean.core.browsing import *
from royal_caribbean.core.browsing import __all__ as _browsing_all

# Explicit, deliberate re-exports required by tests, configuration, or execution
from royal_caribbean.utils.helpers import get_system_currency
from royal_caribbean.utils.logging import (
    BLUE,
    GREEN,
    RESET,
    log,
    setup_hybrid_logging
)

# Tight, controlled public surface contract
_public_utilities = [
    "get_system_currency",
    "log",
    "setup_hybrid_logging"
]

_local_all = ["main"]

# Deduplicate names while maintaining deterministic order
__all__ = sorted(list(set(
    _browsing_all +
    _public_utilities +
    _local_all
)))


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Browse Royal Caribbean Price")
    parser.add_argument('-c', '--currency', type=str, default='System', help='currency (default: System Setting)')
    parser.add_argument('-s', '--ship', type=str, help='Ship')
    parser.add_argument('-d', '--saildate', type=str, help='Sail Date exactly as shown in the sailing list')
    parser.add_argument('-o', '--sortorder', choices=['asc', 'desc'], default="asc", dest="sort_order", help='Set sorting order')
    parser.add_argument('-k', '--sortkey', choices=['price', 'alpha', 'default'], default="default", dest="sort_key", help='Set value to sort on')
    parser.add_argument('-w', '--watchlistcodes', action='store_true', dest="watchlist_codes", help='Show Codes For Watchlist')
    parser.add_argument('-a', '--activitysort', choices=['date', 'alpha', 'default'], default="default", dest="activity_sort", help='Set activity sorting metric')
    parser.add_argument('-l', '--logfile', type=str, nargs='?', const='output.txt', dest="log_file", help='optional logfile')
    parsed_args = parser.parse_args(args)

    setup_hybrid_logging(parsed_args.log_file)

    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error:
        pass

    currency = parsed_args.currency
    if currency == "System":
        currency = get_system_currency()
    if parsed_args.log_file:
        log(f"Logging run to file: {parsed_args.log_file}")

    ships = get_ships_web()
    selected_index = None

    if parsed_args.ship:
        for i, ship in enumerate(ships):
            if ship['name'] == f"{parsed_args.ship} of the Seas" or ship['name'] == f"Celebrity {parsed_args.ship}":
                selected_index = i
                break
        if selected_index is None:
            log(f"I can't find that ship name {parsed_args.ship}; please try again")
            return
    else:
        if not sys.stdin.isatty():
            log("Non-interactive run: pass -s SHIP (and -d SAILDATE) to select without prompts")
            return
        log("Select Ship:")
        for i, ship in enumerate(ships):
            log(f"{BLUE}{i}{RESET}) {GREEN}{ship['name']}{RESET}")
        log(f"{BLUE}q{RESET}) - Quit")

        user_choice = input("Enter Ship Number: ").strip()
        if user_choice.lower() == 'q':
            log("Have a nice day!")
            return

        if not user_choice.isdigit():
            log("Invalid ship selection")
            return
        selected_index = int(user_choice)

    num_ships = len(ships)
    if 0 <= selected_index < num_ships:
        ship_name = ships[selected_index]['name']
        ship_code = ships[selected_index]['code']
        log(f"Getting sailings for {ship_name}")
        sailings = get_sailings_web(ship_code)

        num_sailings = len(sailings)
        sailing_index = None

        if parsed_args.saildate:
            for i, sailing in enumerate(sailings):
                if parsed_args.saildate == sailing['displayDate'].split(" ")[0]:
                    sailing_index = i
                    log(f"Getting info for {sailing['displayDate']} {sailing['description']}")
                    break
            if sailing_index is None:
                log(f"I can't find that sail date {parsed_args.saildate} for {ship_name}; please try again")
                return
        else:
            if not sys.stdin.isatty():
                log("Non-interactive run: pass -d SAILDATE to select without prompts")
                return
            log("")
            log("Select sailing:")
            for i, sailing in enumerate(sailings):
                log(f"{BLUE}{i}{RESET}) {GREEN}{sailing['displayDate']}{RESET} {sailing['description']}")
            log(f"{BLUE}q{RESET}) - Quit")

            user_choice = input("Enter Sailing Number: ").strip()
            if user_choice.lower() == 'q':
                log("Have a nice day!")
                return

            if not user_choice.isdigit():
                log("Invalid sailing selection")
                return
            sailing_index = int(user_choice)

        if 0 <= sailing_index < num_sailings:
            sailing = sailings[sailing_index]
            log("")
            log(f"Browsing for {ship_name} sailing on {sailing['displayDate']} ({sailing['description']})")
            log("")

            ports = get_sailing_details_web(ship_code, sailing['date'])
            log("")

            is_royal = "of the Seas" in ship_name
            if is_royal:
                log("Direct Link To Royal Caribbean Cruise Planner Website: ")
                link_root = "https://www.royalcaribbean.com/account/cruise-planner/category/beverage"
            else:
                log("Direct Link To Celebrity Cruise Planner Website: ")
                link_root = "https://www.celebritycruises.com/account/cruise-planner/category/drinks"

            log(f"{link_root}?bookingId=123456&shipCode={ship_code}&sailDate={sailing['date']}")
            log("")

            num_adults = 2
            num_children = 0
            get_cruise_price_from_API(currency, ship_code + sailing['voyageCode'], sailing['date'], num_adults, num_children, is_royal)
            log("")

            log("Gathering list of products.  This may take a few minutes; please be patient.")
            log("These are public prices, sale prices for you could be less")
            log("")
            print_all_products(ship_code, sailing['date'], sailing['duration'], currency, parsed_args.sort_key, parsed_args.sort_order, parsed_args.watchlist_codes, ports)

            log("")
            log("Gathering list of activities.  This may take a few minutes; please be patient.")
            activities = get_all_activities_web(ship_code, sailing['date'])
            print_all_activities(activities, parsed_args.activity_sort)

            log("")
            log("Gathering list of Main Dining Room Menus.  This may take a few minutes; please be patient.")
            mdr_names = get_MDR_locations(ship_code, sailing['date'], is_royal)
            print_MDR_menus(ship_code, sailing['date'], mdr_names, ports)
        else:
            log("Invalid sailing selection")
    else:
        log("Invalid ship selection")

    if sys.stdin.isatty():
        input("Hit ENTER to quit: ")
    log("Have a nice day!")


if __name__ == "__main__":
    main()

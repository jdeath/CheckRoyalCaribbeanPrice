from __future__ import annotations
import argparse
import base64
import hashlib
import json
import locale
import logging
import os
import platform
import re

# curl_cffi impersonates a real browser's TLS fingerprint so the cruise line's
# edge servers do not reject some IPs/systems as bots with 403 Access Denied
# (see jdeath/CheckRoyalCaribbeanPrice issue #64). Fall back to plain requests
# where it is not installed (e.g. iOS), which works fine for most people.
# Keep standard requests available alongside curl_cffi for endpoints that
# misbehave under TLS impersonation/headers on some networks (e.g. issue #88)
import requests as plain_requests
try:
    from curl_cffi import requests
    IMPERSONATE_ARGS = {"impersonate": "chrome"}
except ImportError:
    requests = plain_requests
    IMPERSONATE_ARGS = {}

import sqlite3
import sys
import tempfile
import traceback
import time
import yaml

# NotifyFormat.TEXT declares notification bodies as plain text so Apprise converts
# them per-service: HTML email renders the \n line breaks instead of collapsing
# them to one line (issue #76); plain-text services are passed through unchanged
# Apprise is optional (e.g. the iOS full install runs without it). The None
# sentinels matter: the config parser checks "Apprise is None" to warn-and-disable
# when apprise: is configured without the package - a bare "except: pass" leaves
# the names undefined and turns that check into a NameError crash (issue #85).
try:
    from apprise import Apprise, NotifyFormat
except ImportError:
    Apprise = None
    NotifyFormat = None

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse


##################################
# Global Constants & Variables
##################################
# Immutable configuration settings
USER_AGENT_WEB = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0'
APPKEY_WEB = 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm'

# API timeout / retry behavior
# Seconds before giving up on an API call so a stalled connection cannot hang the run
# forever. Override with requestTimeout in config.yaml if the API is slow for you.
REQUEST_TIMEOUT = 30

# Shorter timeout for quick auxiliary endpoints (check-in status, loyalty summary,
# sample-config download) where a long wait is not worth it
SHORT_REQUEST_TIMEOUT = 10

# How API failures are handled when a call site does not choose explicitly:
# "retry" (back off and try again), "skip" (log and move on), "exit" (stop the run)
DEFAULT_ON_FAILURE = "retry"

# Retry attempts and exponential backoff base for on_failure="retry" calls
# (sleep = RETRY_BACKOFF_BASE ** attempt seconds between attempts: 2s, 4s)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2

# Cool-down between accounts when checking more than one, to avoid hammering the API
ACCOUNT_COOLDOWN_SECONDS = 5

# Module-level constant for room type translations
STATEROOM_TYPE_MAPPING = {
    "I": "INTERIOR",
    "O": "OUTSIDE",
    "B": "BALCONY",
    "D": "DELUXE",
    "C": "CONCIERGE",
}

# Macro classifications / generic terms that cause HTTP 500 when sent as r0e / r0f
CHECKOUT_FORBIDDEN_CATEGORY_CODES = (
    set(STATEROOM_TYPE_MAPPING.keys())   |
    set(STATEROOM_TYPE_MAPPING.values()) |
    {"S", "SUITE", "OCEANVIEW", "NONE"}
)

# Type alias for duration-based lead-time rules: list of (max_nights, days_before)
DurationRules = list[Tuple[float, int]]

# Market-specific final payment lead times (days before departure)
# Flat integer for markets with uniform policies; duration-tiered lists for others.
MARKET_RULES: dict[str, Union[int, DurationRules]] = {
    # Central & Northern European markets: flat 30 days
    "DEU": 30, "DE": 30,  # Germany
    "CHE": 30, "CH": 30,  # Switzerland
    "CHS": 30,            # Switzerland - Royal's own market code (ISO is CHE)
    "NOR": 30, "NO": 30,  # Norway
    "SWE": 30, "SE": 30,  # Sweden
    "DNK": 30, "DK": 30,  # Denmark
    "FIN": 30, "FI": 30,  # Finland

    # Austria: 15 days for 1-14 nights; 120 days for 15+ nights
    "AUT": [(14, 15), (float("inf"), 120)],
    "AT":  [(14, 15), (float("inf"), 120)],

    # UK & Ireland: 56 days (8 weeks) for standard sailings, 70 days for 15+ nights
    "GBR": [(14, 56), (float("inf"), 70)],
    "GB":  [(14, 56), (float("inf"), 70)],
    "UK":  [(14, 56), (float("inf"), 70)],
    "IRL": [(14, 56), (float("inf"), 70)],
    "IE":  [(14, 56), (float("inf"), 70)],

    # Australia & New Zealand: 90 days for standard sailings, 120 days for 15+ nights
    "AUS": [(14, 90), (float("inf"), 120)],
    "AU":  [(14, 90), (float("inf"), 120)],
    "NZL": [(14, 90), (float("inf"), 120)],
    "NZ":  [(14, 90), (float("inf"), 120)],

    # Default / US / North America rules (1-4 nights: 75 days, 5-14: 90 days, 15+: 120 days)
    "USA": [(4, 75), (14, 90), (float("inf"), 120)],
    "US":  [(4, 75), (14, 90), (float("inf"), 120)],
    "CAN": [(4, 75), (14, 90), (float("inf"), 120)],
    "CA":  [(4, 75), (14, 90), (float("inf"), 120)],
}

# Process exit code contract for main() - a supervising scheduler can rely on
# these three outcomes meaning exactly this and nothing else:
#   EXIT_SUCCESS         - full success: every account was checked
#   EXIT_TOTAL_FAILURE   - fatal/total failure: an unhandled exception, or a
#                          module-level setup failure. This can happen before
#                          any pricing ran, or partway through a multi-account
#                          run after earlier accounts already succeeded and
#                          had their price-drop alerts sent - the exit code
#                          alone doesn't say how far the run got. Don't
#                          blindly auto-retry on this code (that risks
#                          re-alerting accounts that already succeeded);
#                          surface it to a human and check the log first.
#   EXIT_PARTIAL_FAILURE - the run COMPLETED, but one or more accounts were
#                          SKIPPED after a login or post-login profile-fetch
#                          failure. Data already written for the accounts
#                          that DID succeed (price points committed,
#                          price-drop alerts sent, the watchlist JSON) is
#                          real and must not be redone.
#                          A scheduler that treats this the same as exit 1
#                          and blindly retries the whole run will re-price
#                          already-priced accounts and can send duplicate
#                          price-drop notifications to real users - so this
#                          code is deliberately distinct from the fatal (1)
#                          and success (0) cases.
EXIT_SUCCESS = 0
EXIT_TOTAL_FAILURE = 1
EXIT_PARTIAL_FAILURE = 2

# ANSI color codes
RESET = '\033[0m' # Resets color to default

# Original values
RED = '\033[1;31;40m'    # Standard red text, black background, bold weight
GREEN = '\033[1;32m'     # Standard green text, default background, bold weight
YELLOW = '\033[33m'      # Standard yellow text, default background, normal weight
BLUE = '\033[94m'        # Bright blue text, default background, normal weight

# May not work on older/legacy terminals
#RED = '\033[91m'         # Bright red text, default background, normal weight
#GREEN = '\033[92m'       # Bright green text, default background, normal weight
#YELLOW = '\033[93m'      # Bright yellow text, default background, normal weight
#BLUE = '\033[94m'        # Bright blue text, default background, normal weight

# Supported by everything
#RED = '\033[1;31;40m'    # Standard red text, black background, bold weight
#GREEN = '\033[1;32;40m'  # Standard green text, black background, bold weight
#YELLOW = '\033[1;33;40m' # Standard yellow text, black background, bold weight
#BLUE = '\033[1;34;40m'   # Standard dark blue text, black background, bold weight

# Global storage of user config read from YAML
config: CruiseAppConfig = None

# Global storage of price history
history: PriceHistory = None

# Environmental overrides for terminals struggling with Unicode glyphs such as ↑ (e.g., MobaXterm)
PROBLEM_ENVS = ["MOBAEXTRACTONTHEFLY", "MOBANOACL"]
has_terminal_issues = False;

# Define global logging hooks so they are available everywhere in the script module
log = None
log_warn = None
log_err = None

##################################
# Classes (Structural and Logging)
##################################
class EasyLogger:
    """
    A simplified logging manager wrapper providing standalone function shortcuts.

    Exposes functional hooks (such as 'log', 'log_warn', 'log_err') globally across the
    script module so that less-experienced developers don't have to manage raw,
    verbose logging object initializations.  This can be extended to other logging
    categories as desired by duplicating the redirect methods below
    """
    def __init__(self, logger_instance: logging.Logger) -> None:
        self._logger = logger_instance


    def __call__(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """
        Maps log("text") directly to logger.info
        that is, define log("text") as a shorthand
        for log.info("text")
        """
        self._logger.info(message, *args, **kwargs)


    def warn(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """Redirects log_warn("text") calls to logger.warning"""
        self._logger.warning(message, *args, **kwargs)


    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """Redirects log_err("text") calls to logger.error"""
        self._logger.error(message, *args, **kwargs)


class PrintRedirector:
    """
    Intercepts and routes standard Python print() streams directly into the log engine.

    Replaces sys.stdout. When a developer executes a raw print() statement, this
    handler intercepts the text stream, strips trailing line breaks to protect against
    empty blank rows, and channels content cleanly into the active root logging handlers.

    DESIGN NOTE: This is a redirection trick. It captures standard 'print()' statements
    and silently pipes them through our logger so they write to the terminal AND the text log file
    at the same time, without changing all 'print' statements to 'logging.info'.
    """
    def __init__(self, logger_func: Any) -> None:
        self.logger_func = logger_func


    def write(self, buf: str) -> None:
        # Python's print() appends content and trailing newlines sequentially.
        # Strip trailing line breaks to avoid logging empty string rows.
        content = buf.rstrip('\r\n')
        if content:
            self.logger_func(content)


    def flush(self) -> None:
        pass  # Standard log handlers manage their own flushing mechanics


class StripAnsiFilter(logging.Filter):
    """
    Removes terminal formatting expressions before records are written to disk.

    Filters out raw ANSI terminal color declarations (like '\033[1;31;40m') from
    outgoing text lines, keeping written plaintext files entirely safe and clean
    for cross-platform file reading.
    """
    ANSI_REGEX: re.Pattern[str] = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.ANSI_REGEX.sub('', record.msg)
        return True


@dataclass
class Ship:
    """
    Represents an individual physical vessel within a cruise fleet.

    Tracks the short-form corporate identifier ('code') and the user-friendly name.
    """
    code: str
    name: str = "Unknown Ship"


    # Adding an explicit init to ensure attributes map correctly when instantiated manually
    def __init__(self, code: str, name: str = "Unknown Ship"):
        self.code = code
        self.name = name


class ShipRegistry:
    """
    In-memory dictionary cache tracking valid fleet vessel assets.

    Maintains a catalog of hull profiles. If a lookup code cannot be matched
    from server manifests, it returns a safe fallback instance to prevent
    downstream execution faults.
    """
    def __init__(self)->None:
        self.ships: dict[str, Ship] = {}


    def add_from_payload(self, payload: List[Dict[str, Any]]) -> None:
        """
        Populates the registry map by parsing raw ship arrays from corporate servers.

        Iterates through incoming server manifests, extracts the primary identification
        tokens ('shipCode' and 'name'), and caches them as structural Ship objects.
        Guarantees that subsequent UI logs can map technical codes to user-friendly vessel names.
        """
        for item in payload:
            code = item.get("shipCode")
            name = item.get("name", "Unknown Ship")
            if code:
                self.ships[code] = Ship(code=code, name=name)


    def get_ship(self, code: str) -> str:
        """
        Returns the ship if found, otherwise a new 'Unknown' ship object
        """
        # Check if the ship object exists in our registry dictionary
        ship_obj = self.ships.get(code)

        # If it exists, return its clean name.
        # Otherwise, return the raw code string
        return ship_obj.name if ship_obj else code


@dataclass
class CruiseURLParams:
    """
    Data container used to build specific consumer booking pricing requests.

    Assembles voyage, demographic, and state residency identifiers. Includes corporate
    validation logic to strip 'All-Included' fare upgrades from Royal Caribbean paths,
    as that option applies exclusively to Celebrity Cruises.
    """
    package_code: str = ""
    sail_date: str = ""
    ship_code: str = ""
    cabin_class_string: str = ""
    stateroom_type_name: str = ""
    stateroom_subtype: str = ""
    stateroom_category_code: str = ""
    currency_code: str = "USD"
    booking_office_country_code: str = "USA"
    is_royal: bool = True
    username: Optional[str] = None
    coupon_code: Optional[str] = None
    number_of_adults: str = "2"
    number_of_children: str = "0"
    loyalty_number: Optional[str] = None
    state: Optional[str] = None
    senior: bool = False
    fire: bool = False
    police: bool = False
    military: bool = False
    dp340: bool = False

    # Pricing addon flags required by apply_overrides and parse_provided_URL
    all_included: bool = False
    refundable: bool = False
    travel_insurance: bool = False
    prepaid_grats: bool = False

    def apply_discount_profile(self, profile: DiscountProfile) -> None:
        """Safely maps profile values without dropping asymmetric keys."""
        self.loyalty_number = profile.loyalty_number
        self.state = profile.state
        # Keep these boolean like the dataclass declares: a "n" STRING here is
        # truthy, which would silently invert 'y' if params.police else 'n' checks
        self.senior = bool(profile.senior)
        self.military = bool(profile.military)
        self.police = bool(profile.police)
        self.fire = bool(profile.fire)
        self.dp340 = profile.dp340


    @property
    def api_brand(self) -> str:
        # CruiseURLParams has no is_celebrity attribute - derive from is_royal
        return "royal" if self.is_royal else "celebrity"


    @property
    def url_brand(self) -> str:
        """
        Dynamically provides the domain segment for room pricing requests.
        """
        return "royalcaribbean" if self.is_royal else "celebritycruises"


    def apply_overrides(self, overrides: Optional[Dict[str, Any]]) -> None:
        """
        Consumes target 'paidPriceStruct' configurations from the YAML file to modify a pricing query.

        Allows a user to temporarily substitute booking details (such as forcing a specific
        subcategory, updating loyalty numbers, or testing senior rates) without changing
        the source URL.

        Gotcha: Enforces strict corporate structural rules—if 'allIncluded' is selected
        but the target brand is Royal Caribbean, this method automatically strips the upgrade
        since that promotional structure applies exclusively to Celebrity Cruises.
        """
        if not overrides:
            return

        # Direct attribute mapping based on get_cruise_price
        self.all_included = overrides.get("allInUpgrade", self.all_included)
        self.prepaid_grats = overrides.get("gratuities", self.prepaid_grats)
        self.travel_insurance = overrides.get("tripInsurance", self.travel_insurance)
        self.refundable = overrides.get("refundable", self.refundable)
        self.coupon_code = overrides.get("couponCode", self.coupon_code)
        self.stateroom_category_code = overrides.get("categoryOverride", self.stateroom_category_code)
        self.stateroom_subtype = overrides.get("subcategoryOverride", self.stateroom_subtype)
        self.senior = overrides.get("senior", self.senior)
        self.military = overrides.get("military", self.military)
        self.police = overrides.get("police", self.police)
        self.fire = overrides.get("fire", self.fire)
        self.loyalty_number = overrides.get("loyaltyNumber", self.loyalty_number)
        self.state = overrides.get("state", self.state)

        # Mirror categoryOverride to subtype if subcategoryOverride was omitted
        if overrides.get("categoryOverride") and not overrides.get("subcategoryOverride"):
            self.stateroom_subtype = self.stateroom_category_code

        # Enforce corporate structural constraints natively
        if self.all_included and self.is_royal:
            log("Royal Does Not Have All In Fare\nRemoving All In Fare. Check Documentation")
            self.all_included = False


@dataclass
class DiscountProfile:
    """
    Demographic profile containing localized and corporate discount indicators.

    Feeds pricing engines with targeted parameters like regional residency, age
    milestones, military backgrounds, or elite loyalty brackets (such as the 'dp340'
    single-supplement tier modification).
    """
    loyalty_number: str
    state: Optional[str]
    senior: bool
    military: bool
    fire: bool
    police: bool
    dp340: bool  # Diamond Plus with 340+ points (free single supplement tier)


@dataclass
class WatchItemContext:
    """
    Transactional payload mapping an in-flight validation task to a passenger.

    Binds items undergoing pricing review (like specific beverage package codes)
    to specific cabin assignments, original purchase historical records, and
    authorized pricing scopes.
    """
    prefix: str
    product: str
    passenger_ID: Optional[str]
    passenger_name: str
    room: Optional[str]
    paid_price: float
    guest_age_string: str
    sales_unit: Optional[Any] = None
    for_watch: bool = True
    order_code: str = "WATCH-LIST"
    order_date: str = "Watch List"
    owner: bool = True
    reservations: List[str] = field(default_factory=list)
    reservation_id: str = ""


@dataclass(frozen=True)
class PriceAlertExclusion:
    """Mute one product's price notifications within a reservation."""
    reservation: str
    prefix: str
    product: str
    guest: Optional[str] = None

    def matches(self, reservation: Any, ctx: WatchItemContext) -> bool:
        return (self.reservation == str(reservation)
                and self.prefix == str(ctx.prefix)
                and self.product == str(ctx.product)
                and (self.guest is None or self.guest == str(ctx.passenger_ID)))


@dataclass
class APIAccess:
    """
    Authentication session container holding current digital passport tokens.

    Maintains the server-assigned user 'id', OAuth bearer token strings, and the
    persistent network connection session pool context.
    """
    token: str
    id: str
    session: requests.Session


@dataclass
class AccountInfo:
    """
    User credential profile used to initialize authenticated client sessions.

    Holds user login credentials, default demographic flags, targeted brand settings,
    and references to the active authenticated session tracking context.
    """
    username: str
    password: str
    state: Optional[str] = None
    senior: bool = False
    military: bool = False
    fire: bool = False
    police: bool = False
    cruise_line: Optional[str] = "royalcaribbean"

    # Defaulting access to None allows us to load the YAML configuration safely
    # before the script logs in and populates it.
    access: Optional[APIAccess] = None
    found_items: Set[str] = field(default_factory=set)

    # Populated in main() right after get_profile(); history-layer snapshot
    # fields only (C&A/Captain's Club tier label + individual points) - not
    # used anywhere in alert/discount logic.
    loyalty_tier: Optional[str] = None
    loyalty_points: Optional[int] = None

    # Live Runtime Object (excluded from the YAML mapping, like config.apobj).
    # Per-account Apprise object; falls back to the global config.apobj via
    # notifier_for() when this account has no apprise: list of its own.
    apobj: Optional[Apprise] = None


    @property
    def is_royal(self) -> bool:
        return self.cruise_line.lower() in ("royal", "royalcaribbean", "royal caribbean", "r")


    @property
    def is_celebrity(self) -> bool:
        # Put in safety checking for celebrity (for example, "carnival" would be read as celebrity
        # if we just check for strings that start with 'c')
        return self.cruise_line.lower() in ("celebrity", "celebritycruises", "celebrity cruises", "c")


    @property
    def api_brand(self) -> str:
        return "celebrity" if self.is_celebrity else "royal"


    @property
    def url_brand(self) -> str:
        """Used for RSC portals, OAuth login, and web redirect links."""
        return "celebritycruises" if self.is_celebrity else "royalcaribbean"


    @property
    def friendly_name(self) -> str:
        """Returns a presentation-ready string of the target cruise line brand."""
        return "Celebrity Cruises" if self.is_celebrity else "Royal Caribbean"


@dataclass
class WatchListItem:
    """
    User-configured catalog item monitored for price fluctuations.

    Maps tracking targets defined in 'config.yaml' (beverage packages, excursions)
    against baseline targets, targeting specific booking reference IDs if restricted.
    """
    name: str
    prefix: str
    product: str
    price: float
    enabled: bool = True
    guest_age_string: str = "adult"
    reservations: Optional[List[str]] = field(default_factory=list)


@dataclass
class ProspectiveCruise:
    """
    An unbooked, prospective voyage monitored for price drops.

    Pairs a web browser URL with the baseline price targets configured in
    the local environment YAML manifest.
    """
    cruise_URL: str
    paid_price: float
    loyalty_number: Optional[str] = None
    notification_mode: str = "price"


@dataclass
class CruiseAppConfig:
    """
    Master configuration repository storing all global application run states.

    Tracks terminal formats, output log paths, notifications, target accounts,
    and watchlist arrays. Includes a safe JSON serializer method to easily print
    the configuration for debugging.
    """
    # Global Settings
    date_display_format: Optional[str] = "%x"
    request_timeout: int = REQUEST_TIMEOUT
    log_file: Optional[str] = None
    history_db: Optional[str] = None
    check_for_upgrades: bool = False
    upgrade_alert_below: Optional[float] = None
    upgrade_reservations: List[str] = field(default_factory=list)
    upgrade_sister_categories: bool = True
    cabin_availability_state_file: str = "data/cabin-availability.json"
    availability: Optional["AvailabilitySettings"] = None
    output_watch_as_json: bool = False
    output_json_watch_file: Optional[str] = "output-json-watch.txt"
    apprise_urls: List[str] = field(default_factory=list)
    notify_on_error: bool = False
    apprise_test: Optional[bool] = None

    display_cruise_prices: bool = True
    minimum_saving_alert: Optional[float] = None
    show_promos: bool = False

    # Complex Objects
    accounts: List[AccountInfo] = field(default_factory=list)
    watch_list: List[WatchListItem] = field(default_factory=list)
    ignored_price_alerts: List[PriceAlertExclusion] = field(default_factory=list)
    prospective_cruises: List[ProspectiveCruise] = field(default_factory=list)

    # Mapping Dictionaries
    reservation_prices: Dict[str, float] = field(default_factory=dict)
    reservation_names: Dict[str, str] = field(default_factory=dict)
    # Reservations the user has verified as settled (agency/TA bookings often
    # expose no payment state at all, so the API can't confirm it)
    paid_reservations: Set[str] = field(default_factory=set)

    # Live Runtime Objects (Excluded from the initial YAML mapping)
    apobj: Optional[Apprise] = None


    def __str__(self):
        """Automatically pretty-prints the configuration when called via print()."""
        try:
            # default=str handles any leftover non-serializable objects like APIAccess or apobj
            return json.dumps(asdict(self), indent=4, default=str)
        except Exception as e:
            return f"<CruiseAppConfig Error formatting: {e}>"


    def format_date(self, date_str: str) -> str:
        """Transforms a raw YYYYMMDD string timestamp into the user's preferred layout."""
        if not date_str:
            return ""

        # Strip potential legacy hyphens if they leak from web parameters
        clean_str = date_str.replace("-", "").replace("/", "")
        try:
            return datetime.strptime(clean_str, "%Y%m%d").strftime(self.date_display_format)
        except ValueError:
            return str(date_str)   # malformed API date: show it raw, don't crash the run


class PriceHistory:
    """
    Opt-in, append-only SQLite price-history sink (config: historyDb).

    Every public method is a silent no-op when db_path is falsy, so the ~10
    call sites throughout this script never need an `if history:`
    guard - the object itself absorbs "feature off" and touches the
    filesystem not at all in that case. When enabled, each observation is
    committed immediately (one connection per call, WAL mode) so a crash
    mid-run loses nothing already recorded - this script is a short-lived
    process invoked fresh per run, so there is no long-lived connection to
    manage. A `runs` row whose finished_at is still NULL means the process
    exited without ever finalizing it (e.g. a crash or a killed process,
    before main()'s own error handler could run) - treat such rows as an
    aborted run, not a currently-in-progress one. A login failure no longer
    leaves finished_at NULL: main() catches it per account and finalizes the
    run as "partial_failure" instead.
    """

    _PRICE_HISTORY_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS runs (
        run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at       TEXT NOT NULL,
        finished_at      TEXT,
        status           TEXT NOT NULL DEFAULT 'started',
        error_summary    TEXT
    );

    CREATE TABLE IF NOT EXISTS price_points (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(run_id),
        observed_at      TEXT NOT NULL,
        account_label    TEXT,
        reservation_id   TEXT,
        ship_code        TEXT,
        sail_date        TEXT,
        nights           INTEGER,
        item_kind        TEXT NOT NULL,
        item_code        TEXT,
        item_name        TEXT,
        guest_id         TEXT,
        guest_name       TEXT,
        paid_price       REAL,
        current_price    REAL,
        currency         TEXT,
        per_night        INTEGER NOT NULL DEFAULT 0,
        discount_applied TEXT,
        status           TEXT NOT NULL,
        rebook_decision  TEXT,
        notified         INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_price_points_latest
        ON price_points (reservation_id, item_code, guest_id, observed_at DESC);

    CREATE INDEX IF NOT EXISTS idx_price_points_history
        ON price_points (item_code, sail_date, observed_at);

    CREATE INDEX IF NOT EXISTS idx_price_points_run
        ON price_points (run_id);

    CREATE TABLE IF NOT EXISTS bookings (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(run_id),
        observed_at      TEXT NOT NULL,
        account_label    TEXT,                 -- account_info.username
        reservation_id   TEXT NOT NULL,
        ship_code        TEXT,
        ship_name        TEXT,                 -- from the ShipRegistry the run already built
        sail_date        TEXT,                 -- YYYYMMDD
        nights           INTEGER,
        stateroom_type   TEXT,                 -- e.g. BALCONY / INTERIOR / GTY label as the script prints it
        stateroom_number TEXT,                 -- may be NULL (GTY not yet assigned)
        stateroom_category TEXT,               -- e.g. 4D
        guest_count      INTEGER,
        guests_json      TEXT,                 -- [{"name": "...", "id": "...", "age_bracket": "adult"}]
        loyalty_tier     TEXT,                 -- from get_profile(): C&A tier label; NULL if unknown
        loyalty_points   INTEGER,
        checkin_label    TEXT,                 -- the string the summary table prints (e.g. "Opens Dec 10 8:00 AM")
        final_payment_date TEXT,               -- YYYYMMDD, from get_final_payment_date
        past_final_payment INTEGER,            -- 0/1
        balance_due      INTEGER,              -- 1 / 0 / NULL (unknown, TA bookings)
        booking_currency TEXT,
        friendly_name    TEXT                  -- reservationFriendlyNames entry if configured
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_latest ON bookings (reservation_id, observed_at DESC);

    CREATE TABLE IF NOT EXISTS promos (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(run_id),
        observed_at      TEXT NOT NULL,
        account_label    TEXT,
        ship_code        TEXT,
        sail_date        TEXT,
        promo_id         TEXT,
        promo_title      TEXT,
        promo_line       TEXT,                 -- the human-readable line the script logs
        promo_start      TEXT,
        promo_end        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_promos_run ON promos (run_id);
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.enabled = bool(db_path)
        self.db_path = db_path
        self._current_run_id: Optional[int] = None
        if self.enabled:
            try:
                with closing(self._connect()) as conn:
                    conn.executescript(self._PRICE_HISTORY_SCHEMA_SQL)
            except (sqlite3.Error, OSError) as e:
                self._disable(e)

    def __repr__(self) -> str:
        if not self.enabled:
            return "<PriceHistory enabled=False>"
        return f"<PriceHistory db={self.db_path!r} enabled=True run_id={self._current_run_id}>"

    def _disable(self, error: Exception) -> None:
        """A history sink must never take down the price run it observes: on any
        database error, log one warning and degrade to the no-op object the
        disabled path already is. Price checking continues without history."""
        self.enabled = False
        logging.warning(
            f"Price history disabled for the rest of this run: could not write "
            f"{self.db_path!r} ({type(error).__name__}: {error}). Price checking "
            f"is unaffected; check the historyDb path/permissions."
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        # busy_timeout must be armed BEFORE switching journal modes: the WAL
        # switch itself needs a lock, and without a timeout a concurrent run
        # (e.g. a scheduled task overlapping a manual one) fails immediately
        # with "database is locked" on some filesystems (seen on WSL /mnt/c)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def start_run(self) -> Optional[int]:
        """Opens a new `runs` row and remembers it as the active run."""
        if not self.enabled:
            return None
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    "INSERT INTO runs (started_at, status) VALUES (?, 'started')",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                conn.commit()
                self._current_run_id = cur.lastrowid
                return self._current_run_id
        except (sqlite3.Error, OSError) as e:
            self._disable(e)
            return None

    def finish_run(self, status: str, error_summary: Optional[str] = None) -> None:
        """Closes out the active `runs` row opened by the last start_run()."""
        if not self.enabled or self._current_run_id is None:
            return
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    "UPDATE runs SET finished_at=?, status=?, error_summary=? WHERE run_id=?",
                    (datetime.now(timezone.utc).isoformat(), status, error_summary, self._current_run_id),
                )
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            self._disable(e)

    def record_cabin_fare(self, **fields: Any) -> None:
        """Appends one `price_points` row for a cabin-fare observation."""
        if not self.enabled or self._current_run_id is None:
            return
        self._insert("price_points", item_kind="cabin_fare", **fields)

    def record_addon(self, **fields: Any) -> None:
        """Appends one `price_points` row for an addon/watchlist observation."""
        if not self.enabled or self._current_run_id is None:
            return
        self._insert("price_points", **fields)

    def record_booking(self, **fields: Any) -> None:
        """Appends one `bookings` row: a full snapshot of one reservation this run."""
        if not self.enabled or self._current_run_id is None:
            return
        self._insert("bookings", **fields)

    def record_promo(self, **fields: Any) -> None:
        """Appends one `promos` row for one active sitewide promotion this run."""
        if not self.enabled or self._current_run_id is None:
            return
        self._insert("promos", **fields)

    def _insert(self, table: str, **fields: Any) -> None:
        fields.setdefault("observed_at", datetime.now(timezone.utc).isoformat())
        fields["run_id"] = self._current_run_id
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        try:
            with closing(self._connect()) as conn:
                conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(fields.values()))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            self._disable(e)


class CheckinPaymentTracker:
    """
    Tracks and deduplicates check-in and final payment metrics across accounts/bookings,
    rendering an end-of-run summary table.
    """
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def record_row(self, row: Dict[str, Any]) -> None:
        """
        Adds a booking to the end-of-run summary table, merging duplicate linked reservations.
        """
        key = row.get("dedupe_key")
        for existing in self.rows:
            if key is not None and existing.get("dedupe_key") == key:
                if existing.get("balance_due") not in (True, False) and row.get("balance_due") in (True, False):
                    existing["balance_due"] = row["balance_due"]
                    existing["past_final_payment"] = row["past_final_payment"]
                if existing.get("checkin_label") in (None, "TBD") and row.get("checkin_label") not in (None, "TBD"):
                    existing["checkin_label"] = row["checkin_label"]
                return
        self.rows.append(row)

    def print_table(self) -> None:
        """
        Prints a compact end-of-run summary table sorted by sail date.
        """
        if not self.rows:
            return

        rows = sorted(self.rows, key=lambda r: r["sail_date"] or "")

        headers = ("Sail Date", "Ship (Room)", "Reservation", "Check-In", "Final Payment")
        table = []
        pay_colors = []
        for r in rows:
            sail = config.format_date(r["sail_date"]) if r["sail_date"] else "?"
            if r["final_payment"] is not None:
                pay = r["final_payment"].strftime(config.date_display_format)
                if r["balance_due"] is True:
                    if r["past_final_payment"]:
                        pay += " (PAST DUE)"
                        pay_colors.append(RED)
                    else:
                        pay += " (balance due)"
                        pay_colors.append(YELLOW)
                elif r["balance_due"] is False:
                    pay += " (paid)"
                    pay_colors.append(GREEN)
                elif r["balance_due"] == "TA_UNKNOWN":
                    pay += " (contact TA for balance)"
                    pay_colors.append(YELLOW)
                else:
                    # None, or any unexpected raw API value (1, "true", ...):
                    # ALWAYS append a color - a skipped append desynced
                    # pay_colors from table, and the zip() below silently
                    # dropped the last row(s) and shifted colors onto the
                    # wrong rows
                    pay += " (status unknown)"
                    pay_colors.append(YELLOW)
            else:
                pay = "-"
                pay_colors.append("")
            table.append((sail, r["name"], r.get("reservation", "-"), r["checkin_label"], pay))

        widths = [max(len(str(row[i])) for row in ([headers] + table)) for i in range(len(headers))]

        def fmt(cells: Tuple[str, ...], pay_color: str = "") -> str:
            padded = [str(c).ljust(widths[i]) for i, c in enumerate(cells)]
            if pay_color:
                padded[-1] = f"{pay_color}{padded[-1]}{RESET}"
            return "  ".join(padded)

        log(f"\n{BLUE}Upcoming Check-In & Final Payment Dates{RESET}")
        log(fmt(headers))
        log("  ".join("-" * w for w in widths))
        prev_sail = None
        for row, pay_color in zip(table, pay_colors):
            display_row = ("" if row[0] == prev_sail else row[0],) + row[1:]
            prev_sail = row[0]
            log(fmt(display_row, pay_color))


############################################
# Low-level Network Engine & Data Harvesters
############################################
def new_api_session(use_impersonation: bool = True) -> plain_requests.Session:
    """
    Creates a network session that impersonates a real browser's TLS fingerprint
    when curl_cffi is available and requested, falling back to standard requests.
    """
    if use_impersonation and IMPERSONATE_ARGS:
        return requests.Session(**IMPERSONATE_ARGS)
    return plain_requests.Session()


def _execute_api_request(
    account_info: Optional[AccountInfo] = None,
    method: str = "GET",
    url: str = "",
    params: Optional[dict] = None,
    data: Optional[Union[str, dict]] = None,
    json_data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: Optional[int] = None,
    on_failure: str = DEFAULT_ON_FAILURE,
    exit_on_fail: Optional[bool] = None,
    max_retries: int = MAX_RETRIES,
    use_impersonation: bool = True
) -> Optional[plain_requests.Response]:
    """
    Unified API execution engine for all cruise line network interactions.

    Centralizes tracking parameters, developer keys, and connect timeouts.
    If an active session profile exists, it automatically injects 'Access-Token'
    and account tracking headers into the request context.

    Supported strategies for on_failure:
    - "retry": Automatically retries transient errors with exponential backoff.
    - "skip" : Logs the warning and returns None on failure.
    - "exit" : Logs the error and terminates the script entirely on failure.
    """
    # Backwards compatibility helper for existing exit_on_fail parameter callers
    if exit_on_fail is not None:
        on_failure = "exit" if exit_on_fail else "skip"

    # Resolve effective timeout: explicit override -> config setting -> default baseline
    if timeout is None:
        timeout = getattr(config, "request_timeout", REQUEST_TIMEOUT) if 'config' in globals() else REQUEST_TIMEOUT

    # Start with caller override headers or an empty dictionary
    final_headers = headers.copy() if headers else {}

    # Inject corporate authentication layers if a live session exists
    if account_info and getattr(account_info, "access", None):
        if "Access-Token" not in final_headers and account_info.access.token:
            final_headers["Access-Token"] = account_info.access.token
        if "vds-id" not in final_headers and account_info.access.id:
            final_headers["vds-id"] = account_info.access.id
        if "account-id" not in final_headers and account_info.access.id:
            final_headers["account-id"] = account_info.access.id

    # Always include baseline developer web key
    if "AppKey" not in final_headers and "appkey" not in final_headers:
        final_headers["AppKey"] = APPKEY_WEB

    # Target session selection: existing session token or new engine session
    if account_info and getattr(account_info, "access", None) and account_info.access.session:
        session_context = account_info.access.session
    else:
        session_context = new_api_session(use_impersonation=use_impersonation)

    def _handle_terminal_failure(error: Exception) -> Optional[plain_requests.Response]:
        error_msg = f"Can't contact cruise line servers; please try again later\n(program exception '{error}')"
        if on_failure == "exit":
            log(error_msg)
            sys.exit(EXIT_TOTAL_FAILURE)
        else:
            logging.warning(f"Non-critical API interaction skipped (exception: {error})")
            return None

    # --- STRATEGY A: RESILIENT RETRY LOOP ---
    if on_failure == "retry":
        for attempt in range(1, max_retries + 1):
            try:
                response = session_context.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    data=data,
                    json=json_data,
                    headers=final_headers,
                    timeout=timeout
                )

                # Treat 5xx server errors as transient retriable errors
                if response.status_code >= 500:
                    raise plain_requests.exceptions.HTTPError(
                        f"Server Error {response.status_code}", response=response
                    )

                response.raise_for_status()
                return response  # Success!

            except Exception as e:
                # Terminal 4xx client errors (e.g. 401, 403, 404) fail fast without retrying
                resp_obj = getattr(e, "response", None)
                status_code = getattr(resp_obj, "status_code", None)
                # Fallback: curl_cffi's HTTPError does not always attach .response -
                # parse the status out of the exception text ("404 Client Error")
                # so a definitive client error is never misread as transient and
                # retried. Match only HTTP-status phrasing: a bare \b[45]\d\d\b
                # also matched the "port 443" in every HTTPS connection-failure
                # message, misclassifying transient network errors as terminal
                # 4xx and skipping every retry.
                if status_code is None:
                    match = re.search(
                        r"\b([45]\d\d)\s+(?:client|server)\s+error\b"
                        r"|\bhttp(?:\s+error)?\s*:?\s*([45]\d\d)\b"
                        r"|\bstatus(?:\s+code)?\s*:?\s*([45]\d\d)\b",
                        str(e), re.IGNORECASE)
                    if match:
                        status_code = int(next(g for g in match.groups() if g))
                if status_code and 400 <= status_code < 500:
                    return _handle_terminal_failure(e)

                if attempt < max_retries:
                    backoff_time = RETRY_BACKOFF_BASE ** attempt
                    logging.warning(f"Attempt {attempt}/{max_retries} failed for {url}: {e}. Retrying in {backoff_time}s...")
                    time.sleep(backoff_time)
                else:
                    logging.warning(f"All {max_retries} retry attempts exhausted for {url}.")
                    return _handle_terminal_failure(e)

    # --- STRATEGY B: STATIC SINGLE-SHOT ACTIONS ("skip" or "exit") ---
    try:
        response = session_context.request(
            method=method.upper(),
            url=url,
            params=params,
            data=data,
            json=json_data,
            headers=final_headers,
            timeout=timeout
        )
        response.raise_for_status()
        return response
    except Exception as e:
        return _handle_terminal_failure(e)


def _extract_json_array(text: str, key: str) -> Optional[list[Any]]:
    """
    Finds and extracts a specific JSON array buried inside raw text chunks.

    Uses bracket-counting to parse nested arrays ('[' and ']') while bypassing
    escaped quotes. Crucial for harvesting transient elements like 'pricingAddOns'
    from server responses where standard json.loads() fails on the entire page text.

    MAINTENANCE NOTE: The cruise line servers wrap complex background data arrays
    inside raw HTML text pages. This bracket-counting routine slices those hidden
    JSON objects out directly when standard 'response.json()' parsing isn't an option.

    SAFETY NOTE: Because we slice raw text from HTML component fragments, the strings may contain
    unescaped quotes or trailing data points. The bracket-counting tracker manually calculates
    the array boundary [ ] to ensure 'json.loads' receives a perfectly valid string payload.
    """
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not m:
        return None

    start = m.end() - 1  # Exact string position index of the opening '['
    depth, i = 0, start
    in_string, escape = False, False

    while i < len(text):
        ch = text[i]

        if escape:
            escape = False
        elif ch == "\\" and in_string:
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    # Successfully isolated the exact substring boundaries of the array
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        i += 1
    return None


def print_response(response: Union[Dict[str, Any], List[Any], str, requests.Response]) -> None:
    """
    Debug utility to format and display raw API responses.

    Transforms nested API response JSON payloads or dictionary objects into standard,
    indented strings for readable terminal diagnosis during live testing.
    """
    json_resp = json.dumps(response, indent=2)
    log("API returned output:")
    log(json_resp)


##################
# Helper Functions
##################
def above_age_on_sail_date(birth_date: str, sail_date: str, age_threshold: int) -> bool:
    """
    Determines if a passenger meets a specific age requirement on their voyage date.

    Accepts raw date stamps formatted as 'YYYYMMDD'. Evaluates whether the current
    calendar anniversary month and day have been crossed on the ship's sailing
    timeline to account for fractional year offsets.
    """
    if not birth_date or not sail_date:
        return False

    dt1 = datetime.strptime(birth_date, "%Y%m%d")
    dt2 = datetime.strptime(sail_date, "%Y%m%d")
    age = dt2.year - dt1.year

    # Adjust if birthday hasn’t happened yet this year
    if (dt2.month, dt2.day) < (dt1.month, dt1.day):
        age -= 1

    return age >= age_threshold


def _discount_flag_on(value: Any) -> bool:
    """Normalize a senior/military/police/fire flag: booleans from URL parsing,
    'y'/'yes'/'true' strings from configs; anything else (incl. 'n') is off."""
    if value is True:
        return True
    return str(value).strip().lower() in ("y", "yes", "true")

def resolve_lead_time(number_of_nights: int, market_code: Optional[str] = None) -> int:
    """Resolves lead time days using market-specific duration tiers."""
    code = market_code.upper() if market_code else "US"
    rule = MARKET_RULES.get(code, MARKET_RULES["US"])

    # If the market has a flat rule (e.g., DEU = 30)
    if isinstance(rule, int):
        return rule

    # Otherwise, evaluate duration tiers
    for max_nights, days in rule:
        if number_of_nights <= max_nights:
            return days

    return 90  # Safe fallback

def get_final_payment_date(
    number_of_nights: int,
    sail_date: Union[str, date, datetime],
    market_code: Optional[str] = None,
    final_payment_date_override: Optional[Union[int, str, date, datetime]] = None,
) -> date:
    """
    Calculates final payment settlement timelines based on duration and market rules,
    or accepts an explicit date/days override from user config.
    """
    # 1. Standardize date_of_sailing first
    if isinstance(sail_date, (datetime, date)):
        date_of_sailing = sail_date.date() if isinstance(sail_date, datetime) else sail_date
    elif isinstance(sail_date, str):
        clean_date_str = sail_date.replace("-", "").replace("/", "")
        try:
            date_of_sailing = datetime.strptime(clean_date_str, "%Y%m%d").date()
        except ValueError as e:
            raise ValueError(f"Invalid sail_date string format '{sail_date}'. Expected YYYYMMDD or YYYY-MM-DD.") from e
    else:
        raise TypeError("sail_date must be a string, date, or datetime object.")

    # 2. Highest Priority: Explicit User Date Override
    if final_payment_date_override is not None:
        if isinstance(final_payment_date_override, int):
            return date_of_sailing - timedelta(days=final_payment_date_override)

        if isinstance(final_payment_date_override, (datetime, date)):
            return (
                final_payment_date_override.date()
                if isinstance(final_payment_date_override, datetime)
                else final_payment_date_override
            )

        if isinstance(final_payment_date_override, str):
            clean_override = final_payment_date_override.strip()
            if clean_override.isdigit() and len(clean_override) <= 3:
                return date_of_sailing - timedelta(days=int(clean_override))

            clean_override = clean_override.replace("-", "").replace("/", "")
            try:
                return datetime.strptime(clean_override, "%Y%m%d").date()
            except ValueError as e:
                raise ValueError(
                    f"Invalid finalPaymentDate string format '{final_payment_date_override}'. Expected YYYY-MM-DD or lead days."
                ) from e

    # 3. Compute Days Before Departure via Market Rules
    lead_time_days = resolve_lead_time(number_of_nights, market_code)
    return date_of_sailing - timedelta(days=lead_time_days)


def get_config_path() -> str:
    """
    Parses command-line arguments to locate the application configuration file.

    Handles cross-platform routing. On desktop platforms, it evaluates the
    '-c/--config' terminal flag (defaulting to 'config.yaml'). On iOS devices,
    it automatically points to the local sandbox '~/Documents' directory.
    """
    parser = argparse.ArgumentParser(description="Check Royal Caribbean Price")
    parser.add_argument('-c', '--config', type=str, default='config.yaml', help='Path to configuration YAML file (default: config.yaml)')
    args = parser.parse_args()
    if platform.system() != "iOS":
        return args.config
    else:
        return os.path.expanduser('~/Documents') + "/" + args.config


def get_club_royale_tier(points: int) -> str | None:
    """Computes Club Royale Tier name based on individual tier credits."""
    if points is None or points <= 0:
        return None
    elif points < 2500:
        return "CHOICE"
    elif points < 25000:
        return "PRIME"
    elif points < 100000:
        return "ICON"
    else:
        return "MASTERS"


#####################################
# Criuse Domain and Pricing Functions
#####################################
#
# Fleet Discovery functions #
#
def get_ship_dictionary_web(registry: ShipRegistry) -> None:
    """
    Queries corporate servers to construct a dictionary tracking active fleet ship profiles.

    Populates an in-memory ship lookup container mapping corporate short codes
    (e.g., 'AL', 'SY') to user-friendly vessel names, preventing structural lookups
    from displaying blank codes during reporting.
    """
    url: str = 'https://aws-prd.api.rccl.com/en/royal/web/v2/ships'
    params: Dict[str, str] = {
        'sort': 'name',
    }
    # Accept header isn't managed globally, so we pass it explicitly
    headers: Dict[str, str] = {
        'Accept': 'application/json',
    }

    # Centralized manager handles headers, global keys, try/except, and exit(1) on failure
    response = _execute_api_request(
        account_info=None,  # Public endpoint, no active account session required
        method="GET",
        url=url,
        params=params,
        headers=headers,
        on_failure="retry"
    )

    try:
        ships = response.json().get("payload", {}).get("ships", [])
        registry.add_from_payload(ships)
    except Exception as e:
        if response is None:
            log(f"{YELLOW}[WARN] Fleet API unreachable. Falling back to raw ship codes.{RESET}")
        else:
            log(f"{YELLOW}[WARN] Fleet API schema parsing failed ({e}). Falling back to raw ship codes.{RESET}")
        return


#
# URL & Request Parser functions #
#
def parse_provided_URL(url: str) -> CruiseURLParams:
    """
    Parses a consumer-facing booking engine browser URL into a structured CruiseURLParams object.

    Uses urlparse and parse_qs to extract parameters. Translates localized query characters
    (like 'y' or 'n' inside 'r0t', 'r0q', etc.) directly into explicit Python Booleans.
    Employs an explicit list-truthiness conditional check to cleanly resolve and fallback
    between alternative cabin class query parameters ('cabinClassType' vs. 'r0d') safely.
    """
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    domain = parsed_url.netloc

    # Extract qualifiers safely with fallback defaults before parsing booleans
    r0t_val = params.get("r0t", ["n"])[0]
    r0q_val = params.get("r0q", ["n"])[0]
    r0r_val = params.get("r0r", ["n"])[0]
    r0s_val = params.get("r0s", ["n"])[0]

    r0d_list = params.get("r0d")
    cabin_class_type_list = params.get("cabinClassType")

    if cabin_class_type_list:
        cabin_string = cabin_class_type_list[0]
    elif r0d_list:
        cabin_string = r0d_list[0]
    else:
        cabin_string = ""

    # Some Countries List Cabin String as B, causing issue with room lookup
    parsed_cabin_string = _parse_stateroom_type(cabin_string)
    cabin_string = parsed_cabin_string if parsed_cabin_string != "NONE" else cabin_string

    # Extract sub-type (r0e) and category code (r0f) with fallbacks for Guarantee (GTY) codes
    raw_r0e = params.get("r0e", [None])[0]
    raw_r0f = params.get("r0f", [None])[0]

    # If r0e or r0f are missing, fallback to r0d / cabin_string if it's a specific category code (e.g. XB)
    stateroom_subtype = raw_r0e
    stateroom_category_code = raw_r0f

    # Parse the URL parameters and save in a class instance
    return CruiseURLParams(
        is_royal="royal" in domain,
        sail_date=params.get("sailDate", [None])[0],
        currency_code=params.get("selectedCurrencyCode", ["USD"])[0],
        booking_office_country_code=params.get("country", ["USA"])[0],
        ship_code=params.get("shipCode", [None])[0],
        cabin_class_string=cabin_string,
        stateroom_type_name=r0d_list[0] if r0d_list else None,
        stateroom_subtype=stateroom_subtype,
        stateroom_category_code=stateroom_category_code,
        package_code=params.get("packageCode", [None])[0],
        number_of_adults=params.get("r0a", ["2"])[0],
        number_of_children=params.get("r0c", ["0"])[0],
        loyalty_number=params.get("r0l", [None])[0],
        username=params.get("r0H", [None])[0],
        state=params.get("r0k", [None])[0],
        all_included=params.get("r0o", ["XXX"])[0] != "XXX",
        refundable=params.get("r0u", ["XXX"])[0] != "XXX",
        travel_insurance=params.get("r0n", ["n"])[0] != "n",
        prepaid_grats=params.get("r0m", ["n"])[0] != "n",
        coupon_code=params.get("r0i", [None])[0],
        senior=(r0t_val == "y"),
        military=(r0q_val == "y"),
        police=(r0r_val == "y"),
        fire=(r0s_val == "y")
    )


def _parse_stateroom_type(room_type_code: Optional[str]) -> str:
    """
    Translates raw single-character stateroom types into explicit checkout parameters.

    Maps internal character letters (such as 'I', 'O', 'B') to explicit structural
    keywords expected by corporate inventory checkout paths (e.g., 'INTERIOR', 'OUTSIDE', 'BALCONY').
    """
    if not room_type_code:
            return "NONE"
    return STATEROOM_TYPE_MAPPING.get(room_type_code.upper(), "NONE")


def sanitize_category_code(code: Optional[str]) -> Optional[str]:
    """
    Validates that a string is a genuine subcategory/rate code (e.g., 'XB', '2D', 'CB')
    and not a macro stateroom type or single-character classification.
    """
    if not code:
        return None

    # Reject known generic classification words/letters
    cleaned = code.strip().upper()
    if cleaned in CHECKOUT_FORBIDDEN_CATEGORY_CODES:
        return None

    # Caribbean category codes are at least 2 characters (e.g., 'XB', '2D', 'CB')
    if len(cleaned) < 2:
        return None

    return cleaned


def _booking_country_code(booking: Dict[str, Any]) -> Optional[str]:
    """
    Resolves the country code a pricing/availability request should send.

    International/TA bookings carry two country fields: bookingOfficeCountryCode
    (the travel agent's own office, e.g. a German agency) and
    bookingMarketCountryCode (the market the guest actually bought in, e.g.
    Switzerland). The checkout API validates country against the booking
    currency, so an office/currency pair that isn't itself a sales market gets
    rejected (400/500) even though the market code prices fine. Prefer the
    market code, fall back to the office code, and return None -- not the
    literal string "None" -- when neither is present so callers can omit the
    parameter and let downstream defaults (e.g. parse_provided_URL()'s "USA") apply.
    """
    # The API validates this against ^[A-Z]{3}$, so normalise here: a padded or
    # lower-case value would 400 just like the wrong country does, and a
    # whitespace-only value must fall through rather than "win" the preference.
    for key in ("bookingMarketCountryCode", "bookingOfficeCountryCode"):
        value = (booking.get(key) or "").strip().upper()
        if value:
            return value
    return None


# Unknown market codes we already warned about this run (warn once per code,
# not once per booking - multi-booking accounts would otherwise spam it)
_UNKNOWN_MARKETS_WARNED: set = set()


# ---- checkForUpgrades support (Phase 1 port of the fork's upgrade checker) ----
# Class ladder for upgrade ranking. RSC stateroomType codes.
TYPE_RANK = {"INTERIOR": 0, "OUTSIDE": 1, "BALCONY": 2, "CONCIERGE": 3, "AQUA": 3, "DELUXE": 4}

# Within a class, price is the upgrade proxy - but these niche products price
# above regular cabins without being better ones (a Studio is a smaller solo
# cabin; obstructed/partial views are lesser variants). They are only screened
# for SAME-class comparisons: as a class jump they are still genuine upgrades.
LESSER_PRODUCT = re.compile(r"studio|obstruct|partial view", re.I)

# Ledger discount/option descriptions that mark a Club Royale casino-rate booking
CASINO_MARKER = re.compile(r"casino|clubr|club royale", re.I)

# upgradeReservations ids that matched a booking this run (to warn about typos)
_UPGRADE_SCOPE_SEEN: Set[str] = set()


def is_upgrade_candidate(booked_rank: Optional[int], booked_now: Optional[float],
                         row_rank: Optional[int], row_total: float,
                         row_name: str = "") -> bool:
    """
    Whether an inventory row counts as an UPGRADE over the booked category:
    a higher class, or - within the same class - a non-niche category pricing
    above the booked category's current rate.

    booked_rank None means the booked class could not be established (nothing
    from the booked subtype is on sale). Never guess in that case: treating
    "unknown" as "lowest" once alerted a balcony 1B booking to "upgrade" to an
    interior 4U, because every class outranked the -1 fallback.
    """
    if booked_rank is None or row_rank is None:
        return False
    if row_rank > booked_rank:
        return True
    return (row_rank == booked_rank and isinstance(booked_now, (int, float))
            and row_total > booked_now
            and not LESSER_PRODUCT.search(row_name or ""))


def _is_price(value: Any) -> bool:
    """A usable quote: a real, positive, finite number. Booleans, NaN, infinity,
    zero and negatives are not prices - a row carrying one is not offered, never
    alerted on and never anchors a delta (a -1 total once read as a huge saving)."""
    return type(value) in (int, float) and 0 < value < float("inf")


def should_apply_dp340(eligible: bool, booked_with_code: bool, guest_count: int) -> bool:
    """Quote a solo booking with the DP340 single-supplement code when the
    account qualifies (Royal, 340+ Crown & Anchor points), or when the booking
    already carries the code - repricing keeps the terms it was booked on.
    Never on multi-guest bookings."""
    return (eligible or booked_with_code) and guest_count == 1


def _get_upgrade_category_prices(url_params: CruiseURLParams, stype: Optional[str],
                                 dp340: bool = False) -> tuple[Dict[str, float], bool]:
    """
    Per-CATEGORY all-in totals inside ONE subtype family (e.g. 2D alongside 4D
    under subtype D), via the room-selection JSON API - one POST per booking,
    fetched only for the booked family. Sept 2026: this endpoint is
    POST-with-JSON-body; the old GET form gets a blanket Akamai 403.

    Returns ({categoryCode: total}, dp340_actually_applied).
    """
    room: Dict[str, Any] = {
        "adultCount": int(url_params.number_of_adults or 1),
        "childCount": int(url_params.number_of_children or 0),
        "stateroomTypeCode": stype,
        "stateroomSubtypeCode": url_params.stateroom_subtype,
        "accessible": False, "selectionFallbackStrategy": "RECOMMENDATION",
        "editMode": True, "reset": False, "taxesAndFeesBundled": True,
    }
    # same qualifier block the checkout POST sends, so the booked family is
    # priced on the same basis as every other row of the table
    room["qualifiers"] = {
        "fireFighter": url_params.fire, "military": url_params.military,
        "police": url_params.police, "senior": url_params.senior,
    }
    if url_params.loyalty_number:
        room["qualifiers"]["loyaltyNumber"] = str(url_params.loyalty_number)
    if url_params.state:
        room["qualifiers"]["stateCode"] = url_params.state
    if dp340:
        room["couponCode"] = "DP340"
    flt = {"countryCode": url_params.booking_office_country_code or "USA",
           "packageId": url_params.package_code,
           "sailDate": url_params.sail_date,
           "currencyCode": url_params.currency_code or "USD",
           "language": "en", "options": True, "roomNumbers": True,
           "rooms": [room], "platform": "web"}
    headers = {"user-agent": USER_AGENT_WEB, "accept": "*/*",
               "content-type": "application/json",
               "brand": "R" if url_params.is_royal else "C",
               "country": url_params.booking_office_country_code or "USA"}

    response = _execute_api_request(
        account_info=None, method="POST",
        url=f"https://www.{url_params.url_brand}.com/room-selection/api/v1/rooms",
        data=json.dumps(flt), headers=headers, on_failure="skip")

    prices: Dict[str, float] = {}
    if response is not None:
        try:
            for rm in (response.json().get("rooms") or []):
                for cat in ((rm.get("roomNumbers") or {}).get("categories") or []):
                    code = cat.get("categoryCode") or cat.get("code")
                    total = (((cat.get("pricing") or {}).get("invoice")) or {}).get("total")
                    if code and _is_price(total):
                        prices[code] = float(total)
        except Exception:
            prices = {}
    if not prices and dp340:
        # A coupon-priced request can fail as a 4xx or an empty body when the
        # coupon is rejected - retry once without the code instead of silently
        # losing the per-category view
        log(f"\t{YELLOW}DP340-priced category request returned nothing; retrying without the code{RESET}")
        return _get_upgrade_category_prices(url_params, stype, dp340=False)
    return prices, dp340


def _upgrade_money(v: Optional[float], sym: str = "$") -> str:
    return f"{sym}{v:,.2f}" if isinstance(v, (int, float)) else "-"


def _upgrade_delta(v: Optional[float], width: int = 12, sym: str = "$") -> str:
    """Signed money, right-padded to a fixed VISIBLE width, green when <= 0 (a
    saving). Colour codes are applied after padding so columns stay aligned."""
    if not isinstance(v, (int, float)):
        return "-".rjust(width)
    if abs(v) < 0.005:
        return f"{GREEN}{(sym + '0.00').rjust(width)}{RESET}"
    text = f"{'+' if v > 0 else '-'}{sym}{abs(v):,.2f}".rjust(width)
    return f"{GREEN}{text}{RESET}" if v <= 0 else text


def _booking_payment_market(booking: Dict[str, Any]) -> Optional[str]:
    """
    The market code the FINAL-PAYMENT rules should use: the first of
    bookingMarketCountryCode / bookingOfficeCountryCode / countryCode that
    MARKET_RULES actually knows.

    This answers a different question than _booking_country_code (the right
    helper for checkout-API pricing calls). Royal's market vocabulary is not
    all ISO - Switzerland arrives as CHS - and resolve_lead_time silently
    defaults unknown codes to the US windows, so a code the table doesn't
    know must fall through to the next candidate instead of quietly turning
    a 30-day market into a 90-day one. None -> caller gets the US default.
    """
    candidates = [(booking.get(key) or "").strip().upper()
                  for key in ("bookingMarketCountryCode", "bookingOfficeCountryCode",
                              "countryCode")]
    for code in candidates:
        if code in MARKET_RULES:
            return code
    unknown = [c for c in candidates if c]
    if unknown and unknown[0] not in _UNKNOWN_MARKETS_WARNED:
        _UNKNOWN_MARKETS_WARNED.add(unknown[0])
        if log_warn:
            log_warn(f"Booking market code(s) {'/'.join(dict.fromkeys(unknown))} not in "
                     f"MARKET_RULES - using US final-payment windows. Please report the "
                     f"code so the right window can be added.")
    return None


#
# Profile and Session Management Functions #
#
def login(account_info: AccountInfo) -> APIAccess:
    """
    Performs OAuth2 authentication against corporate cruise line identity endpoints.

    Submits standard encoded payloads to capture bearer authorization access tokens.
    Decodes the resulting middle payload segment via base64 to extract the underlying
    account identifier token ('sub'). Terminates the execution thread if authorization fails.

    MAINTENANCE NOTE: OAuth tokens returned by the cruise system are standard JSON Web Tokens (JWT).
    The server splits these using dots (.). Slicing index [1] isolates the base64-encoded payload string.
    Appending '==' satisfies Python's strict base64 pad requirements to prevent standard padding crashes.

    The 'Basic' Authorization hash is a universal hardcoded client, client-id
    and secret utilized by the cruise line's public mobile app and web infrastructure
    to secure the background OAuth handshake process.
    """
    session = new_api_session()
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': USER_AGENT_WEB,
    }

    username = account_info.username
    password = account_info.password
    url_safe_password  = quote(password, safe='')
    data = f'grant_type=password&username={username}&password={url_safe_password}&scope=openid+profile+email+vdsid'

    # Attempt the login using the provided variables
    # TODO: Refactor to unified execution engine in a future architecture pass.
    # NOTE: This is left as a direct session call for now to guarantee that the
    # login cookie container and initial OAuth handshakes are preserved perfectly
    # without running into downstream fallback session side-effects.
    try:
        response = session.post(f'https://www.{account_info.url_brand}.com/auth/oauth2/access_token', headers=headers, data=data, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(EXIT_TOTAL_FAILURE)

    if response.status_code != 200:
        log(f"Login attempt got return code {response.status_code} for user {account_info.username}")

        # The status code alone cannot distinguish a rejected password
        # ("invalid_grant") from a malformed request ("invalid_request") or an
        # edge/WAF block that returns HTML - which makes a login failure very
        # hard to diagnose. Surface the server's own error text; the OAuth
        # error body carries no credentials, and the password is scrubbed
        # defensively in case an echo is ever added.
        detail = (response.text or "").strip()
        if password and password in detail:
            detail = detail.replace(password, "***")
        if detail:
            log(f"	Server said: {detail[:300]}")

        log(f"{account_info.cruise_line} website might be down, username/password incorrect, or have unsupported symbol in password. Quitting.")
        sys.exit(EXIT_TOTAL_FAILURE)

    # Parse out the account's ID and access token
    access_token = response.json().get("access_token")

    try:
        list_of_strings = access_token.split(".")
        if len(list_of_strings) < 2:
            raise ValueError("Token does not contain a valid JWT payload segment.")
        string1 = list_of_strings[1]
        # JWT segments are base64URL: standard b64decode silently drops -/_
        # (validate=False), shifting later bytes and failing the json parse
        # for tokens whose payload contains such a byte
        decoded_bytes = base64.urlsafe_b64decode(string1.replace('+', '-').replace('/', '_') + '==')
        auth_info = json.loads(decoded_bytes.decode('utf-8'))
        account_ID = auth_info["sub"]
    except(IndexError, ValueError, KeyError, AttributeError, TypeError) as parse_err:
        # AttributeError/TypeError: a 200 with no access_token leaves it None
        log(f"Error parsing authentication token structure: {parse_err}")
        sys.exit(EXIT_TOTAL_FAILURE)

    # Store the server access value in an APIAccess object and return
    return APIAccess(
        token = access_token,
        id = account_ID,
        session = session
    )


def get_profile(account_info: AccountInfo) -> Tuple[Optional[str], Optional[str], int]:
    """
    Retrieves personal profile properties to extract valid residency codes and loyalty tiers.

    Inspects user contact records to locate primary residency states and tracks concurrent
    loyalty modules (Crown & Anchor, Club Royale, Captain's Club, and Blue Chip). Returns
    the active brand tracking index to route downstream web requests correctly. Also stashes
    the active-brand loyalty tier label and individual points directly onto account_info
    (history-layer snapshot fields only - never read by any discount/alert logic) since
    account_info is already in hand here and get_voyages()'s signature must not change.
    """
    url = f"https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v3/guestAccounts/{account_info.access.id}"
    response = _execute_api_request(account_info, "GET", url)
    if response is None:
        log(f"{YELLOW}Could not retrieve profile after retries; continuing without residency/loyalty discounts{RESET}")
        return None, None, 0
    payload = response.json().get("payload") or {}

    state = None
    loyalty_number = None
    c_and_a_shared_points = 0

    address = payload.get("contactInformation", {}).get("address", {})
    if address.get("residencyCountryCode") in ("USA", "CAN"):
        state = address.get("state")

    # Pull the loyalty information from the profile
    loyalty = payload.get("loyaltyInformation") or {}
    captains_club_ID = loyalty.get("captainsClubId")
    c_and_a_number = loyalty.get("crownAndAnchorId")
    c_and_a_level = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
    # "or 0" guards explicit JSON nulls: .get(key, 0) only defaults when the key
    # is absent, and a null value here becomes a TypeError in the > and >=
    # comparisons downstream (including the dp340 eligibility check)
    c_and_a_points = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints", 0) or 0
    c_and_a_shared_points = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints", 0) or 0

    # Get and display Royal Caribbean (Crown & Anchor and Club Royale) information
    if c_and_a_number and c_and_a_shared_points > 0:
        log(f"\tC&A: {c_and_a_number} {c_and_a_level} - {c_and_a_shared_points} Shared Points ({c_and_a_points} Individual Points)")

        total_nights, total_trips = get_number_of_nights(account_info, c_and_a_number, brand="royal")
        if total_nights > 0:
            log(f"\tTotal Trips on Royal: {total_trips} - Total Nights: {total_nights}")

        # Club Royale tier currently is not part of the loyalty payload; use a helper to compute it
        # but keep the payload check in case it ever comes back (key name may need to change)
        casino_points = loyalty.get("clubRoyaleLoyaltyIndividualPoints",0) or 0
        club_royale_loyalty_tier = loyalty.get("clubRoyaleLoyaltyTier") or get_club_royale_tier(casino_points)
        if club_royale_loyalty_tier:
            log(f"\tCasino Royale Tier: {club_royale_loyalty_tier} - {casino_points} Credits")

    # Get and display Celebrity (Captain's Club and Blue Chip) information
    cc_level = None
    cc_individual = 0
    if captains_club_ID:
        cc_level = loyalty.get("captainsClubLoyaltyTier")
        cc_individual = loyalty.get("captainsClubLoyaltyIndividualPoints", 0)
        cc_shared = loyalty.get("captainsClubLoyaltyRelationshipPoints", 0)
        log(f"\tCaptain's Club Number: {captains_club_ID} {cc_level} TIER ({cc_shared} Shared Points, {cc_individual} Individual Points)")

        total_nights, total_trips = get_number_of_nights(account_info, captains_club_ID, brand="celebrity")
        if total_nights > 0:
            log(f"\tTotal Trips on Celebrity: {total_trips} - Total Nights: {total_nights}")

        celebrity_blue_chip_loyalty_tier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        if celebrity_blue_chip_loyalty_tier != "Unknown":
            celebrity_blue_chip_loyalty_individual_points = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints",0)
            log(f"\tBlue Chip Tier: {celebrity_blue_chip_loyalty_tier} - {celebrity_blue_chip_loyalty_individual_points} Points")

    # Return the correct loyality number based on the account being used
    loyalty_number_to_use = captains_club_ID if account_info.is_celebrity else c_and_a_number

    # History-layer snapshot only (bookings.loyalty_tier / loyalty_points) - stashed
    # directly on account_info since it's already in hand; not used in any
    # discount/alert calculation and not part of this function's return contract
    account_info.loyalty_tier = cc_level if account_info.is_celebrity else c_and_a_level
    account_info.loyalty_points = cc_individual if account_info.is_celebrity else c_and_a_points

    # Return Royal Crown and Anchor shared points to determine if eligible for dp340
    return state, loyalty_number_to_use, c_and_a_shared_points


def get_checkin_info(account_info: AccountInfo,
                     reservationId: str,
                     passenger_ID: str,
                     ship_code: str,
                     sail_date: str,
                     apobj: Optional[Apprise]
) -> Tuple[str, Optional[datetime]]:
    """
    Retrieves mandatory pre-cruise check-in statuses and digital health manifest timelines.

    Queries check-in tracking endpoints to verify if passengers have completed passport data entry,
    selected their physical arrival times, or if their profile documents are still pending review.

    Returns:
        Tuple[str, Optional[datetime]]: A short check-in label for the end-of-run summary
        table (e.g. the opening date, "Open now", or "") and a datetime to sort it by
        (the check-in opening moment, or None when there is nothing dated to show).
    """
    url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v3/ships/voyages/{ship_code}{sail_date}/enriched'
    response = _execute_api_request(account_info, "GET", url, timeout=SHORT_REQUEST_TIMEOUT)
    if response is None:
        return "", None
    payload = response.json().get("payload")
    if not payload:
        return "", None

    sailing_info = payload.get("sailingInfo")
    if not sailing_info:
        return "", None

    is_checkin_available = sailing_info[0].get("isCheckinAvailable")
    check_window_open_start_date_time = sailing_info[0].get("checkWindowOpenStartDateTime")

    if is_checkin_available:
        log(f"{RED}Check In Available! Fetching boarding documentation data...{RESET}")

        checkin_statuses = get_checkin_statuses(account_info, reservationId, passenger_ID)

        assigned_window = "Not Selected"
        for guest in checkin_statuses:
            if str(guest.get("guestId")) == str(passenger_ID):
                arrival_time = guest.get("appointmentTime") or guest.get("appointmentDepartureTime") or "Not Selected"
                assigned_window = arrival_time
                status = guest.get("onlineCheckinStatus", "NOT_STARTED")
                log(f"\tPassenger Check-In Status: {status}")
                log(f"\tAssigned Boarding Window: {arrival_time}")

        summary = "Open now" if assigned_window == "Not Selected" else f"Open (window {assigned_window})"
        return summary, None

    # Check-in not yet open: surface the future opening date if the API has released it
    if check_window_open_start_date_time:
        # The API gives a UTC timestamp like "2027-03-26T00:00:00.000Z";
        # convert it to local time and show date + time in the configured
        # display format, falling back to the raw date if parsing fails
        try:
            dt = datetime.fromisoformat(check_window_open_start_date_time.replace("Z", "+00:00"))
            local_dt = dt.astimezone()
            opening_date = local_dt.strftime(config.date_display_format + " %X %Z")
            log(f"\tCheck-In opens on: {opening_date}")
            return f"Opens {local_dt.strftime(config.date_display_format + ' %I:%M %p')}", local_dt
        except Exception:
            opening_date = check_window_open_start_date_time.split('T')[0]
            log(f"\tCheck-In opens on: {opening_date}")
            return f"Opens {opening_date}", None

    log(f"\tCheck-In window opening date not yet released.")
    return "Not released", None


#
# Reservation Tracking and Data Scraping Functions #
#
def get_voyages(
    account_info: AccountInfo,
    discounts: CruiseURLParams,
    ship_dictionary: ShipRegistry,
    payment_tracker: Optional[CheckinPaymentTracker] = None,
    collected_watch_rows: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Extracts all current, valid upcoming cruise bookings linked to an active account profile.

    Submits account tokens to retrieve profile booking manifests. For each identified
    reservation, it parses ship names, evaluates deadlines, loops through cabin passengers,
    tracks addon planner purchases, and coordinates live cabin pricing checks.
    """
    # Gather the variables we need from the data classes
    access_token = account_info.access.token
    account_id = account_info.access.id
    session = account_info.access.session

    # Pull the needed items from the global config
    apobj = notifier_for(account_info)
    watch_list_items = config.watch_list
    display_cruise_prices = config.display_cruise_prices
    reservation_price_paid = config.reservation_prices
    reservation_friendly_names = config.reservation_names
    show_promos = config.show_promos
    date_display_format = config.date_display_format

    loyalty_number = discounts.loyalty_number
    state = discounts.state

    # Get the current bookings from the servier
    brand_code = "R" if account_info.is_royal else "C"
    params = {'brand': brand_code, 'includeCheckin': 'true'}
    url = f'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/{account_id}'
    response = _execute_api_request(account_info, "GET", url, params=params)
    if response is None:
        log(f"{YELLOW}Could not retrieve bookings after retries; skipping this account{RESET}")
        return
    bookings = response.json().get("payload", {}).get("profileBookings", [])

    for booking in bookings:
        # Pull out the individual booking fields
        reservation_ID = booking.get("bookingId")
        passenger_ID = booking.get("passengerId")
        sail_date = booking.get("sailDate")
        number_of_nights = int(booking.get("numberOfNights") or 0)
        ship_code = booking.get("shipCode")
        guests = booking.get("passengersInStateroom") or []
        package_code = booking.get("packageCode")
        booking_currency = booking.get("bookingCurrency")
        booking_office_country_code = booking.get("bookingOfficeCountryCode")
        stateroom_number = booking.get("stateroomNumber")
        amend_token = booking.get("amendToken")

        if not sail_date:
            continue

        # Translate room letter code
        stateroom_type_name = _parse_stateroom_type(booking.get("stateroomType"))

        # Unpack cabin occupants & boarding windows safely
        metrics = _calculate_passenger_metrics(guests, sail_date, booking, brand_code)

        # Preserve resolved GTY category code for downstream pricing checks
        if metrics.get("category_code") and not booking.get("stateroomCategoryCode"):
            booking["stateroomCategoryCode"] = metrics["category_code"]

        # Display Reservation Information Header
        reservation_display = f"Reservation #{reservation_ID}"
        if str(reservation_ID) in reservation_friendly_names:
            reservation_display += f" ({reservation_friendly_names.get(str(reservation_ID))})"
        log(f"\n{BLUE}{reservation_display}{RESET}")

        log(f"{config.format_date(sail_date)} {ship_dictionary.get_ship(ship_code)} Room {stateroom_number} (In this cabin: {metrics['passenger_names']})")

        # log Boarding Info or call fallback check-in handler, capturing a short
        # check-in label for the end-of-run summary table
        if metrics['checkin_string']:
            log(metrics['checkin_string'])
            checkin_label = f"Boarding {metrics.get('boarding_time')}" if metrics.get('boarding_time') else "Checked in"
        else:
            checkin_label, _ = get_checkin_info(account_info, reservation_ID, passenger_ID, ship_code, sail_date, apobj)

        # Process Dining Setup
        result = get_dining_and_prices(account_info, booking)
        dining_selection = result.get("dining_selection", [])
        for selection in dining_selection:
            if selection.get("sittingTime", "") == "MY TIME" or selection.get("sittingType", "") == "MY TIME":
                log("Dining: My Time Open Sitting")
            else:
                sitting_type = selection.get('sittingType', '')
                sitting_time = selection.get('sittingTime', '')
                dining_string = f"\tDining: {sitting_type} {sitting_time}"
                raw_table_size = str(selection.get("tableSize", "") or "")
                # tableSize can be a non-numeric code (e.g. "S") - only zero-pad digits
                padded_table = raw_table_size.zfill(2) if raw_table_size.isdigit() else raw_table_size
                if padded_table and padded_table != "00":
                    dining_string += f" Table Size: {padded_table}"
                log(dining_string)

        # Unpack Ledger Pricing Matrix
        payment_string = ""
        gross_totals = None
        prepaid_grats_flag = False
        insurance_flag = False
        all_included_flag = False
        discounted_fare = None
        taxes_and_fees = None
        casino_rate_flag = False
        refundabilities = set()
        booked_with_dp340 = False
        prepaid_addons = 0.0
        cruise_paid_price_from_API = result.get("prices", [])

        # Extract direct YAML overrides for this reservation ID (if configured)
        # The full reservation_price_paid structure will get extracted later
        yaml_payment_override = None
        if isinstance(reservation_price_paid, list):
            for res_entry in reservation_price_paid:
                if str(reservation_ID) == str(res_entry.get("reservation")):
                    yaml_payment_override = (
                        res_entry.get("finalPaymentDaysBeforeSailing")
                        or res_entry.get("finalPaymentDate")
                    )
                    break
        elif isinstance(reservation_price_paid, dict) and str(reservation_ID) in reservation_price_paid:
            res_entry = reservation_price_paid.get(str(reservation_ID))
            if isinstance(res_entry, dict):
                yaml_payment_override = (
                    res_entry.get("finalPaymentDaysBeforeSailing")
                    or res_entry.get("finalPaymentDate")
                )

        # The MARKET the guest bought in governs the payment window, not the
        # TA's own office country (a UK booking placed through a US agency
        # follows the UK 56-day rule) - but only codes MARKET_RULES knows
        # count, since Royal's market vocabulary is not all ISO (CHS).
        market_code = _booking_payment_market(booking)

        final_payment_override = (
            yaml_payment_override
            or booking.get("finalPaymentDaysBeforeSailing")
            or booking.get("finalPaymentDate")
        )

        final_payment_date = get_final_payment_date(
            number_of_nights,
            sail_date,
            market_code=market_code,
            final_payment_date_override=final_payment_override,
        )
        final_payment_date_display = final_payment_date.strftime(date_display_format)

        for cur_price in cruise_paid_price_from_API:
            price_type_code = cur_price.get("priceTypeCode", "")
            amount = cur_price.get("amount")

            # Club Royale casino-rate detection must run BEFORE the amount
            # guard: the OPTIONS record often carries amount 0.0 while its
            # priceItems still name the comp ("CASINO DISC - GOBO", "ClubR...")
            if price_type_code in ("DISCOUNT", "OPTIONS"):
                # This walk runs for every user (flag on or off) and the data
                # comes out of a Next.js RSC stream: priceItems can be a
                # reference string, or a list holding nulls/strings. Never let
                # an optional feature's bookkeeping end the run.
                price_items = cur_price.get("priceItems")
                for item in (price_items if isinstance(price_items, list) else []):
                    if not isinstance(item, dict):
                        continue
                    if CASINO_MARKER.search(str(item.get("description") or "")):
                        casino_rate_flag = True
                    # the DISCOUNT items carry the fare's refundability
                    # (DEPOSIT_NOT_REFUNDABLE = an NRD fare)
                    if isinstance(item.get("refundability"), str):
                        refundabilities.add(item["refundability"])
                    if item.get("promoCd") == "DP340":
                        booked_with_dp340 = True

            # captured BEFORE the amount guard: a fully comped fare is a
            # legitimate 0.0 and must still yield a fare + taxes basis
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                if price_type_code == "DISCOUNTED_CRUISE_FARE":
                    discounted_fare = amount
                elif price_type_code == "TAXES_AND_FEES":
                    taxes_and_fees = amount

            if not amount:
                continue

            # Parse the price gathered from the server
            if price_type_code == "GROSS_TOTALS":
                gross_totals = amount
            elif price_type_code == "GRATUITIES":
                prepaid_grats_flag = True
                prepaid_addons += amount if isinstance(amount, (int, float)) else 0.0
                payment_string += f" Including: {amount:.2f} Gratuities"
            elif price_type_code == "TRIP_INSURANCE":
                insurance_flag = True
                prepaid_addons += amount if isinstance(amount, (int, float)) else 0.0
                payment_string += f" Including: {amount:.2f} Insurance"
            elif "ALL_INC" in price_type_code or "INCLUDED" in price_type_code:
                all_included_flag = True
                prepaid_addons += amount if isinstance(amount, (int, float)) else 0.0
                payment_string += f" Including: {amount:.2f} All Included Drinks/WiFi"
            elif price_type_code == "BALANCE_DUE":
                payment_string += f" {YELLOW}You Still Owe: {amount:.2f} due {final_payment_date_display}{RESET}"

        # Store the parsed information into a dictionary for easy passing around
        paid_price_struct = {}
        if gross_totals is not None:
            paid_price_struct['reservation'] = reservation_ID
            paid_price_struct['paid_price'] = gross_totals
            paid_price_struct['gratuities'] = prepaid_grats_flag
            # NOTE: keys must match what CruiseURLParams.apply_overrides reads
            # (camelCase) - the old snake_case spellings were silently ignored,
            # so insured / all-included bookings compared against a cheaper
            # base fare and fired false "Rebook!" alerts
            paid_price_struct['tripInsurance'] = insurance_flag
            paid_price_struct['allInUpgrade'] = all_included_flag
            # checkForUpgrades basis: a reprice keeps prepaid add-ons, so the
            # honest "vs what you paid" comparison is fare + taxes, not the
            # gross total (which bundles prepaid gratuities/packages)
            if isinstance(discounted_fare, (int, float)) and isinstance(taxes_and_fees, (int, float)):
                paid_price_struct['fareAndTaxes'] = round(discounted_fare + taxes_and_fees, 2)
            paid_price_struct['isCasino'] = casino_rate_flag
            paid_price_struct['isAgency'] = is_agency_booking(booking)
            if "DEPOSIT_NOT_REFUNDABLE" in refundabilities:
                paid_price_struct['depositType'] = "NRD"
            elif "REFUNDABLE" in refundabilities:
                paid_price_struct['depositType'] = "REFUNDABLE"
            paid_price_struct['bookedWithDP340'] = booked_with_dp340
            # a configured reservationPricePaid is entered INCLUDING these (per
            # the docs); the upgrade rows are bare cabin totals
            paid_price_struct['prepaidAddOns'] = round(prepaid_addons, 2)
            log(f"Cruise Fare - Total {gross_totals:.2f}{payment_string}")

        # Record this booking for the end-of-run check-in / final-payment summary table.
        # Include the room number so multiple cabins on the same sailing are distinct.
        ship_name = ship_dictionary.get_ship(ship_code)
        summary_name = ship_name
        if stateroom_number:
            summary_name += f" ({stateroom_number})"
        summary_reservation = str(reservation_ID)
        if summary_reservation in reservation_friendly_names:
            summary_reservation += f" ({reservation_friendly_names.get(summary_reservation)})"

        balance_due = derive_balance_due(booking, cruise_paid_price_from_API)
        balance_due_amount = booking.get("balanceDueAmount")
        if str(reservation_ID) in config.paid_reservations:
            balance_due = False   # user vouches for it (reservationsPaidInFull)

        past_final_payment = date.today() > final_payment_date
        if payment_tracker is not None:
            payment_tracker.record_row({
                    "name": summary_name,
                    "reservation": summary_reservation,
                    "sail_date": sail_date,
                    "checkin_label": checkin_label or "TBD",
                    "final_payment": final_payment_date,
                    "past_final_payment": past_final_payment,
                    "balance_due": balance_due,
                    "dedupe_key": f"{reservation_ID}|{sail_date}",
        })

        # Snapshot this booking for the history layer (opt-in, no-op when disabled).
        # 1/0/NULL tri-state matches the summary table's balance_due handling above.
        guests_json = json.dumps([
            {
                "name": g.get("firstName", "").capitalize(),
                "id": g.get("passengerId"),
                "age_bracket": "adult" if above_age_on_sail_date(g.get("birthdate"), sail_date, 12) else "child",
            }
            for g in guests
        ])
        history.record_booking(
            reservation_id=str(reservation_ID),
            ship_code=ship_code,
            ship_name=ship_name,
            sail_date=sail_date,
            nights=number_of_nights,
            stateroom_type=stateroom_type_name,
            stateroom_number=stateroom_number if stateroom_number and stateroom_number != "GTY" else None,
            stateroom_category=metrics.get('category_code'),
            guest_count=len(guests),
            guests_json=guests_json,
            loyalty_tier=account_info.loyalty_tier,
            loyalty_points=account_info.loyalty_points,
            checkin_label=checkin_label or "TBD",
            final_payment_date=final_payment_date.strftime("%Y%m%d"),
            past_final_payment=int(past_final_payment),
            balance_due={True: 1, False: 0}.get(balance_due),
            booking_currency=booking_currency,
            friendly_name=reservation_friendly_names.get(str(reservation_ID)),
            account_label=account_info.username,
        )

        if balance_due is True:
            owed = (f"{balance_due_amount:.2f}" if isinstance(balance_due_amount, (int, float))
                    else "unknown")
            log(YELLOW + f"Remaining net-to-line balance {owed} due {final_payment_date_display} (Difference is TA's commission/fronted deposit)" + RESET)

        paid_price_struct['booked_obc'] = get_OBC(account_info, booking)

        if show_promos:
            get_all_promotions(account_info, booking)

        # Current Web Market Pricing Block
        if display_cruise_prices:
            # Build the complex Checkout/Room Selection URL
            has_api_category = bool(metrics.get("category_code") or metrics.get("sub_type"))

            # Map legacy manual pricing text overrides from configuration yaml
            if isinstance(reservation_price_paid, dict) and reservation_price_paid:
                if str(reservation_ID) in reservation_price_paid:
                    paid_price = reservation_price_paid.get(str(reservation_ID))
                    if isinstance(paid_price, dict):
                        # dict-of-dicts shape - the same entries the payment
                        # override reads finalPaymentDaysBeforeSailing from;
                        # float(dict) crashed the whole run here
                        paid_price = paid_price.get("paidPrice",
                                                    paid_price.get("paid_price"))
                    if paid_price is not None:
                        paid_price_struct['paid_price'] = float(paid_price)
                        # a manually configured price is a deliberate statement
                        # (e.g. the documented change-fee cushion) - the upgrade
                        # table's dl-paid basis must honor it over the ledger
                        paid_price_struct['paidPriceOverridden'] = True
            elif isinstance(reservation_price_paid, list):
                for reservation in reservation_price_paid:
                    # str-compare: a missing/non-numeric 'reservation' key must
                    # not crash the whole booking loop
                    if str(reservation_ID) == str(reservation.get("reservation")):
                        for key, val in reservation.items():
                            if key == "paidPrice":
                                paid_price_struct["paid_price"] = float(val) if val is not None else None
                                if val is not None:
                                    paid_price_struct['paidPriceOverridden'] = True
                            else:
                                paid_price_struct[key] = val

            if booking.get("stateroomType") != "NONE":
                has_override = bool(paid_price_struct.get("categoryOverride") or paid_price_struct.get("subcategoryOverride"))
                if not (has_api_category or has_override):
                    log(YELLOW + "No stateroom category code could be resolved from API payload." + RESET)
                    log(YELLOW + "Please set categoryOverride in your config YAML for this reservation." + RESET)

                get_cruise_price(account_info,
                                 booking,
                                 ship_dictionary,
                                 automatic_URL=True,
                                 paid_price_struct=paid_price_struct,
                                 discounts=discounts)
            else:
                log(YELLOW + "Cannot Check Cruise Price - Use Manual URL Method" + RESET)

        # Get the extra add-ons purchased for this voyage
        get_orders(account_info, booking, collected_watch_rows=collected_watch_rows)
        log(" ")

        # Process watchlists on a per-occupant layout instead of per-booking line
        if watch_list_items:
            for guest in guests:
                passenger_info = {
                   "passenger_ID": guest.get("passengerId"),
                   "passenger_name": guest.get("firstName", "").capitalize(),
                   "room": guest.get("stateroomNumber") or stateroom_number
                }

                # Handle any watch list items for this guest's booking
                process_watch_list_for_booking(
                    account_info,
                    booking,
                    watch_list_items,
                    apobj,
                    passenger_info,
                    collected_watch_rows=collected_watch_rows
                )

            log(" ")

    return bookings


def get_dining_and_prices(account_info: AccountInfo, booking: Dict[str, Any]) -> Dict[str, List[Any]]:
    """
    Extracts explicit reservation pricing details and dining choices from booked summaries.

    Queries specific reservation components using transient amendment keys. Implements
    safety fallbacks to return blank lists if network timeouts or structural processing
    faults occur, ensuring downstream processes don't break.
    """
    # Safely pull the token and country straight from the booking payload.
    # Prefer the market country (matches the booking currency) over the TA's
    # office country -- see _booking_country_code().
    amendtoken = booking.get("amendToken")
    country = _booking_country_code(booking) or "USA"

    RSC_URL = f"https://www.{account_info.url_brand}.com/usa/en/booked/overview"

    # MAINTENANCE NOTE: The 'RSC: 1' header signals the web server that this is a
    # Next.js React Server Component call. It forces the endpoint to yield backend raw data
    # state structures instead of rendering a full human-readable HTML web page.
    HEADERS = {
        "User-Agent": USER_AGENT_WEB,
        "Accept": "text/x-component",
        "RSC": "1",
    }

    # Make the request to the servers
    resp = _execute_api_request(
        account_info=account_info,
        method="GET",
        url=RSC_URL,
        params={"token": amendtoken, "country": country},
        headers=HEADERS,
        on_failure="retry"
    )

    if resp is None:
        return {"dining_selection": [], "prices": [], "pricing_add_ons": []}

    text = resp.text
    result = {}

    result["dining_selection"] = _extract_json_array(text, "diningSelection") or []
    result["prices"] = _extract_json_array(text, "prices") or []
    result["pricing_add_ons"] = _extract_json_array(text, "pricingAddOns") or []

    return result


def _maybe_report_upgrades(*args: Any, **kwargs: Any) -> None:
    """An optional, informational feature must never end a run: any unexpected
    data shape inside the upgrade report is logged and that booking's table is
    skipped - remaining bookings, accounts and the summary are unaffected."""
    try:
        _report_upgrades(*args, **kwargs)
    except Exception as exc:
        log(f"\t{YELLOW}Upgrade check skipped for this booking (unexpected data: "
            f"{type(exc).__name__}: {exc}){RESET}")


def _report_upgrades(url_params: CruiseURLParams, results: Dict[str, Any],
                     paid_price_struct: Optional[Dict[str, Any]],
                     pre_string: str, reservation_id: Optional[str],
                     apobj: Optional[Apprise],
                     past_final_payment: bool = False) -> None:
    """
    checkForUpgrades: for a booked cruise, list what the other staterooms on the
    sailing cost right now, as ONE delta column - the one that governs this
    booking: dl-paid (vs what was paid) for a normal booking, dl-rate (vs the
    booked category's rate today) for a casino/comped one.

    Rows come from the room-selection sweep the availability gate already
    performs (collect_all), so the table itself adds no API requests; the
    booked family's per-category prices cost one extra POST per booking unless
    upgradeSisterCategories is false.
    """
    rows = [r for r in (results.get('upgrade_rows') or []) if isinstance(r, dict)]
    if not rows:
        return
    struct = paid_price_struct or {}
    currency = url_params.currency_code or "USD"
    sym = "$" if currency == "USD" else ""          # never print "$" on a GBP booking
    is_casino = bool(struct.get('isCasino'))

    def money(v: Optional[float]) -> str:
        return _upgrade_money(v, sym)

    # ---- the booked row: dl-rate anchor + class rank -------------------------
    booked_sub = url_params.stateroom_subtype
    booked_cat = url_params.stateroom_category_code
    booked_row = next((r for r in rows if r.get('subtype') == booked_sub), None)
    booked_now = (booked_row.get('price')
                  if booked_row and _is_price(booked_row.get('price')) else None)
    booked_rank = (TYPE_RANK.get(booked_row.get('type')) if booked_row
                   else TYPE_RANK.get(url_params.cabin_class_string))

    # ---- per-category prices inside the booked family (one extra POST) -------
    # DP340 eligibility is mainline's own decision (shared C&A points), already
    # expressed as url_params.coupon_code - never re-derive it here, and never
    # retry a coupon the main flow just proved rejected.
    guest_count = int(url_params.number_of_adults or 0) + int(url_params.number_of_children or 0)
    apply_dp340 = (not results.get('coupon_rejected')
                   and should_apply_dp340(url_params.coupon_code == "DP340",
                                          bool(struct.get('bookedWithDP340')), guest_count))
    family_prices: Dict[str, float] = {}
    dp340_used = False
    if booked_row is not None and config.upgrade_sister_categories is not False:
        family_prices, dp340_used = _get_upgrade_category_prices(
            url_params, booked_row.get('type'), dp340=apply_dp340)
        family_prices = {code: total for code, total in (family_prices or {}).items()
                         if _is_price(total)}

    # ---- label the dl-rate anchor honestly -----------------------------------
    rate_anchor_label = None
    if booked_row is not None:
        if booked_cat and booked_cat in family_prices:
            booked_now = family_prices[booked_cat]      # exact category beats the lead-in
            rate_anchor_label = f"your booked category {booked_cat} today"
        elif family_prices and booked_cat:
            rate_anchor_label = f"family lead-in; {booked_cat} returned no price today"
        elif booked_cat and booked_row.get('category') == booked_cat:
            rate_anchor_label = f"your booked category {booked_cat} today"
        else:
            rate_anchor_label = "your booked family's lead-in category today"
    else:
        # No row of its own (a GTY booking - casino comps very often are): anchor
        # on the cheapest guarantee of the booked class rather than an all-dash table.
        # Royal flags ordinary subtypes as guarantees too (seen live for solos: a
        # studio balcony) - a lesser product would understate the booked rate
        same_class_gty = [r for r in rows
                          if r.get('guarantee') and r.get('type') == url_params.cabin_class_string
                          and _is_price(r.get('price'))
                          and not LESSER_PRODUCT.search(r.get('display_name') or "")]
        if same_class_gty:
            booked_now = min(r['price'] for r in same_class_gty)
            rate_anchor_label = (f"cheapest {url_params.cabin_class_string} guarantee today "
                                 f"(your booking has no category row of its own)")

    # Same-class "pricier than what you hold" alerts need the EXACT booked
    # category's rate; with a stand-in anchor only class jumps may alert
    anchor_exact = bool(booked_row and booked_cat and
                        (booked_cat in family_prices
                         or booked_row.get('category') == booked_cat))

    # The main check's checkout POST has ALREADY priced the exact booked
    # category - it is the "now X" printed on the line above this table - and
    # that is the authoritative anchor. Seen live: the rooms API omitted a
    # booked category that checkout priced fine, so the table anchored on a
    # lead-in and claimed "returned no price" directly under the "now" figure.
    fare_key = "all_included_fare" if url_params.all_included else "base_fare"
    checkout_fare = (results.get(fare_key) or {}).get("fare")
    family_display: Dict[str, float] = dict(family_prices)
    if _is_price(checkout_fare):
        booked_now = float(checkout_fare)
        anchor_exact = True
        rate_anchor_label = (f"your booked category {booked_cat} today" if booked_cat
                             else "your booked cabin today")
        if url_params.refundable:
            # the main line above prints the REFUNDABLE fare for this booking;
            # the anchor is the non-refundable one so it compares like-for-like
            # with the rows - say so, since the two numbers differ
            rate_anchor_label += " at the non-refundable rate, to match the rows"
        # make sure the exact booked category has a (starred) row of its own
        if booked_row is not None and booked_cat:
            lead_cat = booked_row.get('category')
            if (not family_display and lead_cat and lead_cat != booked_cat
                    and _is_price(booked_row.get('price'))):
                family_display[lead_cat] = booked_row['price']     # keep the lead-in visible
            if family_display or lead_cat != booked_cat:
                family_display[booked_cat] = float(checkout_fare)
    alert_booked_now = booked_now if anchor_exact else None

    # ---- which quotes carry the DP340 discount -------------------------------
    # The main check's checkout price does when it priced with the coupon;
    # booked-family rows do only when the family request KEPT the code (it
    # retries without it when refused); sweep rows of other families never do.
    # A discounted anchor against an undiscounted row (or the reverse) makes a
    # cheaper tier look pricier, and 'pricier' is what marks a same-class upgrade.
    if _is_price(checkout_fare):
        anchor_discounted = url_params.coupon_code == "DP340" and not results.get('coupon_rejected')
    else:
        anchor_discounted = bool(dp340_used and booked_cat and booked_cat in family_prices)

    # ---- dl-paid basis: configured price > ledger fare+taxes > gross ---------
    fare_and_taxes = struct.get('fareAndTaxes')
    user_paid = struct.get('paid_price') if struct.get('paidPriceOverridden') else None
    addons = struct.get('prepaidAddOns')
    if isinstance(user_paid, (int, float)):
        # docs tell users to enter the price INCLUDING prepaid gratuities etc.,
        # but the rows below are bare cabin totals - put both on one basis
        if isinstance(addons, (int, float)) and addons > 0:
            paid_basis = round(user_paid - addons, 2)
            basis_label = (f"your configured reservationPricePaid less "
                           f"{money(addons)} prepaid add-ons")
        else:
            paid_basis = user_paid
            basis_label = "your configured reservationPricePaid (may include prepaid add-ons)"
    elif isinstance(fare_and_taxes, (int, float)):
        paid_basis = fare_and_taxes
        basis_label = "fare + taxes paid; prepaid add-ons excluded"
    else:
        paid_basis = struct.get('paid_price')
        basis_label = "gross paid (fare+taxes unavailable)"
    has_paid = isinstance(paid_basis, (int, float))

    # ---- which single delta governs this booking -----------------------------
    # A normal booking reprices for the dl-paid difference; a casino/comped one
    # can't reprice without forfeiting the comp, so the desk's category
    # difference (dl-rate) governs. One column only - two read as equals.
    prefer_rate = is_casino or not has_paid
    casino_without_anchor = False
    if prefer_rate and not isinstance(booked_now, (int, float)) and has_paid:
        prefer_rate = False                 # nothing to anchor dl-rate on
        casino_without_anchor = is_casino
    delta_label = 'dl-rate' if prefer_rate else 'dl-paid'

    # ---- table rows ----------------------------------------------------------
    # Guarantee and connecting rows ARE listed (tagged): a guarantee is often the
    # cheapest way up a class, and a connecting cabin is a real bookable cabin.
    def listable(r: Dict[str, Any]) -> bool:
        if r is booked_row:
            return True                     # always show the booked family
        if r.get('rooms_left') == 0:        # explicit 0 only; None = count unknown
            return False
        return _is_price(r.get('price'))

    def product_tags(r: Dict[str, Any]) -> List[str]:
        return (["[GTY]"] if r.get('guarantee') else []) + \
               (["[connecting]"] if r.get('connecting') else [])

    table_rows: List[Dict[str, Any]] = []
    for r in rows:
        if not listable(r):
            continue
        if r is booked_row and family_display:
            table_rows.extend({**booked_row, 'category': code, 'price': total,
                               '_dp340': bool(dp340_used and code in family_prices)}
                              for code, total in sorted(family_display.items(),
                                                        key=lambda kv: kv[1]))
        elif _is_price(r.get('price')):
            table_rows.append(r)
    if not table_rows:
        log("\tUpgrade check: no priceable stateroom inventory returned for this sailing")
        return

    BOLD = "\033[1m"
    cur_note = "" if sym else f" - amounts in {currency}"
    log(f"\t{BLUE}Stateroom options on this sailing (priced for this booking's guests){cur_note}{RESET}")
    if prefer_rate:
        log(f"\t  dl-rate basis: {money(booked_now)} ({rate_anchor_label or 'no comparable rate row'})")
    else:
        log(f"\t  dl-paid basis: {money(paid_basis)} ({basis_label})")
    header = f"\t  {'':1} {'cat':5} {'type':9} {'now':>12} {delta_label:>12}  description"
    log(header)
    log("\t  " + "-" * (len(header) - 4))

    deposit_type = struct.get('depositType')

    def fare_type_mismatch(r: Dict[str, Any]) -> str:
        """'' when the row's deposit type matches the booking's (or is unknown)."""
        row_refund = r.get('refundability')
        if deposit_type == "NRD" and isinstance(row_refund, str) and row_refund != "DEPOSIT_NOT_REFUNDABLE":
            return "[refundable rate]"
        if deposit_type == "REFUNDABLE" and row_refund == "DEPOSIT_NOT_REFUNDABLE":
            return "[NRD rate]"
        return ""

    # A per-row tag only informs when the table is MIXED. When every row is on
    # the other fare type (live: a booking whose ledger reads refundable while
    # all 13 quotes were NRD rates) the tags are pure noise - one note says it
    # instead. On a comped casino fare the comparison is moot altogether.
    mismatches = [fare_type_mismatch(r) for r in table_rows]
    all_mismatch = bool(mismatches) and all(mismatches)
    tag_rows = not is_casino and any(mismatches) and not all_mismatch
    hits: List[str] = []
    any_cheaper = False
    threshold = config.upgrade_alert_below if isinstance(config.upgrade_alert_below, (int, float)) else None
    for r in table_rows:
        is_booked_cat = (r.get('subtype') == booked_sub
                         and (not family_display or r.get('category') == booked_cat))
        anchor_price = booked_now if prefer_rate else paid_basis
        delta = (r['price'] - anchor_price) if isinstance(anchor_price, (int, float)) else None
        if delta is not None and delta < 0 and not is_booked_cat:
            any_cheaper = True
        # past final payment a cheaper category returns NO refund
        shown = max(delta, 0.0) if (delta is not None and past_final_payment) else delta

        product = product_tags(r)
        fare_tag = fare_type_mismatch(r) if tag_rows else ""
        tags = "".join(f"  {t}" for t in product + ([fare_tag] if fare_tag else []))

        log(f"\t  {'*' if is_booked_cat else ' '} {str(r.get('category') or r.get('subtype')):5} "
            f"{str(r.get('type')):9} {money(r['price']):>12} {_upgrade_delta(shown, 12, sym)}  "
            f"{r.get('display_name', '')}{tags}")

        # Within the booked class "pricier" is no proxy for "better" on these
        # products (no cabin choice / same cabin with a door): they alert only
        # as a move UP a class, which withholding the same-class anchor enforces
        # The same goes for a row quoted on a different DP340 basis than the anchor.
        same_basis = bool(r.get('_dp340')) == bool(anchor_discounted)
        same_class_anchor = alert_booked_now if (same_basis and not product) else None
        if (threshold is not None and not is_booked_cat and shown is not None
                and shown <= threshold
                and is_upgrade_candidate(booked_rank, same_class_anchor,
                                         TYPE_RANK.get(r.get('type')), r['price'],
                                         r.get('display_name') or "")):
            sign = "+" if shown > 0 else "-" if shown < 0 else ""
            # unlike the table (where an all-mismatch table gets one note instead),
            # an alert stands alone: always say when the quote is on the other fare type
            fare_hit = "" if is_casino else fare_type_mismatch(r)
            hit_tags = "".join(f" {t}" for t in product + ([fare_hit] if fare_hit else []))
            hits.append(f"{r.get('category') or r.get('subtype')} {r.get('display_name', '')}"
                        f"{hit_tags} for {sign}{sym}{abs(shown):,.2f} (now {money(r['price'])})")

    # ---- notes ---------------------------------------------------------------
    tag_notes = []
    if any(r.get('guarantee') for r in table_rows):
        tag_notes.append("[GTY] = guarantee fare: the cruise line assigns the cabin and "
                         "its location - you can't choose it")
    if any(r.get('connecting') for r in table_rows):
        tag_notes.append("[connecting] = has a door to the adjoining cabin")
    if tag_notes:
        log("\t  " + "; ".join(tag_notes) + ".")
    if booked_row is not None and not anchor_exact:
        if family_display and booked_cat:
            log(f"\t  Your category {booked_cat} returned no price today (it may be sold out "
                f"within its family) - no row is starred.")
        elif booked_cat:
            log(f"\t  * marks your booked family's lead-in row; your exact category "
                f"{booked_cat} was not priced individually.")
    if struct.get('bookedWithDP340'):
        log("\t  DP340 single-supplement discount is applied on this booking")
    if dp340_used:
        log("\t  Booked-family rows are quoted with the DP340 single-supplement code; "
            "other families show standard solo rates.")
    elif apply_dp340 and family_prices:
        log("\t  The DP340 single-supplement code was not accepted for the booked-family quote: "
            "every row shows standard solo rates.")

    if is_casino:
        log(f"\t  {YELLOW}Note: casino-rate booking - a straight reprice (dl-paid) would forfeit "
            f"the comp; {BOLD}dl-rate{RESET}{YELLOW} approximates the category difference a casino "
            f"desk charges to move UP. A cheaper category returns nothing on a comped fare. "
            f"Confirm with the casino desk - or your travel agent, if one booked this comp - "
            f"before changing anything.{RESET}")
        if casino_without_anchor:
            log(f"\t  {YELLOW}No comparable rate row was returned for this booking, so dl-paid "
                f"is shown instead - treat it as a rough guide only.{RESET}")
    elif has_paid:
        log(f"\t  Upgrading or downgrading would use {BOLD}dl-paid{RESET} - "
            f"the difference between a category's price today and what you paid.")
        log("\t  An upgrade reprices the booking at today's promotion: your original "
            "promotions/onboard credit are replaced, not kept.")
        if any_cheaper and not past_final_payment:
            log("\t  Cheaper rows are effectively reprices: when today's sale is marked "
                "'new bookings only' Royal may refuse an in-place reprice, leaving "
                "cancel-and-rebook (which puts a non-refundable deposit at risk).")
    if past_final_payment:
        log(f"\t  {YELLOW}Past final payment: upgrades are still possible at today's rates, "
            f"but a cheaper category returns no refund (shown as {sym}0.00).{RESET}")

    # Two live data points: is_agency_booking() fired for a comp a TA really
    # booked AND for one booked directly with the casino - on a casino-rate
    # booking the flag cannot tell them apart. So no separate TA note there;
    # the casino note is worded to be true either way.
    if struct.get('isAgency') and not is_casino:
        log(f"\t  {YELLOW}TA/group booking: figures are Royal's ledger - your agent's own fees "
            f"or discounts aren't visible here, and any reprice or upgrade goes through your "
            f"TA (who may charge their own change fee).{RESET}")

    quotes_nrd = any(r.get('refundability') == "DEPOSIT_NOT_REFUNDABLE" for r in table_rows)
    if deposit_type == "NRD" and not is_casino:
        if url_params.is_royal and currency == "USD":
            log(f"\t  {YELLOW}NRD fare notes:{RESET} category changes on this same ship/sail date "
                f"(including downgrades) have no change fee and keep your deposit. Reprices must "
                f"stay on a non-refundable fare{' (the prices above are NRD rates)' if quotes_nrd else ''}. "
                f"Changing ship or sail date costs $100/person; cancelling forfeits the deposit.")
        else:
            log(f"\t  {YELLOW}NRD fare notes:{RESET} category changes on the same ship/sail date "
                f"generally keep your deposit, and reprices must stay on a non-refundable fare; "
                f"ship/date changes and cancellations carry the penalties in your booking terms.")
    elif deposit_type == "REFUNDABLE" and quotes_nrd and not is_casino:
        which = "rows tagged [NRD rate] are" if tag_rows else "the prices above are"
        log(f"\t  Note: {which} non-refundable-deposit prices - matching one "
            f"may require switching this refundable booking to NRD (allowed before final "
            f"payment; the switch is one-way).")
    if deposit_type == "NRD" and all_mismatch and not is_casino:
        log("\t  Note: the prices above are refundable-deposit rates - an NRD booking can only "
            "reprice onto a non-refundable fare, so these may not be available as priced.")
    if threshold is not None and booked_rank is None:
        log(f"\t  {YELLOW}Booked class unknown - upgrade alerts skipped for this booking.{RESET}")

    if hits:
        basis_tag = (f"category difference vs {rate_anchor_label}" if prefer_rate
                     else f"vs {basis_label}")
        body = (f"{len(hits)} upgrade option(s) at or below {sym}{threshold:,.2f} for "
                f"{pre_string} #{reservation_id or '?'} ({basis_tag}):\n"
                + "\n".join(f"- {h}" for h in hits))
        if any("[refundable rate]" in h for h in hits):
            body += ("\n[refundable rate] = quoted on a refundable-deposit fare; your NRD booking can "
                     "only move onto a non-refundable fare, so it may not be available as priced.")
        if any("[NRD rate]" in h for h in hits):
            body += ("\n[NRD rate] = quoted on a non-refundable-deposit fare; taking it means "
                     "switching this refundable booking to NRD (allowed before final payment, one-way).")
        log(f"\t{RED}{body}{RESET}")
        if apobj is not None:
            apobj.notify(body=body, title='Cruise Upgrade Opportunity', body_format=NotifyFormat.TEXT)


class CabinAvailabilityError(Exception):
    """A cabin availability notification or its persistent state failed."""


@contextmanager
def cabin_state_lock(path: Path):
    """Lock a stable sidecar across read/notify/replace, including other processes."""
    # Locking the JSON itself would lose protection when os.replace swaps its inode.
    # OS locks are released on process exit; leave the sidecar in place, even empty.
    with open(str(path) + ".lock", "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read_cabin_state(path: Path) -> dict:
    """Missing state starts fresh; invalid existing state must never reset alerts."""
    try:
        with path.open(encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(state, dict) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("url"), str)
        or type(row.get("available")) is not bool
        or type(row.get("notified")) is not bool
        or (row["notified"] and not row["available"])
        for row in state.values()
    ):
        raise ValueError("Invalid cabin availability state")
    return state


def write_cabin_state(path: Path, state: dict) -> None:
    """Replace a complete JSON file atomically; retain the old file on failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def notify_cabin_availability(params: CruiseURLParams, result: dict, url: str,
                              ships: ShipRegistry, notifier: Optional[Apprise], scope: str) -> None:
    """Persist latest availability; acknowledge only successful notifications."""
    available = result.get("room_available")
    label = f"{config.format_date(params.sail_date)} {ships.get_ship(params.ship_code)} {params.cabin_class_string} {params.stateroom_subtype}"
    if available is not True and available is not False:
        log(f"{YELLOW}{label}: availability unknown; previous state retained{RESET}")
        return
    log(f"{GREEN if available else YELLOW}{label}: {'Available' if available else 'Not For Sale'}{RESET}")
    path = Path(config.cabin_availability_state_file).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with cabin_state_lock(path):
            state = read_cabin_state(path)
            row = state.get(scope, {})
            notified = bool(available and row.get("available") and row.get("notified"))
            sent = True
            if available and not notified and notifier is not None and len(notifier) > 0:
                lines = [label + " is now available."]
                fare_key = "all_included" if params.all_included else "base"
                fare_key += "_refundable_fare" if params.refundable else "_fare"
                fare = result.get(fare_key) or {}
                price = fare.get("fare")
                if price is not None:
                    if params.prepaid_grats:
                        price += fare.get("gratuities") or 0
                    if params.travel_insurance:
                        price += fare.get("insurance") or 0
                    lines.append(f"Current price: {price:.2f} {params.currency_code}")
                else:
                    lines.append("Current price unavailable; check the booking page.")
                lines.append(url)
                try:
                    sent = notifier.notify(body="\n".join(lines),
                        title="Cruise Room Available", body_format=NotifyFormat.TEXT) is True
                except Exception:
                    sent = False
                notified = sent
            updated = {"url": url, "available": available, "notified": notified}
            if row != updated:
                state[scope] = updated
                write_cabin_state(path, state)
    except (OSError, ValueError, ImportError) as exc:
        raise CabinAvailabilityError("Cannot update cabin availability state; check cabinAvailabilityStateFile, JSON contents and overlapping checks") from exc
    if not sent:
        raise CabinAvailabilityError("Cabin availability notification not confirmed; will retry on the next check")


def get_cruise_price(account_info: AccountInfo,
                     booking: Dict[str, Any],
                     ship_dictionary: ShipRegistry,
                     automatic_URL: bool = True,
                     paid_price_struct: Dict[str, Any] = None,
                     discounts: Optional[DiscountProfile] = None,
                     notification_mode: str = "price"
) -> None:
    """
    Performs dynamic live web-pricing evaluations for a specific stateroom or prospective cruise.

    Simulates consumer search requests to locate real-time pricing and tax figures.
    Compares current market pricing options against the original booked price, logs
    pricing changes to the console, and triggers deal notifications for verified drops.
    """
    # Pull properties from the foundational domain entities
    session = account_info.access.session
    apobj = notifier_for(account_info)
    # None for the synthetic prospective-cruise booking dict (no real reservation)
    reservation_id = booking.get("bookingId")
    if paid_price_struct is None:
        paid_price_struct = booking.get("paidPriceStruct")  # Dict containing target metrics

    provided_url = booking.get("url", "")
    if provided_url:
        # Path A: Standard tracking via an external web marketing link string
        # Parse the provided URL
        url_params = parse_provided_URL(provided_url)

        # FAIL-SAFE PATCH: If the URL parser missed ship/package codes,
        # extract them directly from the tracking URL string parameters
        if not url_params.ship_code or not url_params.package_code:
            try:
                parsed_query = parse_qs(urlparse(provided_url).query)
                if not url_params.ship_code:
                    url_params.ship_code = parsed_query.get("shipCode", [""])[0]
                if not url_params.package_code:
                    url_params.package_code = parsed_query.get("packageCode", [""])[0]
            except Exception:
                pass  # Fall back gracefully if string parsing hits an anomaly
    else:
        # Path B: Active reservation processing fallback.
        # Dynamically calculate passenger counts from the live profile payload.
        guests = booking.get("passengersInStateroom") or booking.get("passengers") or []
        sail_date = booking.get("sailDate", "")

        number_of_adults = 0
        number_of_children = 0
        have_a_senior = False
        stateroom_category_code = ""
        passengers = booking.get("passengers", [])

        for guest in guests:
            if not stateroom_category_code:
                stateroom_category_code = guest.get("stateroomCategoryCode", "")

            birth_date = guest.get("birthdate", "")
            if birth_date and sail_date:
                if not have_a_senior:
                    have_a_senior = above_age_on_sail_date(birth_date, sail_date, 55)

                # Adult is defined as being over 12
                if above_age_on_sail_date(birth_date, sail_date, 12):
                    number_of_adults += 1
                else:
                    number_of_children += 1
            else:
                # No birthdate (TA-entered bookings): count as an adult, same
                # as _calculate_passenger_metrics. Skipping the guest shrank
                # the party - a 2-adult cabin priced as 1 adult compares a
                # 1-guest fare against a 2-guest paid price -> false "Rebook!"
                number_of_adults += 1

        # The category often lives only at booking level (get_voyages also
        # patches its resolved code there for downstream pricing); without
        # this fallback such bookings priced as Unassigned/GTY "Not For Sale"
        if not stateroom_category_code:
            stateroom_category_code = booking.get("stateroomCategoryCode", "")

        metrics = {
            'num_adults': number_of_adults,
            'num_children': number_of_children,
            'have_a_senior': have_a_senior,
            'sub_type': booking.get("stateroomSubtype", ""),
            'category_code': stateroom_category_code
        }

        # 1. Use the pre-validated discounts profile if provided, otherwise fall back
        #    and create a clean dummy dataclass container to pass to the builder
        if discounts is not None:
            temp_discounts = discounts
        else:
            # Safely extract loyalty context from nested access structure
            temp_discounts = DiscountProfile(
                loyalty_number=booking.get("loyaltyNumber") or getattr(account_info, 'loyalty_number', None),
                state=getattr(account_info, 'state', None),
                senior=have_a_senior,
                military=True if (paid_price_struct and paid_price_struct.get('military')) else False,
                fire=True if (paid_price_struct and paid_price_struct.get('fire')) else False,
                police=True if (paid_price_struct and paid_price_struct.get('police')) else False,
                dp340=True if (paid_price_struct and paid_price_struct.get('dp340')) else False
            )

        # 2. Build a dummy pristine, validated web URL
        cruise_price_URL = _build_checkout_url(booking, metrics, account_info, temp_discounts)

        # 3. Parse the dummy URL, jsut as path A!
        url_params = parse_provided_URL(cruise_price_URL)

        # 4. Fix the parser/override omissions immediately while we are safely inside Path B scope
        url_params.package_code = booking.get("packageCode")
        url_params.ship_code = booking.get("shipCode")

        # Extract the correct C&A loyalty asset string rather than the username/email context if given
        if hasattr(account_info, 'access') and account_info.access and getattr(account_info.access, 'loyalty_number', None):
            url_params.loyalty_number = account_info.access.loyalty_number

        # If the account meets the 340 cruise point threshold, pass DP340 as the active code
        if temp_discounts.dp340:
            url_params.coupon_code = 'DP340'

    # Absorb any YAML overrides safely now that url_params is guaranteed to be an object
    url_params.apply_overrides(paid_price_struct)

    # Capture target price bounds if they exist
    # NOTE: both paid_price and paidPrice are valid keys,
    #       depending on booked vs. prospective cruises
    paid_price = None
    if paid_price_struct:
        paid_price = paid_price_struct.get("paid_price", None) # get price retrieved from API
        paid_price = paid_price_struct.get("paidPrice", paid_price) #override with user provided

    room_number = None

    # Identity is based on normalized search criteria, before any coupon fallback.
    cabin_scope = json.dumps(asdict(url_params), sort_keys=True) if notification_mode == "availability" else None
    # Primary API pricing check pass. checkForUpgrades additionally collects
    # every subtype row from the same availability sweep (no extra requests);
    # booked cruises only - upgrade tables mean nothing for a watchlist URL.
    collect_upgrades = bool(automatic_URL and config.check_for_upgrades is True)
    if collect_upgrades:
        # optional scoping: upgradeReservations limits the upgrade check (and
        # its extra family request) to the listed bookings only
        _upgrade_scope = (config.upgrade_reservations
                          if isinstance(config.upgrade_reservations, (list, set, tuple)) else [])
        if _upgrade_scope:
            if str(reservation_id) in [str(r) for r in _upgrade_scope]:
                _UPGRADE_SCOPE_SEEN.add(str(reservation_id))
            else:
                collect_upgrades = False
    api_options = {"inventory_mode": True} if not automatic_URL and notification_mode == "availability" else {}
    results = get_room_price_via_API(url_params, room_number, collect_all=collect_upgrades,
                                     **api_options)
    room_available = results.get("room_available")

    # Defensive Fallback: If a coupon code explicitly bricks availability, retry without it
    fallback_available = results.get("inventory_available", room_available) if notification_mode == "availability" else room_available
    if not fallback_available and url_params.coupon_code is not None:
        log(f"Coupon Code {url_params.coupon_code} may have failed, trying without using it")
        url_params.coupon_code = None
        results = get_room_price_via_API(url_params, room_number, collect_all=collect_upgrades,
                                         **api_options)
        # the upgrade report must not re-try a coupon the main flow just proved fails
        results['coupon_rejected'] = True
        room_available = results.get("room_available")

    if not automatic_URL and notification_mode == "availability":
        results = dict(results, room_available=results.get("inventory_available", room_available))
        # Guarantee categories bypass exact inventory matching in the legacy checker.
        # Require a returned fare before treating that bypass as positive inventory.
        codes = (url_params.stateroom_subtype, url_params.stateroom_category_code)
        if any(code and (code in {"GTY", "XB", "YO", "ZI", "WS", "XN", "CB"} or code.endswith("GTY")) for code in codes):
            if results["room_available"] and not (results.get("base_fare") or {}).get("fare"):
                results["room_available"] = None
        notify_cabin_availability(url_params, results, provided_url, ship_dictionary, apobj, cabin_scope)
        return

    # === Localized Night Count Extraction ===
    # Prioritize the clean parsed values from the watchlist or configuration properties.
    if getattr(url_params, 'duration', 0) > 0:
        resolved_nights = url_params.duration
    elif paid_price_struct and paid_price_struct.get("duration"):
        resolved_nights = int(paid_price_struct["duration"])
    else:
        # Last resort fallback if the availability API contains a valid reading
        api_nights = results.get("sailing_nights")
        resolved_nights = int(api_nights) if (api_nights and int(api_nights) > 0) else 7

    # A watchlist URL can omit or mangle sailDate; a far-future fallback keeps
    # the "past final payment" comparisons meaning "not past" instead of crashing
    try:
        # Resolve the payment market from the booking itself (a watchlist's
        # synthetic booking has no country fields -> None -> US default).
        # Previously this read a market_code attribute CruiseURLParams never
        # had, so EVERY booking was evaluated against the US payment windows
        # and a DEU-market drop 90-30 days out was mislabeled
        # past-final-payment with its alert suppressed.
        market_code = _booking_payment_market(booking)
        final_payment_override = None
        if paid_price_struct:
            final_payment_override = (
                paid_price_struct.get("finalPaymentDaysBeforeSailing")
                or paid_price_struct.get("finalPaymentDate")
            )

        final_payment_date = get_final_payment_date(
            resolved_nights,
            url_params.sail_date,
            market_code=market_code,
            final_payment_date_override=final_payment_override,
        )
    except (TypeError, ValueError):
        final_payment_date = date.max

    # Reach into the global ship mapper object natively
    ship_name = ship_dictionary.get_ship(url_params.ship_code)
    sail_date_display = config.format_date(url_params.sail_date)
    category_display = url_params.stateroom_category_code or url_params.stateroom_subtype or "Unassigned/GTY"
    pre_string = f"{sail_date_display} {ship_name} {url_params.cabin_class_string} {category_display}"

    # Build active discount labels
    used_discounts = ""
    if url_params.loyalty_number is not None: used_discounts += "Loyalty, "
    if url_params.state is not None:          used_discounts += "Residency, "
    # These flags arrive as booleans from parse_provided_URL (a "== 'y'" test
    # here never matched, so the labels silently never printed)
    if _discount_flag_on(getattr(url_params, 'senior', False)):   used_discounts += "Senior, "
    if _discount_flag_on(getattr(url_params, 'police', False)):   used_discounts += "Police, "
    if _discount_flag_on(getattr(url_params, 'military', False)): used_discounts += "Military, "
    if _discount_flag_on(getattr(url_params, 'fire', False)):     used_discounts += "Fire, "
    if url_params.coupon_code is not None:    used_discounts += f"Coupon {url_params.coupon_code}, "

    if used_discounts != "":
        pre_string = f"{pre_string} ({used_discounts[:-2]} Discount)"

    # History must record sail_date/nights in the SAME form the addon/promo/
    # booking-snapshot paths already use (the booking's raw YYYYMMDD sailDate
    # and bare numberOfNights), not url_params.sail_date - that's the dashed
    # date parsed back out of the checkout URL. Recording the dashed form
    # here made a reservation with an add-on purchase produce two history
    # rows that disagree on sail_date/nights for the same sailing, so a
    # downstream viewer grouping on (reservation_id, ship_code, sail_date,
    # nights) showed it as two separate cards. url_params.sail_date/
    # resolved_nights stay exactly as-is for the final-payment computation
    # above and everything else in this function; only what gets written to
    # history changes. The synthetic prospective/watchlist booking (see the
    # `prospective_booking` dict built for config.prospective_cruises) has no
    # sailDate/numberOfNights of its own, so fall back to those already-
    # resolved URL/API-derived values for that case, same as before.
    history_number_of_nights = int(booking.get("numberOfNights") or 0) or None
    if not booking.get("sailDate"):
        history_number_of_nights = resolved_nights
    history_sail_date = booking.get("sailDate") or url_params.sail_date

    # Fields shared by every PriceHistory.record_cabin_fare() call below;
    # each call site only adds current_price/status/rebook_decision/notified
    history_common = {
        # str-coerced to match the addon rows, so the two kinds join cleanly
        "reservation_id": str(reservation_id) if reservation_id is not None else None,
        "account_label": account_info.username,
        "ship_code": url_params.ship_code, "sail_date": history_sail_date, "nights": history_number_of_nights,
        "item_code": f"{url_params.package_code}/{url_params.stateroom_category_code}",
        "paid_price": paid_price, "currency": url_params.currency_code,
        "discount_applied": used_discounts[:-2] if used_discounts else None,
    }

    addons = ""
    refund_not_found = False

    if room_available:
        base_fare_string = "all_included_fare" if url_params.all_included else "base_fare"
        refund_fare_string = "all_included_refundable_fare" if url_params.all_included else "base_refundable_fare"

        fare_struct = results.get(base_fare_string)
        if fare_struct is None and base_fare_string != "base_fare":
            log(f"{RED}All Included Fare is Not Available - Reverting to Non-refundable fare{RESET}")
            fare_struct = results.get("base_fare")

        if fare_struct is None:
            # No fare data at all: bail out rather than comparing against a phantom
            # 0.00 price, which would fire a false "Rebook! New price of 0.00" alert
            log(f"{YELLOW}{pre_string}: No fare pricing returned; cannot compare price{RESET}")
            history.record_cabin_fare(**history_common, current_price=None,
                                              status="no_price_data", rebook_decision=None, notified=False)
            # the sweep still succeeded - the collected rows are worth showing
            _maybe_report_upgrades(url_params, results, paid_price_struct, pre_string,
                                   reservation_id, apobj,
                                   past_final_payment=(date.today() > final_payment_date))
            return

        # The keys always exist (so .get defaults never apply) but their values
        # can be JSON null - treat a null fare like missing fare data instead of
        # crashing on the first {price:.2f} format below
        if fare_struct.get("fare") is None:
            log(f"{YELLOW}{pre_string}: No fare pricing returned; cannot compare price{RESET}")
            history.record_cabin_fare(**history_common, current_price=None,
                                              status="no_price_data", rebook_decision=None, notified=False)
            # the sweep still succeeded - the collected rows are worth showing
            _maybe_report_upgrades(url_params, results, paid_price_struct, pre_string,
                                   reservation_id, apobj,
                                   past_final_payment=(date.today() > final_payment_date))
            return
        price = fare_struct.get("fare") or 0.0
        grats = fare_struct.get("gratuities") or 0.0
        ins = fare_struct.get("insurance") or 0.0

        live_obc = float(fare_struct.get("obc", 0.0) or 0.0)
        booked_obc = float(paid_price_struct.get("booked_obc", 0.0) if paid_price_struct else 0.0)

        # NOTE: For now, we keep the original variable 'obc' mapped to the live_obc
        # to preserve the exact string output behavior the script owner expects.
        obc = f"{live_obc:.2f}" #fare_struct.get("obc", "0.0")

        base_price = price
        base_grats = grats
        base_ins = ins

        desire_refund_price = False
        if url_params.refundable:
            desire_refund_price = True
            addons += "Refundable Deposit, "
            fare_struct = results.get(refund_fare_string)
            if fare_struct is not None and fare_struct.get("fare") is not None:
                price = fare_struct.get("fare") or 0.0
                grats = fare_struct.get("gratuities") or 0.0
                ins = fare_struct.get("insurance") or 0.0
                obc = fare_struct.get("obc") or "0.0"
            else:
                refund_not_found = True

        if url_params.travel_insurance:
            addons += "Travel Protection, "
            price += ins
            base_price += base_ins
        if url_params.prepaid_grats:
            addons += "Prepaid grats, "
            price += grats
            base_price += base_grats
        if url_params.all_included:
            addons += "All Included, "

        if addons != "":
            pre_string = f"{pre_string} ({addons[:-2]})"

    final_payment_date_display = final_payment_date.strftime(config.date_display_format)
    past_final_payment_date = date.today() > final_payment_date

    # Path 0: the availability/pricing request itself FAILED. That is not the
    # same as sold out: don't push "Cruise Room Not Available" and don't
    # record not_for_sale (a network blip would poison back-in-stock history
    # queries and false-alert watchers). Leave a no_price_data row instead.
    if room_available is None or results.get('price_check_failed'):
        log(f"{YELLOW}{pre_string}: Could not check price (request failed); availability unknown{RESET}")
        history.record_cabin_fare(**history_common, current_price=None,
                                  status="no_price_data", rebook_decision=None, notified=False)
        return

    # Path 1: Room is completely unlisted or sold out
    if not room_available:
        text_string = f"{pre_string} Not For Sale"
        if automatic_URL and past_final_payment_date:
            text_string += f". Past Final Payment Date of {final_payment_date_display}"

        log(YELLOW + text_string + RESET)

        # Only notify if it's a watchlist item (automatic_URL is False)
        if not automatic_URL and apobj is not None:
            apobj.notify(body=text_string, title='Cruise Room Not Available', body_format=NotifyFormat.TEXT)

        if url_params.package_code and not automatic_URL:
            # Pre-filter rooms that actually have inventory available
            # (key is 'rooms_left' as produced by check_if_room_is_available; price may be None)
            valid_rooms = [
                r for r in results.get("available_rooms", [])
                if r.get('rooms_left') is not None and r.get('rooms_left') > 0
                and r.get('price') is not None
            ]

            if valid_rooms:
                log(f"\tAvailable Rooms (non-discounted price) for {url_params.number_of_adults} Adult and {url_params.number_of_children} Child on This Sailing Are:")
                for available_room in valid_rooms:
                    log(f"\t{available_room.get('name')} {available_room.get('price'):.2f} - Rooms Left {available_room.get('rooms_left')}")
            else:
                log(f"\tNo alternative room inventory returned by the booking engine.")

        history.record_cabin_fare(**history_common, current_price=None, status="not_for_sale",
                                          rebook_decision=None, notified=(not automatic_URL and apobj is not None))
        # the booked category is gone, but checkForUpgrades can still show
        # what IS on sale for this sailing
        _maybe_report_upgrades(url_params, results, paid_price_struct, pre_string, reservation_id, apobj,
                               past_final_payment=past_final_payment_date)
        return

    obc_value = float(obc or 0.0)
    obc_string = f"{obc_value:.2f}"

    # Path 2: Standard Pricing Evaluation
    if paid_price is None:
        log(GREEN + f"{pre_string}:" + RESET + f" Current Price {price:.2f} {url_params.currency_code}")
        history.record_cabin_fare(**history_common, current_price=price, status="priced",
                                          rebook_decision=None, notified=False)
        _maybe_report_upgrades(url_params, results, paid_price_struct, pre_string, reservation_id, apobj,
                               past_final_payment=past_final_payment_date)
        return

    # rebook_decision / notified are computed inline below, then recorded once
    # after the branch (see B.3/C.2) - the alert logic itself is untouched.
    rebook_decision: Optional[str] = None
    notified = False

    if price < paid_price:
        saving = round(paid_price - price, 2)

        # Sub-branch 1: Actionable booked drop before final lock dates
        if automatic_URL and not past_final_payment_date:
            text_string = f"Rebook! {pre_string} New price of {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                text_string += f", not including {obc_string} USD OBC,"
            text_string += f" is lower than {paid_price:.2f}"

            if config.minimum_saving_alert is not None and saving < config.minimum_saving_alert:
                text_string += f" (Saving {saving:.2f} < minimumSavingAlert {config.minimum_saving_alert}; no notification sent)"
                log(YELLOW + text_string + RESET)
                rebook_decision = "suppressed_below_threshold"
            else:
                log(RED + text_string + RESET)
                if apobj is not None:
                    apobj.notify(body=text_string, title='Cruise Price Alert', body_format=NotifyFormat.TEXT)
                    notified = True
                rebook_decision = "rebook"

        # Sub-branch 2: Booked drop but locked behind final lock dates
        if automatic_URL and past_final_payment_date:
            text_string = f"Past Final Payment Date of {final_payment_date_display}: {pre_string} New price of {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                text_string += f", not including {obc_string} USD OBC,"
            text_string += f" is lower than {paid_price:.2f}"
            log(YELLOW + text_string + RESET)
            rebook_decision = "past_final_payment"

        # Sub-branch 3: Speculative prospective watchlist match
        if not automatic_URL:
            text_string = f"Consider Booking! {pre_string}: New price of {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                text_string += f", not including {obc_string} OBC,"
            text_string += f" is lower than watchlist price of {paid_price:.2f}"

            if config.minimum_saving_alert is not None and saving < config.minimum_saving_alert:
                text_string += f" (Saving {saving:.2f} < minimumSavingAlert {config.minimum_saving_alert:.2f}; no notification sent)"
                log(YELLOW + text_string + RESET)
                rebook_decision = "suppressed_below_threshold"
            else:
                log(RED + text_string + RESET)
                if apobj is not None:
                    apobj.notify(body=text_string, title='Cruise Price Alert', body_format=NotifyFormat.TEXT)
                    notified = True
                rebook_decision = "consider_booking"
    else:
        # Current catalog price is equal to or higher than target price thresholds
        rebook_decision = "best_price"
        temp_string = GREEN + f"{pre_string}: You have the best price of {paid_price:.2f} {url_params.currency_code}" + RESET
        if price > paid_price:
            temp_string += f" (now {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                temp_string += f" not including {obc_string} OBC"
            temp_string += ")"
        else:
            if obc_value > 0:
                temp_string += f" (not including {obc_string} OBC)"

        if desire_refund_price and paid_price > base_price:
            temp_string += f"{YELLOW} Non-Refundable price {base_price:.2f} {url_params.currency_code} is lower than you paid{RESET}"
        elif desire_refund_price:
            temp_string += f" Non-refundable price is {base_price:.2f} {url_params.currency_code}"

        if automatic_URL and past_final_payment_date:
            temp_string += f"{YELLOW} Past Final Payment Date of {final_payment_date_display}{RESET}"
            # distinct from "past_final_payment" (= a LOWER price you are locked
            # out of) so history queries can tell the two situations apart
            rebook_decision = "best_price_past_final_payment"

        log(temp_string)

    history.record_cabin_fare(**history_common, current_price=price, status="priced",
                                      rebook_decision=rebook_decision, notified=notified)

    _maybe_report_upgrades(url_params, results, paid_price_struct, pre_string, reservation_id, apobj,
                               past_final_payment=past_final_payment_date)


def get_room_price_via_API(url_params: CruiseURLParams, room_number: Optional[str] = None,
                           collect_all: bool = False,
                           *, inventory_mode: bool = False) -> Dict[str, Any]:
    # Check room availability against the downstream checker
    room_available, available_rooms = check_if_room_is_available(
        url_params, collect_all=collect_all, inventory_mode=inventory_mode)
    results = {
        'sailing_nights': 0,
        'room_available': room_available,
        # Keep room-selection evidence separate from checkout price results.
        'inventory_available': room_available
    }
    if collect_all:
        # every subtype row with its lead-in total, for the checkForUpgrades table
        results['upgrade_rows'] = available_rooms

    # A matching family with uncertain category inventory can still be priced by
    # checkout. Preserve the legacy early return for ordinary price checks.
    if room_available is False or (room_available is None and not inventory_mode):
        results['available_rooms'] = available_rooms
        return results

    headers = {
        'user-agent': USER_AGENT_WEB,
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
    }

    json_data = {
        'countryCode': url_params.booking_office_country_code,
        'packageId': url_params.package_code,
        'sailDate': url_params.sail_date,
        'currencyCode': url_params.currency_code,
        'rooms': [
            {
                # DO NOT Use the realigned type code here
                'stateroomTypeCode': url_params.stateroom_type_name,
                'stateroomSubtypeCode': url_params.stateroom_subtype,
                'categoryCode': url_params.stateroom_category_code,
                'fareCode': 'BESTRATE',
                'accessible': False,
                'qualifiers': {
                    'fireFighter': url_params.fire,
                    'military': url_params.military,
                    'police': url_params.police,
                    'senior': url_params.senior,
                },
                'occupancy': {
                    'adultCount': url_params.number_of_adults,
                    'childCount': int(url_params.number_of_children),
                },
            },
        ],
    }

    # Create a clean, direct reference alias to the target room dictionary
    room_config = json_data['rooms'][0]

    # Inject targeted elements if they are populated
    if url_params.coupon_code is not None:
        room_config['couponCode'] = url_params.coupon_code

    if room_number is not None:
        room_config['roomNumber'] = room_number

    if url_params.state is not None:
        room_config['qualifiers']['stateCode'] = url_params.state

    if url_params.loyalty_number is not None:
        room_config['qualifiers']['loyaltyNumber'] = url_params.loyalty_number

    # Handle routing endpoints dynamically
    api_URL = f'https://www.{url_params.url_brand}.com/checkout/api/v1/rooms/checkout'

    response = _execute_api_request(
          account_info=None,
          method="POST",
          url=api_URL,
          data=json.dumps(json_data),
          headers=headers,
          on_failure="retry"
    )

    if response is not None:
        try:
            response_json = response.json()
            rooms = response_json.get("rooms")
        except Exception:
             rooms = None
             # unparseable body = request failure, not "sold out"
             results['price_check_failed'] = True
    else:
        rooms = None
        results['price_check_failed'] = True

    if not rooms:
        log("Room price request failed" if results.get('price_check_failed')
            else "Room Price Not Found")
        results['room_available'] = False
        results['available_rooms'] = available_rooms
        return results

    room = rooms[0]

    # Safe multi-layered extraction for sailing nights metrics
    try:
        sailing_nights = response_json.get("sailing", {}).get("itinerary", {}).get("sailingNights", 0)
    except AttributeError:
        sailing_nights = 0

    results['sailing_nights'] = sailing_nights

    # Extract pricing structures with bulletproof inner-dict fallbacks
    fare_mappings = {
        'base_fare': 'baseFare',
        'base_refundable_fare': 'baseRefundableFare',
        'all_included_fare': 'allIncludedFare',
        'all_included_refundable_fare': 'allIncludedRefundableFare'
    }

    for result_key, api_key in fare_mappings.items():
        fare_struct = room.get(api_key)
        if fare_struct is not None:
            # Bulletproof dictionary nesting protection via empty dict defaults {}
            pricing = fare_struct.get("pricing", {})
            invoice = pricing.get("invoice", {})

            results[result_key] = {
                'fare': pricing.get("amount"),
                'gratuities': fare_struct.get("gratuities"),
                'insurance': fare_struct.get("insurance"),
                'obc': invoice.get("onboardCredits", 0)
            }

    if inventory_mode:
        fare = (results.get('base_fare') or {}).get('fare')
        if type(fare) in (int, float) and 0 < fare < float("inf"):
            results['inventory_available'] = True
    results['available_rooms'] = available_rooms
    return results


def check_if_room_is_available(params: CruiseURLParams,
                               collect_all: bool = False,
                               _send_residency: bool = True,
                               *, inventory_mode: bool = False
                               ) -> tuple[Optional[bool], List[Dict[str, Any]]]:
    """
    RSC Scraper Engine wrapper that verifies physical cabin availability on active voyages.

    Simulates a Next.js React Server Component web interaction (/room-selection/type-and-subtype)
    to see if an active booking's specific room style is still available. Employs hardcoded baseline
    testing states ('n') for profile criteria to cleanly monitor general inventory health.
    """
    # Optimized Next.js Server Component payload headers
    headers = {
        'user-agent': USER_AGENT_WEB,
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        "Accept": "text/x-component",
        "RSC": "1",
    }

    # Map directly from the dataclass, maintaining the passenger qualifiers
    request_params = {
        'packageCode': params.package_code,
        'sailDate': params.sail_date,
        'country': params.booking_office_country_code,
        'selectedCurrencyCode': params.currency_code,
        'shipCode': params.package_code[0:2] if params.package_code else "",
        'cabinClassType': params.cabin_class_string or 'INTERIOR', # Endpoint defaults; returns all categories
        'roomIndex': '0',
        'r0a': params.number_of_adults,
        'r0c': params.number_of_children,
        'r0b': 'n',

        'r0l': params.loyalty_number if params.loyalty_number else None,
        'r0r': 'y' if params.police else 'n',
        'r0s': 'y' if params.fire else 'n',
        'r0q': 'y' if params.military else 'n',
        'r0t': 'y' if params.senior else 'n',

        'r0d': params.cabin_class_string or 'INTERIOR',
        'r0D': 'y',
        'rgVisited': 'true',
        'r0C': 'y',
    }
    sent_residency = bool(collect_all and params.state and _send_residency)
    if sent_residency:
        # checkForUpgrades only: the checkout POST and the booked-family request
        # both send the residency state, but this sweep never did - so every
        # OTHER family's row was priced without the residency discount while the
        # booked category was priced with it, overstating each cross-family
        # delta by that discount. r0k is the funnel's own residency parameter
        # (the same one _build_checkout_url emits). Sent only under collect_all
        # so the core price check's request stays byte-identical.
        request_params['r0k'] = params.state

    api_URL = f'https://www.{params.url_brand}.com/room-selection/type-and-subtype'

    response = _execute_api_request(
        method="GET",
        url=api_URL,
        params=request_params,
        headers=headers,
        timeout=config.request_timeout if config else REQUEST_TIMEOUT,
        on_failure="skip",
        use_impersonation=False
    )

    # Under collect_all this sweep also gates the MAIN price check, and r0k on
    # this endpoint is an addition of ours: if the request fails or comes back
    # without inventory, retry once without it (same defensive shape as the
    # coupon retry) rather than let an optional feature break core pricing.
    def _retry_without_residency() -> tuple[Optional[bool], List[Dict[str, Any]]]:
        log("\tResidency-priced availability request returned nothing; retrying without it")
        return check_if_room_is_available(params, collect_all=collect_all,
                                          _send_residency=False, inventory_mode=inventory_mode)

    if response is None:
        if sent_residency:
            return _retry_without_residency()
        log("Unable to check room availability with server")
        # None = unknown, not confirmed unavailable.
        return None, []
    if inventory_mode and response.status_code != 200:
        return None, []

    # Extract structural array matrix out of the component text stream
    available_rooms = []
    rooms = _extract_json_array(response.text, "rooms")

    if not rooms:
        if sent_residency:
            return _retry_without_residency()
        # Missing/malformed is unknown; an explicit empty array confirms closure.
        return (None if inventory_mode and rooms is None else False), available_rooms
    if inventory_mode:
        try:
            stateroom_types = rooms[0]["options"]["stateroomTypes"]
            if not isinstance(stateroom_types, list):
                raise ValueError()
        except (IndexError, AttributeError, KeyError, TypeError, ValueError):
            return None, available_rooms
    else:
        # Preserve the parent project's pricing gate and missing-data behavior.
        try:
            stateroom_types = rooms[0].get("options", {}).get("stateroomTypes", [])
        except (IndexError, AttributeError):
            return False, available_rooms

    # --- GTY / CATEGORY OVERRIDE BYPASS ---
    # Unassigned guarantee inventory (e.g. 'XB', 'ZI', 'YO') and explicit config overrides
    # do not appear as physical subtype entries in the RSC response array. As long as
    # stateroomTypes returned active options for the voyage, bypass the subtype loop.
    gty_codes = {"GTY", "XB", "YO", "ZI", "WS", "XN", "CB"}
    is_gty = (
            params.stateroom_subtype in gty_codes
            or params.stateroom_category_code in gty_codes
            or (params.stateroom_subtype and params.stateroom_subtype.endswith("GTY"))
            or (params.stateroom_category_code and params.stateroom_category_code.endswith("GTY"))
    )

    gty_bypass = bool(is_gty and stateroom_types)
    if gty_bypass and not collect_all:
        return True, []

    # Royal has begun renaming funnel subtype codes so they no longer equal the
    # letters of their categories (Ovation interiors: booking-era code U with
    # categories 2U/4U is now funnel subtype V; Navigator balconies: D -> DW).
    # A booking still carrying the old code then fails the exact-match gate
    # below even though its family is on sale - collect each row's lead-in
    # category so a letters-based fallback can resolve the renamed code.
    letter_matched_subtype = None
    exact_match_found = False
    exact_match_available: Optional[bool] = True
    incomplete_inventory = False

    def subtype_available(subtype):
        if not inventory_mode:
            return True  # Checkout, not lead-in stock, decides ordinary pricing.
        if params.stateroom_category_code and subtype.get("categoryCode") != params.stateroom_category_code:
            return None  # Lead-in stock does not establish a sister category's stock.
        stock = subtype.get("roomsLeft")
        if type(stock) not in (int, float) or not 0 <= stock < float("inf"):
            return None
        return stock > 0

    def _code_letters(code: Optional[str]) -> str:
        return re.sub(r"[^A-Za-z]", "", code or "").upper()

    def sweep_already_answered() -> bool:
        # collect_all keeps sweeping past the point where the plain gate has
        # already returned True (booked subtype matched, or the GTY bypass).
        # From there every row is optional: a malformed one is skipped, so
        # turning the upgrade table on can never fail a check that passes with
        # it off. Before that point rows behave exactly as they always have.
        return collect_all and (exact_match_found or gty_bypass)

    wanted_letters = _code_letters(params.stateroom_subtype) or _code_letters(params.stateroom_category_code)

    for stateroom_type in stateroom_types:
        if inventory_mode and (not isinstance(stateroom_type, dict)
                or not isinstance(stateroom_type.get("stateroomSubtypes"), list)):
            incomplete_inventory = True
            continue
        try:
            stateroom_subtypes = stateroom_type.get("stateroomSubtypes", [])
            if collect_all and not isinstance(stateroom_subtypes, list):
                raise TypeError("stateroomSubtypes is not a list")
        except (AttributeError, TypeError):
            if not sweep_already_answered():
                raise
            continue
        for stateroom_subtype in stateroom_subtypes:
            if inventory_mode and (not isinstance(stateroom_subtype, dict)
                    or not isinstance(stateroom_subtype.get("code"), str)
                    or not stateroom_subtype["code"].strip()):
                incomplete_inventory = True
                continue
            try:
                cur_subtype_code = stateroom_subtype.get("code")
                cur_category_code = stateroom_subtype.get("categoryCode")

                # --- INVENTORY GATE SHORT-CIRCUIT ---
                # If our target cabin style is found, return True immediately. An alternative
                # room array [] isn't needed because the caller function will proceed to execute
                # a heavy POST request for this specific room's pricing.
                #
                # Gate on the subtype `code` alone (not categoryCode). Royal's room-selection
                # page now returns a single lead-in row per subtype; that row's `code` still
                # equals the booking's stateroomSubtype, but its `categoryCode` is only the
                # subtype's lead-in category - no longer the exhaustive per-category list. So it
                # stops equalling the booked stateroomCategoryCode for any cabin booked above the
                # lead-in, which made every such booking read as "Not For Sale". The precise
                # price is unaffected: the checkout POST below still uses the booked category.
                if cur_subtype_code == params.stateroom_subtype:
                    if not collect_all:
                        return subtype_available(stateroom_subtype), []
                    # collect_all: keep sweeping so the upgrade table sees every
                    # subtype (including this booked one - its row anchors dl-rate)
                    exact_match_found = True
                    exact_match_available = subtype_available(stateroom_subtype)

                # Remember the first non-guarantee subtype whose lead-in category shares
                # the booking's letters (booked U/2U -> lead-in 4U -> funnel subtype V),
                # in case the exact-match pass above never fires
                if (letter_matched_subtype is None and wanted_letters
                        and not stateroom_subtype.get("guarantee")
                        and (not inventory_mode or isinstance(cur_category_code, str))
                        and _code_letters(cur_category_code) == wanted_letters):
                    letter_matched_subtype = stateroom_subtype

                # Defensively extract pricing trees to protect against missing API sub-keys
                pricing_struct = stateroom_subtype.get("pricing", {})
                if inventory_mode and not isinstance(pricing_struct, dict):
                    pricing_struct = {}
                invoice_struct = pricing_struct.get("invoice", {}) if pricing_struct else {}
                if inventory_mode and not isinstance(invoice_struct, dict):
                    invoice_struct = {}
                price = invoice_struct.get("total") if invoice_struct else None

                rooms_left = stateroom_subtype.get("roomsLeft")

                # Formulate the alternative room tracking records. The structured
                # fields beyond name/price/rooms_left feed the checkForUpgrades
                # table; the legacy alternatives display only reads those three.
                room_display_name = f"{stateroom_subtype.get('name', '')} {cur_category_code} {cur_subtype_code}".strip()
                subtype_name = stateroom_subtype.get("name")
                subtype_name = subtype_name if isinstance(subtype_name, str) else ""
                available_rooms.append({
                    "name": room_display_name,
                    "price": price,
                    "rooms_left": rooms_left,
                    "type": stateroom_type.get("code"),
                    "subtype": cur_subtype_code,
                    "category": cur_category_code,
                    "display_name": subtype_name,
                    "guarantee": bool(stateroom_subtype.get("guarantee")),
                    "connecting": "connect" in subtype_name.lower(),
                    "refundability": (pricing_struct.get("refundability")
                                      if isinstance(pricing_struct, dict) else None),
                })
            except (AttributeError, TypeError, ValueError):
                if not sweep_already_answered():
                    raise
                continue                    # optional row: skip it, keep the answer

    # Letters fallback: the booked subtype code is not offered under that name,
    # but a subtype whose lead-in category shares its letters is - Royal renamed
    # the code. Adopt the current funnel code so the pricing POST downstream
    # (which sends stateroomSubtypeCode alongside the booked categoryCode)
    # speaks the vocabulary the API expects.
    if exact_match_found:
        return exact_match_available, available_rooms
    if gty_bypass:
        return True, available_rooms

    if letter_matched_subtype is not None:
        available = subtype_available(letter_matched_subtype)
        if available is False:
            return available, []
        current_code = letter_matched_subtype["code"]
        log(f"\tSubtype code {params.stateroom_subtype} is no longer offered under that name; "
            f"using current code {current_code} for the same category family")
        params.stateroom_subtype = current_code
        return available, available_rooms if collect_all else []

    # Fall-through state: The loops completed without finding our exact cabin style.
    # The room is sold out, so we return False along with the collected alternative options.
    return (None if incomplete_inventory else False), available_rooms


####################################
# Add-On/Order/Cart Engine functions
####################################
def get_new_order_price(
    account_info: AccountInfo,
    booking: Dict[str, Any],
    apobj: Optional[Apprise],
    ctx: WatchItemContext
) -> Optional[Dict[str, Any]]:
    """
    Compares active promotional planner prices against a passenger's purchased cost.

    Queries live digital cruise planner catalogs to parse age-bracket targeted rates.
    If a price reduction crosses configured target thresholds, it triggers terminal alerts,
    fires Apprise notifications, and generates explicit browser links for rebooking.
    """
    # --- RESERVATIONS SAFETY FILTER ---
    # Explicit check: If this context item targets specific bookings, enforce isolation
    # Fall back to using extracting the ID from booking if not listed in the ctx structure
    reservation_ID = ctx.reservation_id or booking.get("bookingId")

    # str-coerce both sides: YAML ints vs API string bookingIds must still match
    if ctx.reservations and str(reservation_ID) not in {str(r) for r in ctx.reservations}:
        return

    # Unpack voyage identifiers from the booking entity
    ship = booking.get("shipCode", "")
    start_date = booking.get("sailDate", "")
    number_of_nights = int(booking.get("numberOfNights") or 0)

    currency = booking.get("bookingCurrency", "USD")
    prefix = ctx.prefix or ""
    product = ctx.product or ""

    # Unpack item context elements
    passenger_ID = ctx.passenger_ID
    passenger_name = ctx.passenger_name
    room = ctx.room
    paid_price = ctx.paid_price
    guest_age_string = ctx.guest_age_string
    sales_unit = ctx.sales_unit
    for_watch = ctx.for_watch
    order_code = ctx.order_code
    order_date = ctx.order_date
    owner = ctx.owner

    display_name = passenger_name.ljust(10)
    per_day_price = sales_unit in ['PER_NIGHT', 'PER_DAY']

    params = {
        'reservationId': reservation_ID,
        'startDate': start_date,
        'passengerId': passenger_ID,
    }

    # Get the information on the watched item from the server
    url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/catalog/v2/{ship}/categories/{prefix}/products/{product}'
    response = _execute_api_request(account_info, "GET", url, params=params)

    if response is None:
        # The catalog request failed - NOT "not available for passenger":
        # recording that status for a network error would poison exactly the
        # back-in-stock history queries it exists for.
        log(f"{prefix} {product}: could not check (request failed)")
        history.record_addon(
            item_kind="watchlist" if for_watch else "addon",
            reservation_id=str(reservation_ID) if reservation_ID is not None else None,
            account_label=account_info.username, ship_code=ship, sail_date=start_date,
            nights=number_of_nights or None,
            item_code=f"{prefix}/{product}", guest_id=str(passenger_ID) if passenger_ID is not None else None,
            guest_name=passenger_name, paid_price=paid_price, currency=currency,
            per_night=int(per_day_price), current_price=None,
            status="no_price_data", rebook_decision=None, notified=False)
        return

    try:
        payload = response.json().get("payload")
        if payload is None:
            # Force an exception if the payload layer itself is None
            raise ValueError
    except (AttributeError, ValueError, TypeError):
        log(f"{prefix} {product} not available for passenger")
        # Record this too: for a watchlist item this is the "waiting for it to
        # become bookable" state, exactly what a back-in-stock history query
        # needs a row for. The payload never parsed, so item_name is unknown.
        history.record_addon(
            item_kind="watchlist" if for_watch else "addon",
            reservation_id=str(reservation_ID) if reservation_ID is not None else None,
            account_label=account_info.username, ship_code=ship, sail_date=start_date,
            nights=number_of_nights or None,
            item_code=f"{prefix}/{product}", guest_id=str(passenger_ID) if passenger_ID is not None else None,
            guest_name=passenger_name, paid_price=paid_price, currency=currency,
            per_night=int(per_day_price), current_price=None,
            status="not_available_for_passenger", rebook_decision=None, notified=False)
        return

    # Parse the returned information for analysis and display
    title = payload.get("title")
    variant = ""
    try:
        variant = payload.get("baseOptions")[0].get("selected").get("variantOptionQualifiers")[0].get("value")
    except Exception:
        pass

    if "Bottles" in variant:
        title = f"{title} ({variant})"

    # Fields shared by every PriceHistory.record_addon() call below (title is
    # final as of here); each call site only adds current_price/discount_applied/
    # status/rebook_decision/notified
    history_common = {
        "item_kind": "watchlist" if for_watch else "addon",
        "reservation_id": str(reservation_ID) if reservation_ID is not None else None,
        "account_label": account_info.username, "ship_code": ship, "sail_date": start_date,
        "nights": number_of_nights or None, "item_code": f"{prefix}/{product}", "item_name": title,
        "guest_id": str(passenger_ID) if passenger_ID is not None else None, "guest_name": passenger_name,
        "paid_price": paid_price, "currency": currency, "per_night": int(per_day_price),
    }

    booking_eligibility = payload.get("bookingEligibility") or {}
    if booking_eligibility.get("reason") == "NO_STARTING_FROM_PRICE":
        log(YELLOW + f"\t{title}: Server returned no pricing data (currency mismatch or unavailable for reservation)." + RESET)
        history.record_addon(**history_common, current_price=None, discount_applied=None,
                                     status="no_price_data", rebook_decision=None, notified=False)
        return

    new_price_payload = payload.get("startingFromPrice")

    # Item is no longer for sale or already purchased
    if new_price_payload is None:
        if not for_watch:
            temp_string = YELLOW + f"\t{display_name} (Cabin {room}) has best price "
            if per_day_price:
                temp_string += "per night "
            temp_string += f"for {title} of: {paid_price:.2f} {currency} (No Longer for Sale)" + RESET
        else:
            temp_string = YELLOW + f"\t{title} not available or already booked for {passenger_name.ljust(10)}" + RESET

        log(temp_string)
        history.record_addon(**history_common, current_price=None, discount_applied=None,
                                     status="no_longer_for_sale", rebook_decision=None, notified=False)
        return

    # Extract age-bracket targeted metrics
    current_price = new_price_payload.get(f"{guest_age_string}PromotionalPrice")
    if not current_price:
        current_price = new_price_payload.get(f"{guest_age_string}ShipboardPrice")

    if not current_price:
        # No price returned at all: don't fabricate a 0.00 - comparing it below
        # would fire a false "price is lower / Book!" alert (same failure mode
        # already guarded for cruise fares)
        log(YELLOW + f"\t{title}: no current price returned; cannot compare" + RESET)
        history.record_addon(**history_common, current_price=None, discount_applied=None,
                                     status="no_price_data", rebook_decision=None, notified=False)
        return

    watch_tracker_record = {
        "SailDate": start_date,
        "ReservationID": reservation_ID,
        "Passenger": passenger_name,
        "ProductID": product,
        "ProductTitle": title,
        "CurrentPrice": current_price,
    }

    # Process Deal Alerts
    # rebook_decision / notified / history_discount_applied are computed inline below,
    # then recorded once after the branch (see B.3/C.2) - the alert logic itself is untouched.
    rebook_decision: Optional[str] = None
    notified = False
    history_discount_applied: Optional[str] = None
    if current_price < paid_price:
        # Current price on server is lower than the paid price (rebooking alert path)
        saving = round(paid_price - current_price, 2)
        saving_for_alert = saving
        saving_label = f"Saving {saving} {currency}"

        if per_day_price and number_of_nights:
            saving_for_alert = round(saving * number_of_nights, 2)
            saving_label = f"Saving {saving} {currency} per night ({saving_for_alert} {currency} total)"

        prefix_tag = f"[WATCH] {display_name} (Cabin {room})" if for_watch else f"{passenger_name}"
        text = f"{prefix_tag}: {'Book!' if for_watch else 'Rebook!'} {title} Price "
        if per_day_price:
            text += "per night "
        text += f"is lower: {current_price} {currency} than {paid_price} {currency}"

        # Reaching into global config for alerts configuration
        if config.minimum_saving_alert is not None:
            text += f" ({saving_label})"

        promo_description = payload.get("promoDescription")
        if promo_description:
            promotion_title = promo_description.get("displayName")
            text += f'\n\t\tPromotion:{promotion_title}'
            history_discount_applied = promotion_title

        if for_watch:
            text += f'\n\tBook at https://www.{account_info.url_brand}.com/account/cruise-planner/category/{prefix}/product/{product}?bookingId={reservation_ID}&shipCode={ship}&sailDate={start_date}'
        else:
            text += f'\n\tCancel Order {order_date} {order_code} at https://www.{account_info.url_brand}.com/account/cruise-planner/order-history?bookingId={reservation_ID}&shipCode={ship}&sailDate={start_date}'

        if not owner:
            text += "\tThis was booked by another in your party. They will have to cancel/rebook for you!"

        if any(rule.matches(reservation_ID, ctx) for rule in config.ignored_price_alerts):
            log(YELLOW + text + " (Notification suppressed by ignoredPriceAlerts)" + RESET)
            rebook_decision = "suppressed_by_configuration"
        elif config.minimum_saving_alert is not None and saving_for_alert < config.minimum_saving_alert:
            text += f" ({saving_label} < minimumSavingAlert {config.minimum_saving_alert:.2f}; no notification sent)"
            log(YELLOW + text + RESET)
            rebook_decision = "suppressed_below_threshold"
        else:
            log(RED + text + RESET)
            if apobj is not None:
                apobj.notify(body=text, title='Cruise Addon Price Alert', body_format=NotifyFormat.TEXT)
                notified = True
            rebook_decision = "consider_booking" if for_watch else "rebook"
    else:
        # Current price on server is higher than the paid price ("currently best price" path)
        rebook_decision = "best_price"
        if for_watch:
            if current_price == paid_price:
                comp_string = "the same as"
            else:
                comp_string = "higher than"
            temp_string = GREEN + f"[WATCH] {display_name} (Cabin {room}) {title} price is {comp_string} watch price: {paid_price:.2f} {currency}" + RESET
        else:
            temp_string = GREEN + f"{display_name} (Cabin {room}) has best price "
            if per_day_price:
                temp_string += "per night "
            temp_string += f"for {title} of: {paid_price:.2f} {currency}" + RESET
        if current_price > paid_price:
            temp_string += f" (now {current_price:.2f} {currency})"
        log(temp_string)

    history.record_addon(**history_common, current_price=current_price,
                                 discount_applied=history_discount_applied, status="priced",
                                 rebook_decision=rebook_decision, notified=notified)

    return watch_tracker_record


def write_watch_price_json(rows: List[Dict[str, Any]], output_path: str) -> None:
    """Write the add-on watch prices collected during this run as a JSON array."""
    if platform.system() == "iOS":
        output_path = os.path.expanduser('~/Documents') + "/" + output_path

    try:
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(rows, output_file, indent=2)
            output_file.write("\n")
        log(f"\n{BLUE}Writing watchlist JSON to {output_path}" + RESET)
    except OSError as error:
        log(f"{YELLOW}Warning: Could not write JSON watch output '{output_path}': {error}{RESET}")


def process_watch_list_for_booking(
    account_info: AccountInfo,
    booking: Dict[str, Any],
    watch_list_items: List[WatchListItem],
    apobj: Optional[Apprise],
    passenger_info: Dict[str, Any],
    collected_watch_rows: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    Evaluates individual user watchlist targets against active booking records.

    Iterates through configured targets, enforces isolation boundaries (such as specific
    cabin exceptions), pairs the runtime items into a temporary context package, and
    transfers evaluation duties to the live planner catalog matching engines.
    """
    if not watch_list_items:
        return

    # Unpack passenger details from the transient loop package
    passenger_ID = passenger_info.get("passenger_ID")
    passenger_name = passenger_info.get("passenger_name", "")
    room = passenger_info.get("room")

    for watch_item in watch_list_items:
        # Gather the watchlist item information for checking
        name = getattr(watch_item, 'name', 'Unknown Item')
        product = getattr(watch_item, 'product', None)
        prefix = getattr(watch_item, 'prefix', None)
        watch_price = float(getattr(watch_item, 'price', 0))
        enabled = getattr(watch_item, 'enabled', True)  # Default to True if not specified
        guest_age_string = str(getattr(watch_item, 'guest_age_string', "adult")).lower()

        reservation_list = getattr(watch_item, 'reservations', None)
        reservation_ID = booking.get("bookingId")

        if reservation_list:
            # str-coerce both sides: YAML ints vs API string bookingIds must still match
            if str(reservation_ID) not in {str(r) for r in reservation_list}:
                continue

        # Skip disabled watchlist items
        if not enabled:
            continue

        if not product or not prefix or watch_price <= 0:
            log(f"\t{YELLOW}Skipping {name} - missing required fields{RESET}")
            continue

        # Pack up the transient items into a context object
        ctx = WatchItemContext(
            prefix=prefix,
            product=product,
            passenger_ID=passenger_ID,
            passenger_name=passenger_name,
            room=room,
            paid_price=watch_price,
            guest_age_string=guest_age_string,
            sales_unit=None,
            for_watch=True,
            order_code="WATCH-LIST",
            order_date="Watch List",
            owner=True,
            reservations=getattr(watch_item, 'reservations', []),
            reservation_id=reservation_ID or ""
        )

        # Check the item's current price
        if watch_row := get_new_order_price(account_info, booking, apobj, ctx):
            if collected_watch_rows is not None:
                collected_watch_rows.append(watch_row)


def get_orders(
    account_info: AccountInfo,
    booking: Dict[str, Any],
    collected_watch_rows: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Retrieves the digital order history or itinerary manifest for an active booking.

    Queries corporate transactional endpoints to pull details on pre-purchased items,
    shore excursions, or specialty configurations. Essential for auditing what
    add-ons have already been tied to a passenger's profile.
    """
    # Extract voyage characteristics from booking payload
    ship = booking.get("shipCode", "")
    start_date = booking.get("sailDate", "")
    number_of_nights = int(booking.get("numberOfNights") or 0)
    currency = booking.get("bookingCurrency", "USD")

    # Build dynamic guest/reservation lookups
    guest_registry = {}
    unique_reservations = set()

    # Register primary guests
    primary_res_id = booking.get("bookingId") or booking.get("reservationId")
    if primary_res_id:
        unique_reservations.add(primary_res_id)
    for guest in booking.get("guests", []):
        pid = guest.get("passengerId")
        if pid:
            guest_registry[pid] = {
                "cabin": guest.get("cabinNumber", "None"),
                "res_id": primary_res_id
            }

    # Register linked guests
    for linked in booking.get("linkedReservations", []):
        linked_res_id = linked.get("bookingId") or linked.get("reservationId")
        if linked_res_id:
            unique_reservations.add(linked_res_id)
        for guest in linked.get("guests", []):
            pid = guest.get("passengerId")
            if pid:
                guest_registry[pid] = {
                    "cabin": guest.get("cabinNumber", "None"),
                    "res_id": linked_res_id
                }

    # Loop over each unique reservation to grab order history
    for current_res_id in unique_reservations:
        # Find a passenger ID associated with this specific reservation to use for the payload
        # (The API just needs a valid passenger container attached to that reservation)
        current_passenger_id = next(
            (pid for pid, data in guest_registry.items() if data["res_id"] == current_res_id),
            booking.get("passengerId")
        )

        params = {
            'passengerId': current_passenger_id,
            'reservationId': current_res_id,
            'sailingId': f"{ship}{start_date}",
            'includeMedia': 'false',
        }

        url_history = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/calendar/v1/{ship}/orderHistory'
        response = _execute_api_request(account_info, "GET", url_history, params=params)

        # If this particular reservation has no orders, skip to the next room
        if not response:
            continue
        try:
            payload = response.json().get("payload")
        except ValueError:
            continue
        if not payload:
            continue   # a bare 'return' here would silently drop every remaining cabin

        # Merge my orders and orders booked on my behalf
        all_orders = (payload.get("myOrders") or []) + (payload.get("ordersOthersHaveBookedForMe") or [])

        for order in all_orders:
            order_code = order.get("orderCode")
            try:
                date_obj = datetime.strptime(order.get("orderDate"), "%Y-%m-%d")
                order_date = date_obj.strftime(config.date_display_format)
            except (TypeError, ValueError):
                order_date = order.get("orderDate") or "Unknown"
            owner = order.get("owner")

            # Only process valid paid orders
            if (order.get("orderTotals", {}).get("total", 0) or 0) > 0:
                url_detail = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/calendar/v1/{ship}/orderHistory/{order_code}'
                response = _execute_api_request(account_info, "GET", url_detail, params=params)
                if response is None:
                    continue
                order_data = response.json()
                if not order_data or not order_data.get("payload"):
                    continue

                for order_detail in order_data.get("payload", {}).get("orderHistoryDetailItems", []):
                    quantity = order_detail.get("priceDetails", {}).get("quantity", 0)
                    order_title = order_detail.get("productSummary", {}).get("title")

                    # Pre-6 Feb 2026 API structure safety hook
                    try:
                        product = order_detail.get("productSummary", {}).get("baseOptions")[0].get("selected", {}).get("code")
                    except Exception:
                        product = order_detail.get("productSummary", {}).get("defaultVariantId")

                    prefix = order_detail.get("productSummary", {}).get("productTypeCategory", {}).get("id", "")
                    sales_unit = order_detail.get("productSummary", {}).get("salesUnit")
                    guests = order_detail.get("guests", [])

                    for guest in guests:
                        if guest.get("orderStatus") == "CANCELLED":
                            continue

                        paid_price = guest.get("priceDetails", {}).get("subtotal", 0)
                        paid_quantity = guest.get("priceDetails", {}).get("quantity", 0)

                        if paid_price == 0:
                            continue

                        guest_passenger_ID = guest.get("id")
                        first_name = guest.get("firstName", "").capitalize()
                        guest_age_string = guest.get("guestType", "").lower()

                        # Check the nested guest dictionary first (either reservation or booking ID),
                        # then the value scraped from the primary booking, finally the one passed to the
                        # server to get all the orders
                        guestreservation_ID = guest.get("reservationId") or                               \
                                              guest.get("bookingId") or                                   \
                                              guest_registry.get(guest_passenger_ID, {}).get("res_id") or \
                                              current_res_id

                        # Deduplication filtering
                        new_key = f"{guest_passenger_ID}{guestreservation_ID}{prefix}{product}"
                        if new_key in account_info.found_items:
                            continue
                        account_info.found_items.add(new_key)

                        # Compute specialized per-day or per-night calculations
                        if sales_unit in ['PER_NIGHT', 'PER_DAY'] and number_of_nights > 0:
                            # Strip out voyage duration to establish a daily cabin base rate
                            paid_price = round(paid_price / number_of_nights, 2)

                        if paid_quantity > 0:
                            # Divide by package headcount to isolate the final per-guest daily rate
                            paid_price = round(paid_price / paid_quantity, 2)

                        room = guest_registry.get(guest_passenger_ID, {}).get("cabin")
                        if not room or room == "None":
                            room = guest.get("stateroomNumber") or None

                        # Pack up the transient items into a context object
                        ctx = WatchItemContext(
                            prefix=prefix,
                            product=product,
                            passenger_ID=guest_passenger_ID,
                            passenger_name=first_name,
                            room=room,
                            paid_price=paid_price,
                            guest_age_string=guest_age_string,
                            sales_unit=sales_unit,
                            for_watch=False,
                            order_code=order_code,
                            order_date=order_date,
                            owner=owner,
                            reservations=[],
                            reservation_id=guestreservation_ID
                        )

                        # Check the item's current price
                        if watch_row := get_new_order_price(account_info, booking, notifier_for(account_info), ctx):
                            if collected_watch_rows is not None:
                                collected_watch_rows.append(watch_row)


def get_all_promotions(account_info: AccountInfo, booking: Dict[str, Any]) -> None:
    """
    Queries corporate promotion catalog directories for applicable public or loyalty fare discount codes.

    Gathers combinations of eligible code matrices (such as 'BESTRATE') active for a specific
    vessel and departure timeline. Provides a foundational dictionary array used by the pricing
    engines to determine valid discount paths.
    """
    def fetch_promos(page: str) -> List[Dict[str, Any]]:
        """
        Submits specific voyage parameters to corporate servers to harvest eligible discount code strings.

        Acts as the targeted fetching layer for promotion matrices. Isolates public rate adjustments
        and client loyalty discounts available for a precise ship, cabin code, and departure window,
        returning a clean index array used by downstream pricing validation engines.
        """
        # _execute_api_request automatically handles Access-Token, AppKey, and vds-id,
        # so we no longer need to manually declare the headers dict here!
        resp = _execute_api_request(
            account_info=account_info,
            method="GET",
            url=base_url,
            params={'sailingId': sailing_ID, 'page': page, 'currencyIso': currency},
            on_failure="retry"  # Allow non-essential promotions to degrade gracefully if API drops
        )

        if resp is None:
            return []

        try:
            # The original code looks for "payload" and falls back to an empty list
            return resp.json().get("payload") or []
        except Exception:
            return []


    base_url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/catalog/v2/promotions/list'

    # Safely extract routing identifiers from the booking dictionary
    ship = booking.get("shipCode", "")
    start_date = booking.get("sailDate", "")
    currency = booking.get("bookingCurrency")

    sailing_ID = f"{ship}{start_date}"

    all_promos = fetch_promos('homepage')
    if not all_promos and config.show_promos:
        log("No active promos to display")
        return

    banner_by_id = {}
    for promo in fetch_promos('pdp'):
        # Defensive check: skip if the API returned a flat string instead of a dictionary
        if not isinstance(promo, dict):
            continue

        for template in promo.get("templates", []):
            if not isinstance(template, dict):
                continue
            if template.get("type") == "SITEWIDE_BANNER":
                banner_by_id[promo.get("id")] = template
                break

    seen_IDs = set()
    for promo in all_promos:
        promo_ID = promo.get("id")
        if promo_ID in seen_IDs:
            continue
        seen_IDs.add(promo_ID)

        promo_start = (promo.get("startDate") or "")[:10]
        promo_end = (promo.get("endDate") or "")[:10]
        date_range = f"(Valid {promo_start} to {promo_end})"

        banner = banner_by_id.get(promo_ID)
        if banner:
            promo_title = f"{banner.get('heading3', '')} {banner.get('heading4', '')} - {banner.get('heading1', '')}"
            promo_line = f"[PROMO] {promo_title} {date_range}"
        else:
            template = next((t for t in promo.get("templates", []) if isinstance(t, dict) and t.get("type") == "HOME_HERO_LOCKUP"), None)
            if not template:
                continue

            description = ""
            lockup_media = template.get("lockupMedia")
            if lockup_media and lockup_media.get("source"):
                filename = lockup_media["source"].get("path", "").split("/")[-1]
                match = re.search(r'lockup-(.+?)_[A-Z]{2}\.', filename)
                if match:
                    # Asset filenames often end with design descriptors
                    # (e.g. "40-early-booking-bonus-internet-green-teal-blue-text");
                    # strip that trailing run of color/design words so only the
                    # promotion name remains
                    design_words = {"text", "logo", "lockup", "banner", "light", "dark",
                                    "white", "black", "red", "green", "blue", "teal", "navy",
                                    "yellow", "gold", "orange", "purple", "magenta", "pink",
                                    "silver", "gray", "grey", "aqua", "cyan"}
                    words = match.group(1).split("-")
                    while len(words) > 2 and words[-1].lower() in design_words:
                        words.pop()
                    description = " ".join(words).upper()

            category_code = template.get("categoryCode", "")
            promo_title = description or promo_ID
            promo_line = f"[PROMO] {promo_title}"
            if category_code:
                promo_line += f" ({category_code})"
            promo_line += f" {date_range}"

        history.record_promo(
            account_label=account_info.username, ship_code=ship, sail_date=start_date,
            promo_id=promo_ID, promo_title=promo_title, promo_line=promo_line,
            promo_start=promo_start, promo_end=promo_end,
        )
        log(YELLOW + promo_line + RESET)


def get_OBC(account_info: AccountInfo, booking: Dict[str, Any]) -> float:
    """
    Extracts Onboard Credit (OBC) balances and promotional credit allocations for a booking.

    Inspects transaction summaries and pricing breakdowns within an active reservation.
    Aggregates split credit lines into a single friendly number, letting users see exactly
    how much total spending money is attached to their account.
    """
    # Pull authenticated identity elements from account_info
    access_token = account_info.access.token
    account_id = account_info.access.id
    session = account_info.access.session

    # Safely pull transaction metrics directly from the booking dictionary
    reservation_ID = booking.get("bookingId")
    ship_code = booking.get("shipCode", "")
    sail_date = booking.get("sailDate", "")

    params = {
        'passengerId': booking.get("passengerId"),
        'sailingId': f"{ship_code}{sail_date}",
    }

    url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/cart/v1/obc/reservations/{reservation_ID}'
    response = _execute_api_request(account_info, "GET", url, params=params)
    if response is None:
        return 0.0
    payload = response.json().get("payload")
    if not payload:
        return 0.0

    amount = payload.get("amount")
    cur = payload.get("currencyIso")

    if amount and amount > 0:
        log(f"\tOnboard Credit of {amount:.2f} {cur}")
        return float(amount)

    return 0.0


def _build_checkout_url(
    booking: Dict[str, Any],
    metrics: Dict[str, Any],
    account_info: AccountInfo,
    discounts: DiscountProfile
) -> str:
    """
    Generates a live corporate web URL mirroring the parameters used during price tracking.

    Assembles passenger counts, ship short codes, voyage targets, regional residency codes,
    and senior or military indicators into url parameters. Provides users with a direct
    browser link to confirm or purchase the rate.
    """
    brand_code = "R" if account_info.is_royal else "C"

    # Map the boolean flags from the discounts dataclass to web-URL strings ('y'/'n')
    # and safely apply the 'senior' override locally
    is_senior = "y" if (discounts.senior or metrics['have_a_senior']) else "n"
    is_military = "y" if discounts.military else "n"
    is_police = "y" if discounts.police else "n"
    is_fire = "y" if discounts.fire else "n"

    sail_date = booking.get("sailDate")
    url_sail_date = f"{sail_date[0:4]}-{sail_date[4:6]}-{sail_date[6:8]}"
    stateroom_number = booking.get("stateroomNumber")

    # Resolve stateroom type and subtype defaults safely
    stateroom_type = _parse_stateroom_type(booking.get("stateroomType"))
    fallback_category = metrics.get('category_code') or metrics.get('sub_type') or booking.get("stateroomType")

    sub_type = metrics.get('sub_type') or fallback_category
    category_code = metrics.get('category_code') or fallback_category

    # Build the dictionary of parameters that URLs for GTY and non-GTY share completely
    params = {
        'packageCode': booking.get("packageCode"),
        'sailDate': url_sail_date,
        'country': _booking_country_code(booking),
        'selectedCurrencyCode': booking.get("bookingCurrency"),
        'shipCode': booking.get("shipCode"),
        'roomIndex': '0',
        'r0a': metrics['num_adults'],
        'r0c': metrics['num_children'],
        'r0d': _parse_stateroom_type(booking.get("stateroomType")),
        'r0e': metrics['sub_type'],
        'r0f': metrics['category_code'],
        'r0b': 'n',
        'r0r': is_police,
        'r0s': is_fire,
        'r0q': is_military,
        'r0t': is_senior,
        'r0D': 'y'
    }

    # Handle optional properties from the dataclass
    if discounts.dp340 and brand_code == "R" and metrics['num_adults'] == 1 and metrics['num_children'] == 0:
        params['r0i'] = 'DP340'

    if discounts.loyalty_number is not None:
        params['r0l'] = discounts.loyalty_number

    if discounts.state is not None:
        params['r0k'] = discounts.state

    # Define the base URL and add the GTY-specific parameters as needed
    base_url = f"https://www.{account_info.url_brand}.com/room-selection/room-location"
    if stateroom_number == "GTY":
        params['r0g'] = 'BESTRATE'
        params['r0h'] = 'n'
        params['r0C'] = 'y'

    # Drop parameters with no value: urlencode() would otherwise stringify
    # None into the literal string "None" (e.g. travel-agent bookings that
    # carry no bookingOfficeCountryCode). Downstream URL parsing would then
    # forward countryCode="None" to the checkout API, which rejects it with
    # HTTP 400 BAD_INPUT ("must match pattern ^[A-Z]{3}$") and the cabin
    # reports "Room Price Not Found". Omitting the key lets
    # parse_provided_URL() fall back to its defaults (country -> "USA").
    params = {key: value for key, value in params.items() if value is not None and value != ""}

    # Seamlessly combine the base URL and the safely encoded string
    return f"{base_url}?{urlencode(params)}"


def get_checkin_statuses(account_info: AccountInfo, reservation_id: str, guest_ID: str) -> dict:
    """
    Retrieves digital check-in boarding passes or luggage tag documentation assets.
    """
    account_ID = account_info.access.id if account_info.access else ""

    headers = {
        'content-type': 'application/json',
        'accept': 'application/json',
    }

    payload = {
        'guestReservationIds': [
            {
                'bookingId': reservation_id,
                'guestId': guest_ID,
            },
        ],
    }

    api_url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v2/guestCheckin/statuses/{account_ID}'
    response = _execute_api_request(
            account_info=account_info,
            method="POST",
            url=api_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=SHORT_REQUEST_TIMEOUT,
            on_failure="retry"
    )

    if response is None:
        return []

    # Safely extract the payload, defaulting to {} if it's missing or explicitly None
    data = response.json().get("payload") or {}
    return data.get("checkinStatuses") or []


def get_boarding_pass(account_info: AccountInfo, booking: Dict[str, Any], guest_ID: str) -> dict:
    """
    [FUTURE USE}
   Retrieves digital check-in boarding passes or luggage tag documentation assets.

    Pulls technical verification receipts and barcode metadata maps showing if a booking is
    cleared to print standard pier entry documentation or if profile records require active
    terminal management.
    """
    booking_ID = booking.get("bookingId")
    account_ID = account_info.access.id

    headers = {
        'content-type': 'application/json',
        'accept': 'application/json',
    }

    payload = {
        'guestReservationIds': [
            {
                'bookingId': booking_ID,
                'guestId': guest_ID,
            },
        ],
    }

    api_url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v2/guestCheckin/statuses/{account_ID}'
    response = _execute_api_request(
            account_info=account_info,
            method="POST",
            url=api_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=SHORT_REQUEST_TIMEOUT,
            on_failure="retry"
    )

    ret_val = {} if response is None else response.json()
    return ret_val


##############################
# Metric Calculation functions
##############################
def get_number_of_nights(account_info: AccountInfo, loyalty_number: str,
                         brand: Optional[str] = None) -> Tuple[int, int]:
    """
    Queries cumulative night metrics and cruise totals for a specified loyalty profile.

    Queries corporate historical data points. Runs with 'on_failure="retry"' inside the
    request core so historical lookup dropouts won't crash critical root execution pipelines.

    brand must match the loyalty PROGRAM being queried ("royal" for Crown &
    Anchor numbers, "celebrity" for Captain's Club), not the account's login
    brand: the profile shows both programs for either login, and querying a
    number against the other program's endpoint returns HTTP 400 (issue #116).
    Defaults to the account's brand for any caller that queries its own program.
    """
    total_nights, total_trips = -1, -1

    url = f"https://aws-prd.api.rccl.com/en/{brand or account_info.api_brand}/web/v1/guestAccounts/loyalty/history/summary"

    # Request the information from the servers
    response = _execute_api_request(
        account_info, "GET", url,
        params={'loyaltyNumber': loyalty_number},
        timeout=SHORT_REQUEST_TIMEOUT,
        on_failure="retry"
    )

    if response and response.status_code == 200:
        payload = response.json().get("payload", {})
        total_nights = payload.get("totalNights", total_nights)
        total_trips = payload.get("totalTrips", total_trips)

    return total_nights, total_trips


def _calculate_passenger_metrics(
    guests: List[Dict[str, Any]],
    sail_date: str,
    booking: Dict[str, Any],
    brand_code: str,
) -> Dict[str, Any]:
    """
    Parses structural guest files to calculate age milestones, check-in windows, and demographic flags.

    Evaluates age metrics on departure day to isolate senior statuses, tracks child/adult ratios,
    extracts boarding windows, and applies legacy GTY profile patches to fix missing API elements.
    """
    passenger_names = []
    checkin_strings = []
    boarding_time = ""
    num_adults = 0
    num_children = 0
    have_a_senior = False

    # Track distinct room tracking variables to safely prevent outer loop corruption
    stateroom_type = booking.get("stateroomType")
    stateroom_subtype = booking.get("stateroomSubtype")
    stateroom_category_code = None   # a booking with no guests must not leave this unbound

    # Check top-level booking fallbacks for GTY reservations where guest-level keys are omitted
    raw_category = (
        booking.get("stateroomCategoryCode")
        or booking.get("categoryCode")
        or booking.get("subCategoryCode")
    )

    # Deep check in passengersInStateroom or passengers arrays if top-level is absent (common in GTY)
    if not raw_category:
        passenger_list = (booking.get("passengersInStateroom") or []) + (booking.get("passengers") or [])
        for guest in passenger_list:
            if isinstance(guest, dict):
                raw_category = (
                    guest.get("stateroomCategoryCode")
                    or guest.get("subCategoryCode")
                    or guest.get("categoryCode")
                )
                if raw_category:
                    break

    # Sanitize and validate candidate category code against CHECKOUT_FORBIDDEN_CATEGORY_CODES
    booking_category_fallback = sanitize_category_code(raw_category)

    for guest in guests:
        guest_category = guest.get("stateroomCategoryCode")
        stateroom_category_code = sanitize_category_code(guest_category) or booking_category_fallback
        category_unresolved = (stateroom_category_code is None and stateroom_subtype is None)

        # Names & Demographic verification
        first_name = guest.get("firstName", "").capitalize()
        passenger_names.append(first_name)

        birth_date = guest.get("birthdate")
        if not have_a_senior:
            have_a_senior = above_age_on_sail_date(birth_date, sail_date, 55)

        if not birth_date or above_age_on_sail_date(birth_date, sail_date, 12):
            # No birthdate on record (common on TA-entered bookings): price as
            # an adult - above_age_on_sail_date() returns False for a missing
            # date, which silently classified these guests as children
            num_adults += 1
        else:
            num_children += 1

        # Calculate Check-in Windows
        status = guest.get("onlineCheckinStatus", "")
        arrival_time = guest.get("arrivalTime")

        if arrival_time:
            # Safely slice hours and minutes from the API's time string
            boarding_hour = arrival_time[9:11]
            boarding_min = arrival_time[11:13]
            formatted_time = f"{boarding_hour}:{boarding_min}"
            if not boarding_time:
                boarding_time = formatted_time

            if status == "COMPLETED":
                checkin_strings.append(f"{first_name}: Boarding Time {formatted_time}")
            # Catch "IN_PROGRESS", "PARTIAL", or "PARTIALLY_COMPLETE" safely
            elif "PART" in status or status == "IN_PROGRESS":
                # Yellow: this guest still has check-in steps to finish
                checkin_strings.append(f"{YELLOW}{first_name}: Check-in partially complete; Boarding Time {formatted_time}{RESET}")
            else:
                # Fallback if a time exists but the status string is unusual
                checkin_strings.append(f"{first_name}: Boarding Time {formatted_time}")

    return {
        "passenger_names": ", ".join(passenger_names),
        "checkin_string": ", ".join(checkin_strings),
        "boarding_time": boarding_time,
        "num_adults": num_adults,
        "num_children": num_children,
        "have_a_senior": have_a_senior,
        "category_code": stateroom_category_code,
        "sub_type": stateroom_subtype#,
    }


#####################################
# Main execution path and Run Control
#####################################
def setup_hybrid_logging(log_file_path: Optional[str] = None) -> None:
    """
    Initializes the tracking environment, functional logging aliases, and file captures.
    """
    global log, log_warn, log_err, has_terminal_issues

    # 1. Determine terminal safety based on module-level configuration constant
    has_terminal_issues = any(k in os.environ for k in PROBLEM_ENVS)

    # 2. Safely attempt stream reconfiguration and ANSI enablement on Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            has_terminal_issues = True

        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

    # 3. Construct and clear out active root logging context
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    # 4. Terminal Stream Handler (Keeps original ANSI terminal colors)
    # Extract underlying real stdout stream to prevent recursion on re-initialization calls
    real_stdout = sys.stdout
    while isinstance(real_stdout, PrintRedirector):
        real_stdout = getattr(real_stdout, '_wrapped_stream', None) or sys.__stdout__

    console_handler = logging.StreamHandler(real_stdout)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    if platform.system() == "iOS":
        console_handler.addFilter(StripAnsiFilter())

    root_logger.addHandler(console_handler)

    # 5. Plain Text File Handler
    if log_file_path:
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"

        if platform.system() == "iOS":
            log_file_path = os.path.expanduser('~/Documents') + "/" + log_file_path

        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(delimiter)

            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter('%(message)s'))
            file_handler.addFilter(StripAnsiFilter())
            root_logger.addHandler(file_handler)
        except IOError as e:
            sys.stderr.write(f"Warning: Could not open log file '{log_file_path}': {e}\n")

    # 6. Initialize shortcut execution instances and map to module globals
    easy_log_instance = EasyLogger(root_logger)
    log = easy_log_instance
    log_warn = easy_log_instance.warn
    log_err = easy_log_instance.error

    # 7. Intercept raw standard print statements system-wide
    sys.stdout = PrintRedirector(root_logger.info)


def expand_env_vars(value: Any) -> Any:
    """
    Recursively replaces configuration values that are exactly ${VAR_NAME} with
    that environment variable's value, so secrets like passwords can stay out
    of config.yaml. Only whole-value matches against set variables are
    expanded, which keeps literal passwords containing '$' untouched.
    """
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', value)
        if match and match.group(1) in os.environ:
            return os.environ[match.group(1)]
    return value


def build_apprise(items: List[Dict[str, Any]]) -> Optional[Any]:
    """Builds an Apprise object from a list of {url: ...} dicts, as found under an
    apprise: key in config.yaml (top-level or per-account).

    Apprise is an optional dependency, so notifications are disabled with a warning
    if apprise: is configured but the apprise package is not installed.

    Args:
        items (List[Dict[str, Any]]): List of dictionary configs (e.g. [{'url': '...'}]).

    Returns:
        Optional[Apprise]: A configured Apprise notifier, or None.
    """
    if not items:
        return None

    urls = [
        item["url"]
        for item in items
        if isinstance(item, dict) and "url" in item
    ]
    if not urls:
        return None

    if Apprise is None:
        logging.warning(
            "apprise: is configured in config.yaml but the apprise package "
            "is not installed - notifications are disabled. Run: pip install apprise"
        )
        return None

    apobj = Apprise()
    for url in urls:
        apobj.add(url)
    return apobj


def notifier_for(account_info: Optional[AccountInfo]) -> Optional[Apprise]:
    """Per-account Apprise object if configured, else the global one."""
    if account_info is not None and getattr(account_info, "apobj", None) is not None:
        return account_info.apobj
    return config.apobj


def parse_price_alert_exclusions(raw: Any) -> List[PriceAlertExclusion]:
    """Require explicit identifiers so malformed rules cannot broaden a mute."""
    if raw is None:
        return []  # A YAML section with all rules commented out is null.
    if not isinstance(raw, list):
        raise ValueError("ignoredPriceAlerts must be a list")
    rules = []
    for index, item in enumerate(raw):
        label = f"ignoredPriceAlerts[{index}]"
        if not isinstance(item, dict) or set(item) - {"reservation", "prefix", "product", "guest"}:
            raise ValueError(f"{label} accepts only reservation, prefix, product and optional guest")
        values = {}
        for key in ("reservation", "prefix", "product", "guest"):
            if key == "guest" and key not in item:
                continue
            value = item.get(key)
            if type(value) not in (str, int) or not str(value).strip():
                raise ValueError(f"{label}.{key} must be a nonempty identifier")
            values[key] = str(value).strip()
        rules.append(PriceAlertExclusion(**values))
    return rules



# Reservation availability (opt-in)
##################################
@dataclass(frozen=True)
class AvailabilityCategory:
    category: str
    products: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class AvailabilityReservation:
    reservation: str
    categories: Tuple[AvailabilityCategory, ...]
    notify_on_reopen: bool = False


@dataclass(frozen=True)
class AvailabilitySettings:
    reservations: Tuple[AvailabilityReservation, ...]
    dry_run: bool = True
    state_file: str = "data/reservation-availability.json"


class AvailabilityUnknown(ValueError):
    """Incomplete/failed evidence must never be converted into unavailable."""


class AvailabilityCatalogIncomplete(AvailabilityUnknown):
    """Retain returned products without treating a partial catalog as absence."""

    def __init__(self, reason: str, products: list):
        super().__init__(reason)
        self.products = products


@dataclass(frozen=True)
class AvailabilityResult:
    product: str
    title: str
    state: str  # available, unavailable, unknown
    reason: str
    times: Tuple[str, ...] = ()


def availability_category_label(category: str) -> str:
    return {"dining": "Dining reservations", "show": "Shows"}.get(category, category)


def availability_sailing_label(booking: dict) -> str:
    """Presentation label for a monitored sailing without requiring another API call."""
    sailing = availability_date(booking["sailDate"])
    ship_name = booking.get("shipName")
    if not isinstance(ship_name, str) or not ship_name.strip():
        ship_name = str(booking.get("shipCode") or "Unknown ship")
    return f"{ship_name.strip().upper()} ({config.format_date(sailing.strftime('%Y%m%d'))})"


@contextmanager
def suppress_availability_notification_info():
    """Hide Apprise transport-success chatter while preserving warnings/errors."""
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.INFO))
    try:
        yield
    finally:
        logging.disable(previous)


def parse_availability_config(raw: Any) -> Optional[AvailabilitySettings]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("availability must be a mapping")

    def fail(location: str, message: str):
        raise ValueError(f"{location}: {message}")

    def identifier(value: Any, location: str, field_name: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
            fail(location, f"{field_name} must be a nonempty identifier")
        return str(value).strip()

    def keys(obj: dict, allowed: tuple, location: str):
        unknown = set(obj) - set(allowed)
        if unknown:
            fail(location, "unrecognized configuration key(s): " + ", ".join(sorted(map(str, unknown))))

    keys(raw, ("reservations", "dryRun", "stateFile"), "availability")
    reservations = raw.get("reservations")
    if not isinstance(reservations, list) or not reservations:
        raise ValueError("availability.reservations must be a nonempty list")

    parsed_reservations = []
    seen_reservations = set()
    for index, item in enumerate(reservations):
        location = f"availability.reservations[{index}]"
        if not isinstance(item, dict):
            fail(location, "reservation entry must be a mapping")
        keys(item, ("reservation", "dining", "shows", "notifyOnReopen"), location)
        reservation = identifier(item.get("reservation"), location, "reservation")
        if reservation in seen_reservations:
            fail(location, "duplicate reservation; reservation IDs must be unique")
        seen_reservations.add(reservation)

        categories = []
        for config_key, category in (("dining", "dining"), ("shows", "show")):
            value = item.get(config_key, False)
            if value is False:
                continue
            if value is True:
                categories.append(AvailabilityCategory(category))
                continue
            nested = f"{location}.{config_key}"
            if not isinstance(value, dict):
                fail(location, f"{config_key} must be true, false, or a mapping")
            keys(value, ("products",), nested)
            products = value.get("products")
            if not isinstance(products, list) or not products:
                fail(nested, "products must be a nonempty list")
            normalized = tuple(identifier(product, nested, "product") for product in products)
            if len(set(normalized)) != len(normalized):
                fail(nested, "products must not contain duplicates")
            categories.append(AvailabilityCategory(category, normalized))

        if not categories:
            fail(location, "at least one of dining or shows must be enabled")

        notify_on_reopen = item.get("notifyOnReopen", False)
        if not isinstance(notify_on_reopen, bool):
            fail(location, "notifyOnReopen must be true or false")
        parsed_reservations.append(
            AvailabilityReservation(reservation, tuple(categories), notify_on_reopen))

    dry_run = raw.get("dryRun", True)
    if not isinstance(dry_run, bool):
        raise ValueError("availability: dryRun must be true or false")
    state = raw.get("stateFile", "data/reservation-availability.json")
    if not isinstance(state, str) or not state.strip() or state == ":memory:":
        raise ValueError("availability.stateFile must name a persistent file")
    return AvailabilitySettings(tuple(parsed_reservations), dry_run, state)


_AVAILABILITY_PRODUCTS_QUERY = """
query WebProductsByCategory($category: String!, $passengerId: String,
  $shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $reservationId: String,
  $pageSize: Long, $currentPage: Long, $currencyCode: String!) {
  products(category: $category, guestTypes: [ADULT], passengerId: $passengerId,
    shipCode: $shipCode, sailDate: $sailDate, reservationId: $reservationId,
    pageSize: $pageSize, currentPage: $currentPage,
    filter: {includeVariantProducts: false}, currencyIso: $currencyCode) {
    __typename
    ... on CommerceProductResultSuccess {
      commerceProducts { id title type { id } productStatus }
      pageInfo { totalResults totalPages }
    }
    ... on CommerceProductExceptions { exceptions { __typename } }
  }
}
"""


def availability_json(account: AccountInfo, method: str, url: str, **kwargs) -> dict:
    """Reuse existing authentication/retries; never log raw payloads or tokens."""
    response = _execute_api_request(account, method, url, **kwargs)
    if response is None:
        raise AvailabilityUnknown("request failed; previous state preserved")
    if not 200 <= response.status_code < 300:
        raise AvailabilityUnknown(f"Royal API returned HTTP {response.status_code}")
    try:
        data = response.json()
    except (ValueError, TypeError):
        raise AvailabilityUnknown("response is not JSON") from None
    if (not isinstance(data, dict) or data.get("errors") or data.get("error") or
            ("status" in data and data["status"] != 200)):
        raise AvailabilityUnknown("API returned an error")
    return data


def availability_date(value: Any) -> date:
    try:
        return datetime.strptime(str(value).replace("-", ""), "%Y%m%d").date()
    except ValueError:
        raise AvailabilityUnknown("invalid sailing date") from None


def availability_products(account: AccountInfo, booking: dict, category: str) -> list:
    products = []
    seen = set()
    total_pages = None
    total_results = None
    try:
        for page in range(100):
            variables = {"category": category, "passengerId": str(booking["passengerId"]),
                         "shipCode": booking["shipCode"],
                         "sailDate": availability_date(booking["sailDate"]).isoformat(),
                         "reservationId": str(booking["bookingId"]), "pageSize": 12,
                         "currentPage": page, "currencyCode": booking.get("bookingCurrency") or "USD"}
            data = availability_json(account, "POST", "https://aws-prd.api.rccl.com/en/royal/web/graphql",
                                     json_data={"operationName": "WebProductsByCategory",
                                                "variables": variables, "query": _AVAILABILITY_PRODUCTS_QUERY})
            result = (data.get("data") or {}).get("products")
            if not isinstance(result, dict):
                raise AvailabilityUnknown("missing catalog result")
            if result.get("__typename") == "CommerceProductExceptions":
                exceptions = result.get("exceptions")
                if page == 0 and exceptions and all(isinstance(e, dict) and
                        e.get("__typename") == "CommerceProductNotFound" for e in exceptions):
                    return []
                raise AvailabilityUnknown("catalog exception or incomplete pagination")
            if result.get("__typename") != "CommerceProductResultSuccess":
                raise AvailabilityUnknown("unrecognized catalog result")
            info = result.get("pageInfo") or {}
            pages, count = info.get("totalPages"), info.get("totalResults")
            if type(pages) is not int or type(count) is not int or not 0 <= pages <= 100 or count < 0:
                raise AvailabilityUnknown("invalid catalog pagination")
            if total_pages is not None and (pages != total_pages or count != total_results):
                raise AvailabilityUnknown("catalog changed during pagination")
            total_pages, total_results = pages, count
            entries = result.get("commerceProducts")
            if not isinstance(entries, list):
                raise AvailabilityUnknown("missing catalog products")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise AvailabilityUnknown("invalid catalog product")
                if entry["id"] in seen:
                    raise AvailabilityUnknown("duplicate catalog page or product")
                seen.add(entry["id"])
                products.append(entry)
            if page + 1 >= pages:
                if len(products) != count:
                    raise AvailabilityUnknown("incomplete catalog")
                return products
            if not entries:
                raise AvailabilityUnknown("empty intermediate catalog page")
        raise AvailabilityUnknown("catalog page limit reached")
    except (AvailabilityUnknown, KeyError, TypeError, AttributeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, AvailabilityUnknown) else "malformed catalog response"
        raise AvailabilityCatalogIncomplete(reason, products) from None


def availability_party(booking: dict) -> Tuple[Tuple[str, str], ...]:
    guests = booking.get("passengersInStateroom")
    if not isinstance(guests, list) or not guests:
        raise AvailabilityUnknown("booking has no guests")
    party = []
    for guest in guests:
        if not isinstance(guest, dict) or not guest.get("passengerId"):
            raise AvailabilityUnknown("booking guest ID missing")
        party.append((str(guest["passengerId"]), str(booking["bookingId"])))
    if len({g[0] for g in party}) != len(party):
        raise AvailabilityUnknown("duplicate booking guest IDs")
    return tuple(sorted(party))


def availability_eligibility(account: AccountInfo, booking: dict, category: str,
                            product: str, party: tuple) -> dict:
    start = availability_date(booking["sailDate"])
    try:
        nights = int(booking["numberOfNights"])
        if not 0 < nights <= 365:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AvailabilityUnknown("booking duration missing or invalid") from None
    body = {"brandCode": "R", "categoryId": "pt_" + category,
            "guests": [{"id": g, "reservationId": r} for g, r in party],
            "productCode": product, "reservationId": str(booking["bookingId"]),
            "shipCode": booking["shipCode"], "channel": "WEB",
            "startDate": start.strftime("%Y%m%d"), "passengerId": str(booking["passengerId"]),
            "endDate": (start + timedelta(days=nights)).strftime("%Y%m%d"),
            "email": account.username, "cartId": ""}
    data = availability_json(account, "POST",
        "https://aws-prd.api.rccl.com/en/royal/web/commerce-api/eligibility/v1/eligibility",
        json_data=body)
    payload = data.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("offerings"), list):
        for offering in payload["offerings"]:
            if not isinstance(offering, dict):
                raise AvailabilityUnknown("malformed offering")
            try:
                offering_date = datetime.fromisoformat(offering["dateTime"]).date()
            except (KeyError, TypeError, ValueError):
                raise AvailabilityUnknown("invalid offering date") from None
            if not start <= offering_date < start + timedelta(days=nights):
                raise AvailabilityUnknown("offering outside requested sailing")
    return data


def evaluate_availability(data: dict, category: str, product: str,
                          title: str) -> AvailabilityResult:
    try:
        return _evaluate_availability(data, category, product, title)
    except (KeyError, TypeError, AttributeError, ValueError):
        return AvailabilityResult(product, title, "unknown", "malformed eligibility fields")


def _evaluate_availability(data: dict, category: str, product: str,
                           title: str) -> AvailabilityResult:
    """Interpret captured fields, preserving unknown instead of inventing semantics.

    Measures offering inventory independently of existing bookings, conflicts, and
    the offering's active flag. This does not guarantee checkout or a table for
    the full party.
    """
    def result(state, reason, times=()):
        return AvailabilityResult(product, title, state, reason, tuple(times))

    p = data.get("payload") if isinstance(data, dict) else None
    if (not isinstance(p, dict) or data.get("status") != 200 or data.get("error") or
            data.get("warnings") or p.get("productCode") != product or
            p.get("categoryId") != "pt_" + category):
        return result("unknown", "invalid or mismatched eligibility response")
    offerings = p.get("offerings")
    if not isinstance(offerings, list):
        return result("unknown", "missing offerings")
    if not offerings:
        return result("unavailable", "no offerings returned")
    available = []
    uncertain = False
    seen = set()
    for offering in offerings:
        if (not isinstance(offering, dict) or not isinstance(offering.get("id"), str)
                or not offering["id"] or offering["id"] in seen):
            return result("unknown", "invalid or duplicate offering")
        seen.add(offering["id"])
        stamp = offering.get("dateTime")
        try:
            datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            uncertain = True
            continue
        stock_status = offering.get("stockLevelStatus")
        stock = offering.get("stockLevel")
        if stock_status in ("outOfStock", "OUT_OF_STOCK") and type(stock) in (int, float) and stock == 0:
            continue
        if stock_status not in ("inStock", "IN_STOCK") or type(stock) not in (int, float) or not 0 <= stock <= 9999:
            uncertain = True
            continue
        if stock == 0:
            continue
        available.append(stamp)
    if available:
        return result("available", "offering inventory reported", sorted(set(available)))
    if uncertain:
        return result("unknown", "unrecognized inventory or eligibility fields")
    return result("unavailable", "no qualifying offerings")


def availability_scope(account: AccountInfo, booking: dict, category: str) -> str:
    # Keep account and reservation identifiers out of the persisted context key.
    values = [account.username.lower(), booking["shipCode"], availability_date(booking["sailDate"]).isoformat(),
              str(booking["bookingId"]), category]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def availability_time_lines(times: tuple) -> list[str]:
    """Group Royal's wall-clock times by date, using the existing date preference."""
    by_date = {}
    for stamp in times:
        when = datetime.fromisoformat(stamp)
        by_date.setdefault(when.date(), []).append(when.strftime("%H:%M"))
    return [f"{config.format_date(day.strftime('%Y%m%d'))}: {', '.join(values)}"
            for day, values in by_date.items()]


def read_reservation_state(path: Path) -> dict:
    """Only a missing file starts fresh; invalid state never resets alerts."""
    invalid = ("Invalid reservation availability JSON state; check availability.stateFile. "
               "Legacy watch-scoped state is not compatible with reservation/category scopes; "
               "delete the old reservation state file before the first run of this build.")
    try:
        with path.open(encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {"version": 1, "scopes": {}}
    except ValueError:
        raise AvailabilityUnknown(invalid) from None
    if (not isinstance(state, dict) or set(state) != {"version", "scopes"}
            or type(state["version"]) is not int or state["version"] != 1
            or not isinstance(state["scopes"], dict)):
        raise AvailabilityUnknown(invalid)
    for scope, products in state["scopes"].items():
        if not scope or not isinstance(products, dict):
            raise AvailabilityUnknown(invalid)
        for product, row in products.items():
            if (not product or not isinstance(row, dict)
                    or set(row) != {"last_state", "notified"}
                    or row["last_state"] not in ("available", "unavailable")
                    or type(row["notified"]) is not bool):
                raise AvailabilityUnknown(invalid)
    return state


def availability_notification_error(account: AccountInfo) -> Optional[str]:
    """Require lossless overflow handling on the effective account destinations."""
    notifier = notifier_for(account)
    if notifier is None or len(notifier) == 0:
        return "No notification service configured; configure apprise for availability alerts"
    services = sorted({service.service_name for service in notifier
                       if service.overflow_mode != "split"})
    if services:
        return (f"Availability alerts for {account.username}: {', '.join(services)} require "
                "overflow=split; update this account's apprise URLs or the global fallback. "
                "Pending alerts will retry after configuration is corrected.")
    return None


def deliver_availability(settings: AvailabilitySettings, account: AccountInfo, booking: dict,
                         category: AvailabilityCategory, notify_on_reopen: bool, results: list,
                         *, catalog_products: Optional[Set[str]] = None) -> bool:
    """One aggregated alert per reservation/category/run.

    A file lock serializes read/notify/replace. Dry runs never create or advance
    state, so enabling alerts cannot swallow the first notification.
    """
    for result in results:
        color = {"available": GREEN, "unavailable": YELLOW, "unknown": RED}[result.state]
        log(f"        {color}{result.title}: {result.state.capitalize()}{RESET} ({result.reason})")
        for line in availability_time_lines(result.times):
            log(f"          {line}")
    notification_error = availability_notification_error(account)
    if notification_error:
        log_warn(f"        {YELLOW if settings.dry_run else RED}{notification_error}{RESET}")
    if settings.dry_run:
        log(f"        {YELLOW}Availability dry run: no availability notifications or state changes{RESET}")
        return not any(result.state == "unknown" for result in results)

    path = Path(settings.state_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    scope = availability_scope(account, booking, category.category)
    with cabin_state_lock(path):
        state = read_reservation_state(path)
        rows = dict(state["scopes"].get(scope, {}))
        changed = False

        # Narrowing an existing category to selected products should stop tracking
        # unrelated products rather than falsely marking them unavailable.
        if category.products is not None:
            selected = set(category.products)
            pruned = {pid: row for pid, row in rows.items() if pid in selected}
            if pruned != rows:
                rows = pruned
                changed = True

        result_products = {result.product for result in results}
        if catalog_products is not None:
            missing = [AvailabilityResult(pid, pid, "unavailable", "product no longer listed")
                       for pid in rows
                       if pid not in catalog_products and pid not in result_products]
            for result in missing:
                log(f"        {YELLOW}{result.title}: Unavailable{RESET} ({result.reason})")
            results = [*results, *missing]

        candidates = []
        for result in results:
            if result.state == "unknown":
                continue
            row = rows.get(result.product, {})
            notified = row.get("notified", False)
            if result.state == "unavailable" and notify_on_reopen:
                notified = False
            if result.state == "available" and not notified:
                candidates.append(result)
            updated = {"last_state": result.state, "notified": notified}
            if row != updated:
                rows[result.product] = updated
                changed = True

        sent = notification_error is None
        if candidates and sent:
            label = availability_category_label(category.category)
            lines = [f"{label}: {booking['shipCode']} sailing {availability_date(booking['sailDate']).isoformat()}"]
            lines.append("Inventory released; personal conflicts not checked.")
            for result in candidates:
                lines.extend(["", f"{result.title}:"])
                lines.extend(availability_time_lines(result.times[:6]))
                if len(result.times) > 6:
                    lines.append(f"(+{len(result.times) - 6} more times in Cruise Planner)")
            params = urlencode({"bookingId": str(booking["bookingId"]), "shipCode": booking["shipCode"],
                                "sailDate": availability_date(booking["sailDate"]).strftime("%Y%m%d")})
            lines.extend(["", "Cruise Planner:",
                f"https://www.royalcaribbean.com/account/cruise-planner/category/pt_{category.category}?{params}",
                "Times as returned by Royal. Confirm availability in Cruise Planner."])
            if category.category == "dining":
                lines.append("Reported stock does not guarantee a table for the full party.")
            notifier = notifier_for(account)
            try:
                with suppress_availability_notification_info():
                    sent = notifier.notify(body="\n".join(lines),
                        title="Cruise Reservation Availability", body_format=NotifyFormat.TEXT) is True
            except Exception:
                sent = False
            if sent:
                for result in candidates:
                    rows[result.product]["notified"] = True
                    changed = True
            else:
                log_warn(f"        {RED}Notification not confirmed; will retry on a later check{RESET}")

        if changed:
            state["scopes"][scope] = rows
            write_cabin_state(path, state)
    return sent and not any(result.state == "unknown" for result in results)


def process_availability_bookings(account: AccountInfo, bookings: list, settings: AvailabilitySettings) -> bool:
    if not account.is_royal:
        log_warn("[Availability] Only Royal Caribbean is supported")
        return False
    if not isinstance(bookings, list) or any(not isinstance(booking, dict) for booking in bookings):
        raise AvailabilityUnknown("invalid booking list")
    if not settings.reservations:
        return True

    log(f"  {account.friendly_name} for user {account.username}")
    healthy = True
    for reservation in settings.reservations:
        matches = [booking for booking in bookings
                   if str(booking.get("bookingId")) == reservation.reservation]
        if not matches:
            continue
        try:
            if len(matches) != 1:
                raise AvailabilityUnknown("ambiguous booking context")
            booking = matches[0]
            if any(not booking.get(key) for key in ("bookingId", "passengerId", "shipCode", "sailDate")):
                raise AvailabilityUnknown("incomplete booking context")
            if availability_date(booking["sailDate"]) < date.today():
                log(f"    {YELLOW}Departed sailing skipped{RESET}")
                continue
            party = availability_party(booking)
            log(" ")
            log(f"    {BLUE}{availability_sailing_label(booking)}{RESET}")
        except (AvailabilityUnknown, KeyError, TypeError, AttributeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, AvailabilityUnknown) else type(exc).__name__
            log_warn(f"    {RED}Unknown ({reason}); state not advanced{RESET}")
            healthy = False
            continue

        for category in reservation.categories:
            log(f"      {BLUE}{availability_category_label(category.category)}{RESET}")
            try:
                complete = True
                try:
                    products = availability_products(account, booking, category.category)
                except AvailabilityCatalogIncomplete as exc:
                    products = exc.products
                    complete = False
                    healthy = False
                    log_warn(f"        {YELLOW}Incomplete catalog ({exc}); checking returned products. "
                             f"Absent products retain previous state.{RESET}")
                selected = set(category.products) if category.products is not None else None
                scoped_products = [product for product in products
                                   if selected is None or product["id"] in selected]
                catalog_products = {product["id"] for product in scoped_products}
                catalog_ids = {product["id"] for product in products}
                results = []
                for product in scoped_products:
                    pid = product["id"]
                    title = product.get("title") or pid
                    product_type = product.get("type")
                    type_id = product_type.get("id") if isinstance(product_type, dict) else None
                    if not isinstance(type_id, str) or not type_id.startswith("pt_") or len(type_id) <= 3:
                        results.append(AvailabilityResult(pid, title, "unknown", "missing or malformed product type"))
                        continue
                    if type_id != "pt_" + category.category:
                        if selected is not None:
                            results.append(AvailabilityResult(
                                pid, title, "unknown", "unexpected product type: " + type_id))
                        # Category catalogs can contain packages/activities that
                        # are intentionally out of scope. Ignore them silently.
                        continue
                    try:
                        payload = availability_eligibility(
                            account, booking, category.category, pid, party)
                        results.append(evaluate_availability(
                            payload, category.category, pid, title))
                    except (AvailabilityUnknown, KeyError, TypeError, AttributeError, ValueError) as exc:
                        reason = str(exc) if isinstance(exc, AvailabilityUnknown) else "malformed eligibility response"
                        results.append(AvailabilityResult(pid, title, "unknown", reason))

                if selected is not None:
                    for pid in sorted(selected - catalog_ids):
                        results.append(AvailabilityResult(
                            pid, pid, "unavailable" if complete else "unknown",
                            "product not listed" if complete else "product absent from incomplete catalog"))

                if complete and not scoped_products and selected is None:
                    log(f"        {YELLOW}No {category.category} products listed{RESET}")

                healthy = deliver_availability(
                    settings, account, booking, category, reservation.notify_on_reopen,
                    results, catalog_products=catalog_products if complete else None) and healthy
            except (AvailabilityUnknown, KeyError, TypeError, AttributeError, ValueError, OSError, ImportError) as exc:
                if isinstance(exc, AvailabilityUnknown):
                    reason = str(exc)
                elif isinstance(exc, (OSError, ImportError)):
                    reason = ("state storage error (" + type(exc).__name__ +
                              "); check availability.stateFile and directory permissions or overlapping checks")
                else:
                    reason = type(exc).__name__
                log_warn(f"        {RED}Unknown ({reason}); state not advanced{RESET}")
                healthy = False
    return healthy


def finish_availability_run(settings: AvailabilitySettings, found_reservations: set, healthy: bool) -> None:
    """Report missing bookings and incomplete checks after normal price outputs."""
    missing = sorted({reservation.reservation for reservation in settings.reservations
                      if reservation.reservation not in found_reservations})
    if missing:
        log_warn(f"  {RED}Configured reservations were not found: {', '.join(missing)}{RESET}")
        healthy = False
    if not healthy:
        raise AvailabilityUnknown("One or more availability checks or notifications failed; see status lines")
    if settings.reservations:
        log(" ")
        log(f"  {GREEN}Availability checks completed successfully{RESET}")
        log(" ")


def _config_bool(value: Any, default: bool) -> bool:
    """YAML-tolerant boolean. None (a present-but-null key) -> default, and the
    STRINGS "false"/"no"/"off" are false - bool("false") is True in Python."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "y", "1")
    return bool(value)


def _config_id_list(value: Any, key: str) -> List[str]:
    """A reservation id or a list of them, normalized to strings. A bare scalar
    is one id - iterating the string "1234567" would scope to its characters
    and silently disable the feature for every booking."""
    if value is None:
        return []
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return [str(value).strip()] if str(value).strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    raise ValueError(f"{key} must be a reservation id or a list of reservation ids")


def _config_amount(value: Any, key: str) -> Optional[float]:
    """A money threshold; tolerates "$100" / "1,000" as users naturally write them."""
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{key} must be a number (got {value!r})") from None


def load_config_objects(config_path: str) -> CruiseAppConfig:
    """
    Loads, sanitizes, and maps YAML configuration elements into structural dataclass attributes.

    Extracts individual profile arrays, unbooked prospective cruise watchlists,
    and addon tracking lists. Pre-configures functional notification managers (Apprise)
    and handles fractional logic safely (like differentiating a 0.0 value alert from None).
    """
    currency_present = False
    currency_override_present = False

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)
    except UnicodeDecodeError:
        # Fallback for legacy non-UTF-8 files saved on Windows (e.g., CP1252/ANSI)
        with open(config_path, "r") as file:
            raw_data = yaml.safe_load(file)

    # Handle empty files (yaml.safe_load returns None for empty files)
    data = expand_env_vars(raw_data or {})

    # Parse accounts
    accounts = [
        AccountInfo(
            username=a["username"],
            password=a["password"],
            state=a.get("state"),
            senior=a.get("senior", False),
            military=a.get("military", False),
            fire=a.get("fire", False),
            police=a.get("police", False),
            cruise_line=a.get("cruiseLine", "royalcaribbean"),
            apobj=build_apprise(a.get("apprise") or [])
        )
        for a in (data.get("accountInfo") or [])
    ]

    # DESIGN NOTE:  YAML keys will remain camel_case instead of snake_case
    # to not interfere with config files already created by existing script users

    # Parse prospective cruises. Availability-only watches need no target price.
    prospective_cruises = []
    cruise_entries = data.get("cruises")
    if cruise_entries is None:
        cruise_entries = []
    if not isinstance(cruise_entries, list):
        raise ValueError("cruises must be a list")
    for index, item in enumerate(cruise_entries):
        mode = item.get("notificationMode", "price")
        if mode not in ("price", "availability"):
            raise ValueError(f"cruises[{index}].notificationMode must be price or availability")
        prospective_cruises.append(ProspectiveCruise(
            cruise_URL=item["cruiseURL"],
            paid_price=float(item.get("paidPrice", 0) if mode == "availability" else item["paidPrice"]),
            loyalty_number=item.get("loyaltyNumber"), notification_mode=mode))
    cabin_state_file = data.get("cabinAvailabilityStateFile", "data/cabin-availability.json")
    if not isinstance(cabin_state_file, str) or not cabin_state_file.strip():
        raise ValueError("cabinAvailabilityStateFile must be a nonempty file path")

    # Parse watch list
    watch_list = []
    for w in (data.get("watchList") or []):
        # Map out the mandatory fields that MUST exist
        item_kwargs = {
            "name": w["name"],
            "prefix": w["prefix"],
            "product": w["product"],
            "price": float(w["price"]),
        }

        # Inject optional elements if they were actually configured in the file.
        # Otherwise, fall back onto default values
        if "enabled" in w:         item_kwargs["enabled"] = w["enabled"]
        if "guestAgeString" in w:  item_kwargs["guest_age_string"] = w["guestAgeString"]
        if "reservations" in w:    item_kwargs["reservations"] = w["reservations"]

        if "currency" in w:
            currency_present = True

        # Unpack into the constructor
        watch_list.append(WatchListItem(**item_kwargs))

    # Parse Apprise URLs safely
    apprise_urls = [item["url"] for item in (data.get("apprise") or []) if "url" in item]

    # Build the apprise object natively (apprise is an optional dependency)
    apobj = build_apprise(data.get("apprise") or [])

    # Safe initialization of minimum_saving_alert to allow None as well as 0.0
    raw_alert = data.get("minimumSavingAlert", None)
    minimum_saving_alert = float(raw_alert) if raw_alert is not None else None

    if data.get("currencyOverride", None) is not None:
        currency_override_present = True

    # Build and return the global master config object using data.get() for fallback defaults
    config = CruiseAppConfig(
        display_cruise_prices=data.get("displayCruisePrices", True),
        minimum_saving_alert=minimum_saving_alert,
        notify_on_error=data.get("notifyOnError", False),
        show_promos=data.get("showPromos", False),
        request_timeout=int(data.get("requestTimeout", REQUEST_TIMEOUT)),
        date_display_format=data.get("dateDisplayFormat", "%x"),
        log_file=data.get("logFile"),
        history_db=data.get("historyDb"),
        check_for_upgrades=_config_bool(data.get("checkForUpgrades"), False),
        upgrade_alert_below=_config_amount(data.get("upgradeAlertBelow"), "upgradeAlertBelow"),
        upgrade_reservations=_config_id_list(data.get("upgradeReservations"), "upgradeReservations"),
        upgrade_sister_categories=_config_bool(data.get("upgradeSisterCategories"), True),
        cabin_availability_state_file=cabin_state_file,
        availability=parse_availability_config(data.get("availability")),
        output_watch_as_json=data.get("outputWatchAsJson",False),
        output_json_watch_file=data.get("outputJsonFile","output-json-watch.txt"),
        apobj=apobj,
        accounts=accounts,
        watch_list=watch_list,
        ignored_price_alerts=parse_price_alert_exclusions(data.get("ignoredPriceAlerts", [])),
        prospective_cruises=prospective_cruises,
        apprise_urls=apprise_urls,
        reservation_prices=data.get("reservationPricePaid", {}),
        reservation_names=data.get("reservationFriendlyNames", {}),
        # accept both spellings: the original code and README document apprise_test
        apprise_test=data.get("appriseTest", data.get("apprise_test", False)),
        paid_reservations={str(r) for r in (data.get("reservationsPaidInFull") or [])}
    )

    # Set up the custom logger
    setup_hybrid_logging(config.log_file)

    if currency_override_present:
        log(YELLOW + f"Due to RCCL API updates, config file option 'currencyOverride' is deprecated" + RESET)
    if currency_present:
        log(YELLOW + f"Due to RCCL API updates, config file watchlist option 'currency' is deprecated" + RESET)

    return config


def is_agency_booking(booking: dict) -> bool:
    """Returns True if booking payload indicates Travel Agent or Group handling."""
    # 1. Explicit Direct flag override from RC API
    if booking.get("isDirect") is False:
        return True

    # 2. Agency ID fields
    if booking.get("agencyId") or booking.get("travelAgencyId") or booking.get("agencyName"):
        return True

    # 3. Booking Type codes ("G" = Group, "AGENCY", "GROUP", "TA")
    booking_type = str(booking.get("bookingType", "")).upper()
    if booking_type in ("G", "AGENCY", "GROUP", "TA"):
        return True

    # 4. Group boolean flags
    if booking.get("groupBooking") is True or booking.get("groupBookingFlag") is True:
        return True

    return False


def derive_balance_due(booking: dict, cruise_paid_price_from_api: Optional[List[dict]] = None) -> Optional[str]:
    """
    Whether a booking still owes money: True / False, or "TA_UNKNOWN" / None.
    """
    # 1. Direct explicit boolean check
    balance_due = booking.get("balanceDue")
    if balance_due is not None:
        return balance_due

    # 2. Check paidInFull
    if booking.get("paidInFull") is True:
        return False

    # 3. Check explicit amount field
    amount = booking.get("balanceDueAmount")
    if isinstance(amount, (int, float)):
        return amount > 0

    # 4. Check API pricing array fallback
    if cruise_paid_price_from_api:
        for cur_price in cruise_paid_price_from_api:
            if isinstance(cur_price, dict) and cur_price.get("priceTypeCode") == "BALANCE_DUE":
                bal_amount = cur_price.get("amount")
                if isinstance(bal_amount, (int, float)):
                    return bal_amount > 0

    # 5. If data is still missing, it's expected if explicit agency/group indicators are present,
    #    so return "TA_UNKNOWN"
    if is_agency_booking(booking):
        return "TA_UNKNOWN"

    return None


def main() -> None:
    """
    Primary orchestration engine for the cruise pricing validation suite.

    Controls execution sequencing: initializes environments, applies platform-specific
    color adjustments, loads tracking configurations, registers fleet definitions,
    authenticates active user accounts, inspects individual bookings, and processes
    unbooked prospective vacation watchlists.
    """
    availability_enabled = (isinstance(config.availability, AvailabilitySettings)
                            and bool(config.availability.reservations))
    availability_healthy = True
    availability_found = set()
    try:
        # Instantiate clean per-run tracker
        payment_tracker = CheckinPaymentTracker()

        # Watch list table rows
        collected_watch_rows: List[Dict[str, Any]] = []

        history.start_run()

        # Set Time with AM/PM or 24h based on locale
        locale.setlocale(locale.LC_TIME,'')
        timestamp = datetime.now()

        if config.log_file:
            log(f"Logging run to file: {config.log_file}")

        if config.output_watch_as_json:
            log(f"Logging watch list item prices to JSON file: {config.output_json_watch_file}")

        # Since timestamp is a datetime object, convert it to a string or update format_date to handle both
        log(f"Report generated {config.format_date(timestamp.strftime('%Y%m%d'))} {timestamp.strftime('%X')}")

        # A per-account-only setup (no global apprise:) must still trigger the
        # self-test - otherwise the script silently falls through into a real
        # pricing pass instead of confirming notifications are wired up.
        any_notifier = config.apobj is not None or any(a.apobj is not None for a in config.accounts)
        if config.apprise_test and any_notifier:
            if config.apobj is not None:
                config.apobj.notify(body="This is only a test. Apprise is set up correctly", title='Cruise Price Notification Test', body_format=NotifyFormat.TEXT)
            log("Apprise Notification Sent...quitting")

            # Also exercise each account's own notifier, so a misconfigured
            # per-account URL is caught before a real alert is missed.
            for account in config.accounts:
                if account.apobj is not None:
                    account.apobj.notify(body=f"This is only a test for account {account.username}. Apprise is set up correctly",
                                          title='Cruise Price Notification Test', body_format=NotifyFormat.TEXT)

            history.finish_run("apprise_test")
            sys.exit(EXIT_SUCCESS)   # quit() is a site-builtin, absent in frozen builds

        if config.minimum_saving_alert is not None:
            log(YELLOW + f"Only alerting for savings >= {config.minimum_saving_alert:.2f}" + RESET)

        # Generate the list of ship codes
        ship_dictionary = ShipRegistry()
        get_ship_dictionary_web(ship_dictionary)

        # Accounts that could not be checked this run, paired with which
        # phase failed (login or post-login profile fetch) - tracked so the
        # end-of-run summary (and exit status) can't come out looking green
        # when one account in a multi-account config silently never got
        # checked, and so the persisted history row can still tell a stale
        # password from a transient profile-API failure after the fact.
        failed_accounts: List[Tuple[str, str]] = []
        failed_watches: List[int] = []

        for account_info in config.accounts:
            log(f"\nUsing {account_info.friendly_name} for user {account_info.username}")
            log(f"\t{account_info.friendly_name} loyalty number will be used for checking cabin prices")

            # Login in to this account and get the profile information.
            #
            # login() intentionally still raises SystemExit on failure (a stale
            # password, a WAF block, etc.) - that contract is load-bearing for
            # an out-of-tree caller that uses login() directly as a standalone
            # credential probe. Left unguarded, though, that SystemExit would
            # propagate straight out of this loop and take the whole
            # multi-account run down over ONE bad account, silently skipping
            # every account queued after it. So the caller - not login() -
            # absorbs the failure: catch it (and any other unexpected
            # Exception from either call) per account, report it loudly, and
            # move on to the next account. SystemExit derives from
            # BaseException rather than Exception, so it has to be named
            # explicitly here; a bare `except Exception` would not catch it.
            #
            # login() and get_profile() are guarded together (an account that
            # can't log in can't have its profile fetched either, so both
            # failures are skip-and-continue the same way), but a phase flag
            # tracks which of the two actually raised. A get_profile() error
            # (e.g. a transient 500) on an account whose login SUCCEEDED is a
            # different failure than a bad password, and must not be reported
            # to the user as a login problem - that would send them chasing
            # the wrong fix.
            account_phase = "login"
            try:
                account_info.access = login(account_info)
                account_phase = "profile"
                state_from_profile, loyalty_number, c_and_a_points = get_profile(account_info)
            except (SystemExit, Exception) as account_err:
                # A failed profile lookup skips the voyage cleanup below.
                # Release its authenticated session before error notification,
                # which can itself raise.
                if account_phase == "profile":
                    try:
                        account_info.access.session.close()
                    except Exception:
                        log(YELLOW + "Session cleanup failed after profile lookup failure; continuing." + RESET)
                failed_accounts.append((account_info.username, account_phase))
                if account_phase == "login":
                    skip_reason = "could not be logged in"
                    notify_title = 'Cruise Price Account Login Failed'
                else:
                    skip_reason = "logged in, but its profile could not be fetched"
                    notify_title = 'Cruise Price Account Profile Fetch Failed'
                log(RED + f"\n[SKIPPED] {account_info.friendly_name} ({account_info.username}) {skip_reason} "
                          f"this run (see the error above) - skipping this account and continuing "
                          f"with the rest of the run." + RESET)

                # Route the failure through the same per-account/global
                # notifier resolution used everywhere else in this script, so
                # a bad password reaches the user the same way a price alert
                # would - not a bespoke notification path. Gated the same way
                # as the module-level fatal-error notifier below: honor the
                # notifyOnError opt-out, and only fire when the resolved
                # notifier actually has URLs registered.
                account_notifier = notifier_for(account_info)
                if config.notify_on_error and account_notifier is not None and len(account_notifier) > 0:
                    # login()'s SystemExit carries nothing but a bare exit
                    # status (e.g. SystemExit(1), whose str() is just "1") -
                    # the real diagnosis (a stale password, a WAF block,
                    # etc.) was already logged by login() itself and a bare
                    # "1" would only be noise to a user who, by definition,
                    # is being notified because they won't read the log. A
                    # plain Exception (e.g. from get_profile()) DOES carry a
                    # useful message, so that case is still included.
                    if isinstance(account_err, SystemExit) and not isinstance(account_err.code, str):
                        detail = "See the run log for the exact reason."
                    else:
                        detail = str(account_err)
                    account_notifier.notify(
                        body=f"Account {account_info.username} ({account_info.friendly_name}) {skip_reason} "
                             f"this run and was skipped. {detail}",
                        title=notify_title,
                        body_format=NotifyFormat.TEXT,
                    )
                continue

            if account_info.state is None:
                account_info.state = state_from_profile

            # This block bundles all age, loyalty, and regional residency codes
            # together. If you want to check prices for a specific state or check senior discounts,
            # this profile ensures the request matches those promotional brackets.
            # July 2026: a Royal Caribbean loyalty PDF briefly listed this benefit
            #            at 175 points (any Diamond Plus tier), but that was a typo
            #            corrected two days later - the single supplement discount
            #            still requires 340 points, and the original script reverted
            #            to match. Keep the override switch in case RCCL ever makes
            #            the 175-point change for real.
            diamond_plus_override = False
            has_dp340_bracket = (c_and_a_points >= 340) or (diamond_plus_override and c_and_a_points >= 175)

            discounts = DiscountProfile(
                loyalty_number=loyalty_number,
                state=account_info.state,
                senior=account_info.senior,
                military=account_info.military,
                fire=account_info.fire,
                police=account_info.police,
                dp340=has_dp340_bracket
            )

            # Gather the information on all voyages under the current account
            try:
                bookings = get_voyages(
                   account_info,
                   discounts,
                   ship_dictionary,
                   payment_tracker=payment_tracker,
                   collected_watch_rows=collected_watch_rows,
                 )
                if availability_enabled and account_info.is_royal:
                    if isinstance(bookings, list) and all(isinstance(b, dict) for b in bookings):
                        for booking in bookings:
                            ship_code = booking.get("shipCode")
                            if ship_code and not booking.get("shipName"):
                                booking["shipName"] = ship_dictionary.get_ship(ship_code)
                        availability_found.update(str(b.get("bookingId")) for b in bookings)
                        log(f"\n{BLUE}Reservation Availability Watches{RESET}")
                        availability_healthy = process_availability_bookings(
                            account_info, bookings, config.availability) and availability_healthy
                    else:
                        log_warn("[Availability] Booking lookup failed; previous state retained")
                        availability_healthy = False
            finally:
                # Close the account session even when a booking raises, so
                # sessions don't leak across the remaining accounts
                account_info.access.session.close()
            if len(config.accounts) > 1:
                log("Sleeping for 5 seconds to allow API to cool down between accounts")
                time.sleep(ACCOUNT_COOLDOWN_SECONDS)

        # Process the anonymous prospective cruise watchlist using the config dataclass property
        if getattr(config, 'prospective_cruises', None):
            log(f"\n{BLUE}Processing Prospective Cruise Watchlist...{RESET}")

            # Establish a clean, isolated session for tracking
            anon_session = new_api_session()
            try:
                for watch_index, prospective_cruise in enumerate(config.prospective_cruises, 1):

                    # Build the mock AccountInfo structure with an anonymous access context
                    prospective_account = AccountInfo(
                        username="AnonymousWatch",
                        password="",
                        cruise_line="royalcaribbean",
                        access=APIAccess(token=None, id=None, session=anon_session)
                    )

                    # Build the prospective booking structure
                    cruise_url = prospective_cruise.cruise_URL
                    paid_price = float(prospective_cruise.paid_price)
                    prospective_booking = {
                        "url": cruise_url,
                        "paidPriceStruct": {
                            "paidPrice": paid_price
                        },
                        "finalPaymentDate": None,
                        "shipCode": "",
                        "sailDate": "",
                        "packageCode": "",
                        "stateroomType": "NONE"
                    }

                    # STRATEGY NOTE: 'automaticURL=False' forces the scraper to use manually extracted browser
                    # URL context components. This prevents the code from executing automated customer profile queries,
                    # keeping this entire script iteration running safely, anonymously, and unauthenticated.
                    prospective_target = {'paid_price': paid_price}
                    try:
                        get_cruise_price(prospective_account, prospective_booking, ship_dictionary, automatic_URL=False, paid_price_struct=prospective_target,
                                         notification_mode=getattr(prospective_cruise, "notification_mode", "price"))
                    except CabinAvailabilityError as exc:
                        failed_watches.append(watch_index)
                        log(RED + f"[FAILED] Cabin availability watch {watch_index}: {exc}; continuing with remaining watches." + RESET)

            finally:
                anon_session.close()

        # A scoped id that matched no booking is almost always a typo - say so,
        # since the symptom is otherwise just "no upgrade table appeared"
        _scope = config.upgrade_reservations if isinstance(config.upgrade_reservations, (list, set, tuple)) else []
        if config.check_for_upgrades is True and _scope:
            _unmatched = [str(r) for r in _scope if str(r) not in _UPGRADE_SCOPE_SEEN]
            if _unmatched:
                log(YELLOW + f"upgradeReservations: no booking matched {', '.join(_unmatched)} "
                             f"- check the reservation id(s)" + RESET)

        # Summary table of upcoming check-in and final-payment dates for booked sailings
        payment_tracker.print_table()

        # Write the watchlist price results to JSON for external consumption
        if config.output_watch_as_json:
            write_watch_price_json(collected_watch_rows, config.output_json_watch_file)

        failure_summaries = []
        if availability_enabled:
            try:
                finish_availability_run(config.availability, availability_found, availability_healthy)
            except AvailabilityUnknown as exc:
                log_warn(str(exc))
                failure_summaries.append(str(exc))
        if failed_accounts:
            # At least one account never got checked this run. A quiet exit
            # 0 here would look identical to a fully-successful run to any
            # scheduler/log-scraper watching this process, hiding exactly the
            # kind of silent partial loss this guard exists to prevent - so
            # both the history row and the process exit code say otherwise.
            failed_usernames = [username for username, _phase in failed_accounts]
            log(RED + f"\n{len(failed_accounts)} of {len(config.accounts)} account(s) could not be checked this "
                      f"run and were SKIPPED: {', '.join(failed_usernames)}. Prices for those accounts were NOT "
                      f"checked. See the [SKIPPED] line(s) above for whether each was a login or profile-fetch "
                      f"failure." + RESET)
            # Name each account's failure phase (login vs profile) in the
            # persisted summary too, not just the console [SKIPPED] lines -
            # a later reader of the history DB only has this string, and
            # without the phase they can't tell a stale password from a
            # transient profile-API failure.
            failure_detail = ", ".join(f"{username} ({phase})" for username, phase in failed_accounts)
            failure_summaries.append(
                f"{len(failed_accounts)} of {len(config.accounts)} account(s) could not be checked: "
                f"{failure_detail}")
        if failed_watches:
            watch_summary = f"{len(failed_watches)} cabin availability watch(es) failed: " + ", ".join(map(str, failed_watches))
            log(RED + watch_summary + ". See the [FAILED] lines above; pending alerts will retry." + RESET)
            failure_summaries.append(watch_summary)
        if failure_summaries:
            history.finish_run("partial_failure", "; ".join(failure_summaries))
            # Distinct from the fatal exit 1 below - see EXIT_PARTIAL_FAILURE.
            sys.exit(EXIT_PARTIAL_FAILURE)

        history.finish_run("ok")

    except Exception as e:
        # Mark the price-history run as failed before the module-level handler reports it
        history.finish_run("error", f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    config_path = get_config_path()

    try:
        # Load everything once. Logging, Apprise, and YAML values are now armed.
        config = load_config_objects(config_path)

        # Opt-in SQLite price-history sink; PriceHistory is a no-op when history_db is unset
        history_db_path = config.history_db
        if history_db_path and platform.system() == "iOS":
            history_db_path = os.path.expanduser('~/Documents') + "/" + history_db_path
        history = PriceHistory(history_db_path)

        # Now that the config object is fully built, pass control to main
        main()

    except FileNotFoundError:
        print("\n[!]No Configuration File Found")

        # If running non-interactively, just auto-create it
        # Otherwise, ask the user.
        is_interactive = sys.stdin.isatty()
        if is_interactive:
            user_input = input("Would you like me to download a barebones config.yaml file for you? (y/n): ")
            user_choice = user_input.lower().strip()
        else:
            # Default to yes for non-interactive operation
            user_choice = "y"

        if user_choice == "y":
            try:
                print("Downloading sample configuaration file...")
                url = 'https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/SAMPLE-SIMPLE-config.yaml'
                response = requests.get(url, timeout=SHORT_REQUEST_TIMEOUT)
                response.raise_for_status()

                local_file_name = "config.yaml"
                if platform.system() == "iOS":
                    local_file_name = os.path.expanduser('~/Documents') + "/config.yaml"

                with open(local_file_name, "wb") as f:
                    f.write(response.content)

                print(f"\n[+] Success: Created '{local_file_name}' in the current directory.")
                print("--> Please edit Username/password then run the tool again")

            except requests.RequestException as req_err:
                sys.stderr.write(f"Failed to download sample configuration file from GitHub: {req_err}\n")
                sys.exit(EXIT_TOTAL_FAILURE)
        else:
            print("Exiting. Please create a valid config.yaml file manually.")
            sys.exit(EXIT_TOTAL_FAILURE)

    except Exception as exc:
        error_summary = f"{type(exc).__name__}: {exc}"

        # Standard fallback if the config failed to load entirely before the try block
        if config is not None:
            date_part = config.format_date(datetime.now().strftime("%Y%m%d"))
        else:
            date_part = datetime.now().strftime("%m/%d/%Y")
        timestamp = f"{date_part} {datetime.now().strftime('%X')}"

        # Using sys.stderr here is correct for standard error streams
        sys.stderr.write(f"ERROR: {error_summary}\n")
        traceback.print_exc()

        # Safe structural verification for notifications
        if config is not None and config.notify_on_error and config.apobj:
            if len(config.apobj) > 0:
                body = f"Script failed at {timestamp}\n{error_summary}"
                config.apobj.notify(body=body, title='Cruise Price Script Error', body_format=NotifyFormat.TEXT)

        sys.exit(EXIT_TOTAL_FAILURE)

import argparse
import base64
import json
import locale
import logging
import os
import platform
import re
import requests
import sys
import traceback
import time
import yaml

from apprise import Apprise
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse


##################################
# Global variables
##################################
user_agent_web = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0'
appkey_web = 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm'

# TO ASK: Which to use
#RED = '\033[91m'
#GREEN = '\033[92m'
#YELLOW = '\033[93m'
#BLUE = '\033[94m'
RED = '\033[1;31;40m'
GREEN = '\033[1;32;40m'
YELLOW = '\033[1;33;40m'
BLUE = '\033[1;34;40m'
RESET = '\033[0m' # Resets color to default

config: CruiseAppConfig = None

# Define global logging hooks so they are available everywhere in the script module
log = None
log_warn = None
log_err = None

##################################
# Helper functions
##################################
def print_response(response: Union[Dict[str, Any], List[Any], str, requests.Response]) -> None:
#def print_response(response):
    """
    Debug utility to format and display raw API responses.
    
    Transforms nested API response JSON payloads or dictionary objects into standard, 
    indented strings for readable terminal diagnosis during live testing.
    """
    json_resp = json.dumps(response, indent=2)
    print("API returned output:")
    print(json_resp)

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
    
def _execute_api_request(
    account_info: Optional[AccountInfo],
    method: str,
    url: str,
    params: Optional[dict] = None,
    data: Optional[Union[str, dict]] = None,
    headers: Optional[dict] = None,
    timeout: int = 15,
    exit_on_fail: bool = True
) -> Optional[requests.Response]:
#def _execute_api_request(
#    account_info: AccountInfo,
#    method: str,
#    url: str,
#    params: Optional[dict] = None,
#    data: Optional[Union[str, dict]] = None,
#    headers: Optional[dict] = None,
#    timeout: int = 15,
#    exit_on_fail: bool = True
#) -> Optional[requests.Response]:
    """
    Unified API execution engine for all cruise line network interactions.
    
    Centralizes tracking parameters, developer keys, and connect timeouts. 
    If an active session profile exists, it automatically injects 'Access-Token' 
    and account tracking headers into the request context.
    
    Setting exit_on_fail=False allows non-essential network lookups (such as 
    historical loyalty status summaries) to fail silently without breaking the script.

    NOTE: If an account context is passed, we reuse its persistent state 
    session (maintaining cookies and token headers). For anonymous/watchlist lookups, 
    we fall back onto an ephemeral, short-lived 'requests.Session()' instance on-the-fly.
    """
    # Start with any caller-specified override headers, or an empty base
    final_headers = headers.copy() if headers else {}
    
    # Inject corporate authentication layers if a live session exists
    if account_info and account_info.access:
        if "Access-Token" not in final_headers and account_info.access.token:
            final_headers["Access-Token"] = account_info.access.token
        if "vds-id" not in final_headers and account_info.access.id:
            final_headers["vds-id"] = account_info.access.id
        if "account-id" not in final_headers and account_info.access.id:
            final_headers["account-id"] = account_info.access.id
            
    # Always include the baseline developer key
    if "AppKey" not in final_headers:
        final_headers["AppKey"] = appkey_web

    # Choose the target network session channel
    session_context = account_info.access.session if (account_info and account_info.access) else requests

    # Fire the request dynamically
    try:
        response = session_context.request(
            method=method.upper(),
            url=url,
            params=params,
            data=data,
            headers=final_headers,
            timeout=timeout
        )
        response.raise_for_status()
        return response
        
    except Exception as e:
        error_msg = f"Can't contact cruise line servers; please try again later\n(program exception '{e}')"
        if exit_on_fail:
            print(error_msg)
            sys.exit(1)
        else:
            logging.warning(f"Non-critical API interaction skipped (exception: {e})")
            return None

def aboveAgeOnSailDate(birthDate: str, sailDate: str, ageThreshold: int) -> bool:
#def aboveAgeOnSailDate(birthDate, sailDate, ageThreshold):
    """
    Determines if a passenger meets a specific age requirement on their voyage date.
    
    Accepts raw date stamps formatted as 'YYYYMMDD'. Evaluates whether the current 
    calendar anniversary month and day have been crossed on the ship's sailing 
    timeline to account for fractional year offsets.
    """
    dt1 = datetime.strptime(birthDate, "%Y%m%d")
    dt2 = datetime.strptime(sailDate, "%Y%m%d")
    
    age = dt2.year - dt1.year
    # Adjust if birthday hasn’t happened yet this year
    if (dt2.month, dt2.day) < (dt1.month, dt1.day):
        age -= 1
    
    return age >= ageThreshold

##################################
# Classes
##################################
class EasyLogger:
    """
    A simplified logging manager wrapper providing standalone function shortcuts.
    
    Exposes functional hooks ('log', 'log_warn', 'log_err') globally across the 
    script module so that less-experienced developers don't have to manage raw, 
    verbose logging object initializations.
    """
    def __init__(self, logger_instance: logging.Logger) -> None:
        self._logger = logger_instance
        
    def __call__(self, message: Any, *args: Any, **kwargs: Any) -> None:
#    def __call__(self, message, *args, **kwargs):
        """
        Maps log("text") directly to logger.info
        that is, define log("text") as a shorthand
        for logger.info("text")
        """
        self._logger.info(message, *args, **kwargs)
        
    def warn(self, message: Any, *args: Any, **kwargs: Any) -> None:
#    def warn(self, message, *args, **kwargs):
        """Redirects log_warn("text") calls to logger.warning"""
        self._logger.warning(message, *args, **kwargs)
        
    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
#    def error(self, message, *args, **kwargs):
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
#    def __init__(self, logger_func):
        self.logger_func = logger_func
        self._buffer = []

    def write(self, buf: str) -> None:
#    def write(self, buf):
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
#    ANSI_REGEX = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def filter(self, record: logging.LogRecord) -> bool:
#    def filter(self, record):
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

class ShipRegistry:
    """
    In-memory dictionary cache tracking valid fleet vessel assets.
    
    Maintains a catalog of hull profiles. If a lookup code cannot be matched 
    from server manifests, it returns a safe fallback instance to prevent 
    downstream execution faults.
    """
    def __init__(self)->None:
#    def __init__(self):
        self.ships: dict[str, Ship] = {}

    def add_from_payload(self, payload: List[Dict[str, Any]]) -> None:
#    def add_from_payload(self, payload):
        """
        Populates the registry map by parsing raw ship arrays from corporate servers.
        
        Iterates through incoming server manifests, extracts the primary identification 
        tokens ('shipCode' and 'name'), and caches them as structural Ship objects. 
        Guarantees that subsequent UI logs can map technical codes to user-friendly vessel names.
        """
        for item in payload:
            code = item.get("shipCode")
            name = item.get("name", "Unknown Ship")
            self.ships[code] = Ship(code=code, name=name)
            
    def get_ship(self, code: str) -> Ship:
#    def get_ship(self, code):
        # Returns the ship if found, otherwise a new 'Unknown' ship object
        return self.ships.get(code, Ship(code=code))

@dataclass
class CruiseURLParams:
    """
    Data container used to build specific consumer booking pricing requests.
    
    Assembles voyage, demographic, and state residency identifiers. Includes corporate 
    validation logic to strip 'All-Included' fare upgrades from Royal Caribbean paths, 
    as that option applies exclusively to Celebrity Cruises.
    """
    packageCode: str = ""
    sailDate: str = ""
    shipCode: str = ""
    cabinClassString: str = ""
    stateroomTypeName: str = ""
    stateroomSubtype: str = ""
    stateroomCategoryCode: str = ""
    currencyCode: str = "USD"
    bookingOfficeCountryCode: str = "USA"
    isRoyal: bool = True
    username: Optional[str] = None
    couponCode: Optional[str] = None
    state: Optional[str] = None
    loyaltyNumber: Optional[str] = None
    senior: bool = False             
    fire: bool = False               
    police: bool = False             
    military: bool = False           
    numberOfAdults: str = "2"
    numberOfChildren: str = "0"
    
    # Pricing addon flags required by apply_overrides and parseProvidedURL
    allIncluded: bool = False
    refundable: bool = False
    travelInsurance: bool = False
    prepaidGrats: bool = False

    @property
    def api_brand(self) -> str:
        # TO ASK: Should this endpoint utilize accountInfo.api_brand to dynamically construct the path for Celebrity Cruises?
        return "royal"
#        return "celebrity" if self.is_celebrity else "royal"

    @property
    def url_brand(self) -> str:
        """
        Dynamically provides the domain segment for room pricing requests.
        """
        return "royalcaribbean" if self.isRoyal else "celebritycruises"

    def apply_overrides(self, overrides: Optional[Dict[str, Any]]) -> None:
#    def apply_overrides(self, overrides: Optional[dict]):
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

        # Direct attribute mapping based on getCruisePrice
        self.allIncluded = overrides.get("allInUpgrade", self.allIncluded)
        self.prepaidGrats = overrides.get("gratuities", self.prepaidGrats)
        self.travelInsurance = overrides.get("tripInsurance", self.travelInsurance)
        self.refundable = overrides.get("refundable", self.refundable)
        self.couponCode = overrides.get("couponCode", self.couponCode)
        self.stateroomCategoryCode = overrides.get("categoryOverride", self.stateroomCategoryCode)
        self.stateroomSubtype = overrides.get("subcategoryOverride", self.stateroomSubtype)
        self.senior = overrides.get("senior", self.senior)
        self.military = overrides.get("military", self.military)
        self.police = overrides.get("police", self.police)
        self.fire = overrides.get("fire", self.fire)
        self.loyaltyNumber = overrides.get("loyaltyNumber", self.loyaltyNumber)
        self.state = overrides.get("state", self.state)
        
        # Enforce corporate structural constraints natively
        if self.allIncluded and self.isRoyal:
            print("Royal Does Not Have All In Fare\nRemoving All In Fare. Check Documentation")
            self.allIncluded = False

@dataclass
class DiscountProfile:
    """
    Demographic profile containing localized and corporate discount indicators.
    
    Feeds pricing engines with targeted parameters like regional residency, age 
    milestones, military backgrounds, or elite loyalty brackets (such as the 'dp340' 
    single-supplement tier modification).
    """
    loyaltyNumber: str
    state: Optional[str]
    senior: bool
    military: bool
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
    passengerId: Optional[str]
    passengerName: str
    room: Optional[str]
    paidPrice: float
    currency: str
    guestAgeString: str
    salesUnit: Optional[Any] = None
    forWatch: bool = True
    orderCode: str = "WATCH-LIST"
    orderDate: str = "Watch List"
    owner: bool = True
    reservations: List[str] = field(default_factory=list)

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
    police: bool = False
    cruiseLine: Optional[str] = "royalcaribbean"

    # Defaulting access to None allows us to load the YAML configuration safely 
    # before the script logs in and populates it.
    access: Optional[APIAccess] = None
    found_items: List[str] = field(default_factory=list)

    @property
    def is_royal(self) -> bool:
        return self.cruiseLine.lower() in ("royal", "royalcaribbean", "royal caribbean", "r")

    @property
    def is_celebrity(self) -> bool:
        # Put in safety checking for celebrity (for example, "carnival" would be read as celebrity
        # if we just check for strings that start with 'c')
        return self.cruiseLine.lower() in ("celebrity", "celebritycruises", "celebrity cruises", "c")

    @property
    def api_brand(self) -> str:
        # TO ASK: Should this endpoint utilize accountInfo.api_brand to dynamically construct the path for Celebrity Cruises?
        return "royal"
#        return "celebrity" if self.is_celebrity else "royal"

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
    guestAgeString: str = "adult"
    currency: str = "USD"
    reservations: Optional[List[str]] = field(default_factory=list)

@dataclass
class ProspectiveCruise:
    """
    An unbooked, prospective voyage monitored for price drops.
    
    Pairs a web browser URL with the baseline price targets configured in 
    the local environment YAML manifest.
    """
    cruiseURL: str
    paidPrice: float
    loyaltyNumber: Optional[str] = None

@dataclass
class CruiseAppConfig:
    """
    Master configuration repository storing all global application run states.
    
    Tracks terminal formats, output log paths, notifications, target accounts, 
    and watchlist arrays. Includes a safe JSON serializer method to easily print 
    the configuration for debugging.
    """
    # Global Settings
    dateDisplayFormat: Optional[str] = "%x"
    logFile: Optional[str] = None
    apprise_urls: List[str] = field(default_factory=list)
    notifyOnError: bool = False
    appriseTest: Optional[bool] = None
    currencyOverride: Optional[str] = None

    displayCruisePrices: bool = True
    minimumSavingAlert: Optional[float] = None
    showPromos: bool = True
    
    # Complex Objects
    accounts: List[AccountInfo] = field(default_factory=list)
    watch_list: List[WatchListItem] = field(default_factory=list)
    prospective_cruises: List[ProspectiveCruise] = field(default_factory=list)
    
    # Mapping Dictionaries
    reservation_prices: Dict[str, float] = field(default_factory=dict)
    reservation_names: Dict[str, str] = field(default_factory=dict)

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
        return datetime.strptime(clean_str, "%Y%m%d").strftime(self.dateDisplayFormat)

##################################
# Main execution path
##################################
def main() -> None:
    """
    Primary orchestration engine for the cruise pricing validation suite.
    
    Controls execution sequencing: initializes environments, applies platform-specific 
    color adjustments, loads tracking configurations, registers fleet definitions, 
    authenticates active user accounts, inspects individual bookings, and processes 
    unbooked prospective vacation watchlists.
    """
    try:
        # Set Time with AM/PM or 24h based on locale    
        locale.setlocale(locale.LC_TIME,'')
        timestamp = datetime.now()
        print(" ")
    
        if config.logFile:
            print(f"Logging run to file: {config.logFile}")
                
        # Since timestamp is a datetime object, convert it to a string or update format_date to handle both
        print(f"Report generated {config.format_date(timestamp.strftime('%Y%m%d'))} {timestamp.strftime('%X')}")
            
        if config.apobj is not None and config.appriseTest:
            config.apobj.notify(body="This is only a test. Apprise is set up correctly", title='Cruise Price Notification Test')
            print("Apprise Notification Sent...quitting")
            quit()

        if config.minimumSavingAlert is not None:
            print(YELLOW + f"Only alerting for savings >= {config.minimumSavingAlert}" + RESET)
        else:
            print("config.minimumSavingAlert was NONE")

        if config.currencyOverride:
            print(YELLOW + f"Overriding Current Price Currency to {config.currencyOverride}" + RESET)

        # Generate the list of ship codes
        shipDictionary = ShipRegistry()
        getShipDictionaryWeb(shipDictionary)
            
        for accountInfo in config.accounts:
            print(f"\n  Using {accountInfo.friendly_name} for user {accountInfo.username}")
            print(f"        {accountInfo.friendly_name} loyalty number will be used for checking cabin prices")

            accountInfo.access = login(accountInfo)
            stateFromProfile, loyaltyNumber, cAndAPoints = getProfile(accountInfo)
            if accountInfo.state is None:
                accountInfo.state = stateFromProfile

            # NOTE: This block bundles all age, loyalty, and regional residency codes 
            # together. If you want to check prices for a specific state or check senior discounts, 
            # this profile ensures the request matches those promotional brackets.
            discounts = DiscountProfile(
                loyaltyNumber=loyaltyNumber,
                state=accountInfo.state,
                senior=accountInfo.senior,
                military=accountInfo.military,
                police=accountInfo.police,
                dp340=(cAndAPoints >= 340)
            )

            getVoyages(accountInfo, discounts, shipDictionary)

            accountInfo.access.session.close()
            print("Sleeping for 5 seconds to allow API to cool down between accounts")
            time.sleep(5)

        # Process the anonymous prospective cruise watchlist using the config dataclass property
        if getattr(config, 'prospective_cruises', None):
            print("\nProcessing Prospective Cruise Watchlist...")

            # Establish a clean, isolated session for tracking
            anon_session = requests.Session()
            for prospective_cruise in config.prospective_cruises:
                
                # Build the mock AccountInfo structure with an anonymous access context
                prospective_account = AccountInfo(
                    username="AnonymousWatch",
                    password="",
                    cruiseLine="royalcaribbean", 
                    access=APIAccess(token=None, id=None, session=anon_session)
                )
                
                # Extract properties from the current prospective object attribute style
                # (Using getattr behavior to match your WatchListItem object property access style)
                cruise_url = getattr(prospective_cruise, 'cruiseURL', '')
                paid_price = float(getattr(prospective_cruise, 'paidPrice', 0.0))
                
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
                getCruisePrice(prospective_account, prospective_booking, shipDictionary, automaticURL=False)

            # Safely release the connection socket resources back to the OS
            anon_session.close()

    except Exception as e:
        # Let the global catch-all at the module entry point handle unexpected execution faults
        raise e                

def setup_hybrid_logging(log_file_path: Optional[str] = None) -> None:
    """
    Initializes the tracking environment, functional logging aliases, and file captures.
    
    Configures two destination tracks: a standard terminal console output stream 
    preserving live ANSI text colors, and an optional plaintext file log tracking 
    run milestones with ANSI styling expressions filtered out.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()  # Avoid handler duplication

    # A. Terminal Stream Handler (Keeps original ANSI terminal colors)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    if platform.system() == "iOS":
        console_handler.addFilter(StripAnsiFilter())
    
    root_logger.addHandler(console_handler)

    # B. Plain Text File Handler (Only built if a log file path is supplied)
    if log_file_path:
        # Write the run execution start sequence first
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"
        
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(delimiter)
            
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter('%(message)s'))
            
            # Keep the validated filter that strips ANSI color symbols out of text files
            file_handler.addFilter(StripAnsiFilter())
            root_logger.addHandler(file_handler)
            
        except IOError as e:
            sys.stderr.write(f"Warning: Could not open log file '{log_file_path}': {e}\n")

    # C. Activate the Hybrid Magic Core
    easy_log_instance = EasyLogger(root_logger)
    
    global log, log_warn, log_err
    log = easy_log_instance
    log_warn = easy_log_instance.warn
    log_err = easy_log_instance.error

    # D. Safely Intercept Standard print() System-Wide
    #    This redirects stdout to the custom logger
    sys.stdout = PrintRedirector(root_logger.info)

def load_config_objects(config_path: str) -> CruiseAppConfig:
    """
    Loads, sanitizes, and maps YAML configuration elements into structural dataclass attributes.
    
    Extracts individual profile arrays, unbooked prospective cruise watchlists, 
    and addon tracking lists. Pre-configures functional notification managers (Apprise) 
    and handles fractional logic safely (like differentiating a 0.0 value alert from None).
    """
    with open(config_path, 'r') as file:
        data = yaml.safe_load(file)    # Parse accounts

    accounts = [
        AccountInfo(
            username=a["username"],
            password=a["password"],
            state=a.get("state"),
            senior=a.get("senior", False),
            military=a.get("military", False),
            police=a.get("police", False),
            cruiseLine=a.get("cruiseLine", "royalcaribbean")
        )
        for a in data.get("accountInfo", [])
    ]
    
    # Parse prospective cruises
    prospective_cruises = [
        ProspectiveCruise(
            cruiseURL=c["cruiseURL"],
            paidPrice=float(c["paidPrice"]),
            loyaltyNumber=c.get("loyaltyNumber")
        )
        for c in data.get("cruises", [])
    ]
    
    # Parse watch list
    watch_list = []
    for w in data.get("watchList", []):
        # Map out the mandatory fields that MUST exist
        item_kwargs = {
            "name": w["name"],
            "prefix": w["prefix"],
            "product": w["product"],
            "price": float(w["price"]),
        }
    
        # Inject optional elements if they were actually configured in the file.
        # Otherwise, fall back onto default values
        if "enabled" in w:        item_kwargs["enabled"] = w["enabled"]
        if "guestAgeString" in w:  item_kwargs["guestAgeString"] = w["guestAgeString"]
        if "currency" in w:        item_kwargs["currency"] = w["currency"]
        if "reservations" in w:    item_kwargs["reservations"] = w["reservations"]
    
        # Unpack into the constructor
        watch_list.append(WatchListItem(**item_kwargs))
    
    # Parse Apprise URLs safely
    apprise_urls = [item["url"] for item in data.get("apprise", []) if "url" in item]

    # Build the apprise object natively
    apobj = None
    if apprise_urls:
        apobj = Apprise()
        for url in apprise_urls:
            apobj.add(url)

    # Safe initialization of minimumSavingAlert to allow None as well as 0.0
    raw_alert = data.get("minimumSavingAlert", None)
    minimumSavingAlert = float(raw_alert) if raw_alert is not None else None

    # Build and return the master config object using data.get() for fallback defaults
    config = CruiseAppConfig(
        displayCruisePrices=data.get("displayCruisePrices", True),
        currencyOverride=data.get("currencyOverride", None),
        minimumSavingAlert=minimumSavingAlert,
        notifyOnError=data.get("notifyOnError", False),
        showPromos=data.get("showPromos", True),
        dateDisplayFormat=data.get("dateDisplayFormat", "%x"),
        logFile=data.get("logFile"),
        apobj=apobj,
        accounts=accounts,
        watch_list=watch_list,
        prospective_cruises=prospective_cruises,
        apprise_urls=apprise_urls,
        reservation_prices=data.get("reservationPricePaid", {}),
        reservation_names=data.get("reservationFriendlyNames", {})
    )

    # Set up the custom logger
    setup_hybrid_logging(config.logFile)

    return config

def login(accountInfo: AccountInfo) -> APIAccess:
#def login(accountInfo):    
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
    session = requests.Session()
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': user_agent_web,
    }
    
    username = accountInfo.username
    password = accountInfo.password
    urlSafePassword  = quote(password, safe='')    
    data = f'grant_type=password&username={username}&password={urlSafePassword}&scope=openid+profile+email+vdsid'

    try:
        response = session.post(f'https://www.{accountInfo.url_brand}.com/auth/oauth2/access_token', headers=headers, data=data)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)
    
    if response.status_code != 200:
        print(f"Login attempt got return code {response.status_code}")
        print(f"{accountInfo.cruiseLine} website might be down, username/password incorrect, or have unsupported symbol in password. Quitting.")
        sys.exit(1)
          
    access_token = response.json().get("access_token")
    
    try:
        list_of_strings = access_token.split(".")
        string1 = list_of_strings[1]
        decoded_bytes = base64.b64decode(string1 + '==')
        auth_info = json.loads(decoded_bytes.decode('utf-8'))
        accountID = auth_info["sub"]
    except(IndexError, ValueError, KeyError) as parse_err:
        print(f"Error parsing authentication token structure: {parse_err}")
        sys.exit(1)

    # Return the APIAccess object
    return APIAccess(
        token = access_token,
        id = accountID,
        session = session
    )


def getDiningAndPrices(accountInfo: AccountInfo, booking: Dict[str, Any]) -> Dict[str, List[Any]]:
#def getDiningAndPrices(accountInfo, booking):
    """
    Extracts explicit reservation pricing details and dining choices from booked summaries.
    
    Queries specific reservation components using transient amendment keys. Implements 
    safety fallbacks to return blank lists if network timeouts or structural processing 
    faults occur, ensuring downstream processes don't break.
    """
    # Safely pull the token and country straight from the booking payload
    amendtoken = booking.get("amendToken")
    country = booking.get("bookingOfficeCountryCode", "USA")

    RSC_URL = f"https://www.{accountInfo.url_brand}.com/usa/en/booked/overview"

    # MAINTENANCE NOTE: The 'RSC: 1' header signals the web server that this is a 
    # Next.js React Server Component call. It forces the endpoint to yield backend raw data 
    # state structures instead of rendering a full human-readable HTML web page.        
    HEADERS = {
        "User-Agent": user_agent_web, 
        "Accept": "text/x-component",
        "RSC": "1",
    }
    
    resp = _execute_api_request(
        account_info=accountInfo,
        method="GET",
        url=RSC_URL,
        params={"token": amendtoken, "country": country},
        headers=HEADERS,
        timeout=30,
        exit_on_fail=False
    )    

    if resp is None:
        return {"diningSelection": [], "prices": [], "pricingAddOns": []}

    text = resp.text
    result = {}

    result["diningSelection"] = _extract_json_array(text, "diningSelection") or []
    result["prices"] = _extract_json_array(text, "prices") or []
    result["pricingAddOns"] = _extract_json_array(text, "pricingAddOns") or []
        
    return result


def getFinalPaymentDate(numberOfNights: int, sailDate: Union[str, date, datetime]) -> date:
    """
    Calculates final payment settlement timelines based on duration rules.
    
    Accepts string timestamps or explicit date objects. Computes strict policy deadlines 
    by calculating offsets from the ship's departure date (75 days for short sailings, 
    90 days for standard voyages, 120 days for extended itineraries).
    """
    # Standardize the input into a solid date object defensively
    if isinstance(sailDate, (datetime, date)):
        # If it's a datetime, extract just the date portion
        dateOfSailing = sailDate.date() if isinstance(sailDate, datetime) else sailDate
    elif isinstance(sailDate, str):
        # Strip out any potential dash or slash delimiters left over by the caller
        clean_date_str = sailDate.replace("-", "").replace("/", "")
        try:
            dateOfSailing = datetime.strptime(clean_date_str, "%Y%m%d").date()
        except ValueError as e:
            raise ValueError(f"Invalid sailDate string format '{sailDate}'. Expected YYYYMMDD or YYYY-MM-DD.") from e
    else:
        raise TypeError("sailDate must be a string, date, or datetime object.")

    # Apply final payment window rules (from Royal Caribbean FAQ)
    if numberOfNights < 5:
        finalPaymentDeadline = 75
    elif numberOfNights < 15:
        finalPaymentDeadline = 90
    else:
        finalPaymentDeadline = 120
    
    return dateOfSailing - timedelta(days=finalPaymentDeadline)
   
    
def getNewBeveragePrice(
    accountInfo: AccountInfo, 
    booking: Dict[str, Any], 
    apobj: Optional[Apprise], 
    ctx: WatchItemContext
) -> None:
#def getNewBeveragePrice(accountInfo, booking, apobj, ctx: WatchItemContext):
    """
    Compares active promotional planner prices against a passenger's purchased cost.
    
    Queries live digital cruise planner catalogs to parse age-bracket targeted rates. 
    If a price reduction crosses configured target thresholds, it triggers terminal alerts, 
    fires Apprise notifications, and generates explicit browser links for rebooking.
    """
    # --- RESERVATIONS SAFETY FILTER ---
    # Explicit check: If this context item targets specific bookings, enforce isolation
    reservationId = booking.get("bookingId")
    if ctx.reservations and reservationId not in ctx.reservations:
        return

    # Unpack voyage identifiers from the booking entity
    ship = booking.get("shipCode", "")
    startDate = booking.get("sailDate", "")
    numberOfNights = int(booking.get("numberOfNights", 0))
    
    currency = config.currencyOverride if config.currencyOverride else ctx.currency
    prefix = ctx.prefix or ""
    product = ctx.product or ""

    # Unpack item context elements
    passengerId = ctx.passengerId
    passengerName = ctx.passengerName
    room = ctx.room
    paidPrice = ctx.paidPrice
    guestAgeString = ctx.guestAgeString
    salesUnit = ctx.salesUnit
    forWatch = ctx.forWatch
    orderCode = ctx.orderCode
    orderDate = ctx.orderDate
    owner = ctx.owner

    params = {
        'reservationId': reservationId,
        'startDate': startDate,
        'currencyIso': currency,
        'passengerId': passengerId,
    }
    
    url = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/commerce-api/catalog/v2/{ship}/categories/{prefix}/products/{product}'
    response = _execute_api_request(accountInfo, "GET", url, params=params, timeout=15)
    payload = response.json().get("payload")
    if payload is None:
        print(f"{prefix} {product} not available for passenger")
        return

    title = payload.get("title")    
    variant = ""
    try:
        variant = payload.get("baseOptions")[0].get("selected").get("variantOptionQualifiers")[0].get("value")
    except Exception:
        pass
    
    if "Bottles" in variant:
        title = f"{title} ({variant})"

    perDayPrice = salesUnit in ['PER_NIGHT', 'PER_DAY']
    newPricePayload = payload.get("startingFromPrice")

    if newPricePayload is None:
        if not forWatch:
            tempString = YELLOW + f"\t{passengerName.ljust(10)} (Cabin {room}) has best price " 
            if perDayPrice:
                tempString += "per night "
            tempString += f"for {title} of: {paidPrice} {currency} (No Longer for Sale)" + RESET
        else:
            tempString = YELLOW + f"\t{title} not available or already booked for {passengerName.ljust(10)}" + RESET
            
        print(tempString)
        return
    
    # Extract age-bracket targeted metrics
    currentPrice = newPricePayload.get(f"{guestAgeString}PromotionalPrice")
    if not currentPrice:
        currentPrice = newPricePayload.get(f"{guestAgeString}ShipboardPrice")

    if not currentPrice:
        currentPrice = 0
    
    # Process Deal Alerts
    if currentPrice < paidPrice:
        saving = round(paidPrice - currentPrice, 2)
        savingForAlert = saving
        savingLabel = f"Saving {saving} {currency}"
        
        if perDayPrice and numberOfNights:
            savingForAlert = round(saving * numberOfNights, 2)
            savingLabel = f"Saving {saving} {currency} per night ({savingForAlert} {currency} total)"
            
        if forWatch:
            text = f"{passengerName}: Book! {title} Price "
            if perDayPrice:
                text += "per night "
            text += f"is lower: {currentPrice} {currency} than {paidPrice} {currency}"
        else:
            text = f"{passengerName}: Rebook! {title} Price " 
            if perDayPrice:
                text += "per night "
            text += f"is lower: {currentPrice} {currency} than {paidPrice} {currency}"
            
        # Reaching into global config for alerts configuration
        if config.minimumSavingAlert is not None:
            text += f" ({savingLabel})"
        
        promoDescription = payload.get("promoDescription")
        if promoDescription:
            promotionTitle = promoDescription.get("displayName")
            text += f'\n\t\tPromotion:{promotionTitle}'

        if forWatch:
            text += f'\n\tBook at https://www.{accountInfo.url_brand}.com/account/cruise-planner/category/{prefix}/product/{product}?bookingId={reservationId}&shipCode={ship}&sailDate={startDate}'
        else:
            text += f'\n\tCancel Order {orderDate} {orderCode} at https://www.{accountInfo.url_brand}.com/account/cruise-planner/order-history?bookingId={reservationId}&shipCode={ship}&sailDate={startDate}'
        
        if not owner:
            text += "\tThis was booked by another in your party. They will have to cancel/rebook for you!"
            
        if config.minimumSavingAlert is not None and savingForAlert < config.minimumSavingAlert:
            text += f" ({savingLabel} < minimumSavingAlert {config.minimumSavingAlert}; no notification sent)"
            print(YELLOW + text + RESET)
        else:
            print(RED + text + RESET)
            if apobj is not None:
                apobj.notify(body=text, title='Cruise Addon Price Alert')
    else:
        if forWatch:
            tempString = GREEN + f"{passengerName.ljust(10)} {title} price "
            if perDayPrice:
                tempString += "per night "
            tempString += f"is higher than watch price: {paidPrice} {currency}" + RESET
        else:
            tempString = GREEN + f"{passengerName.ljust(10)} (Cabin {room}) has best price "
            if perDayPrice:
                tempString += "per night "
            tempString += f"for {title} of: {paidPrice} {currency}" + RESET
        if currentPrice > paidPrice:
            tempString += f" (now {currentPrice} {currency})"
        print(tempString)

def processWatchListForBooking(
    accountInfo: AccountInfo,
    booking: Dict[str, Any],
    watchListItems: List[WatchListItem],
    apobj: Optional[Apprise],
    passenger_info: Dict[str, Any]
) -> None:
#def processWatchListForBooking(accountInfo, booking, watchListItems, apobj, passenger_info):
    """
    Evaluates individual user watchlist targets against active booking records.
    
    Iterates through configured targets, enforces isolation boundaries (such as specific 
    cabin exceptions), pairs the runtime items into a temporary context package, and 
    transfers evaluation duties to the live planner catalog matching engines.
    """
    if not watchListItems:
        return
        
    # Unpack passenger details from the transient loop package
    passengerId = passenger_info.get("passengerId")
    passengerName = passenger_info.get("passengerName", "")
    room = passenger_info.get("room")
    
    for watchItem in watchListItems:
        name = getattr(watchItem, 'name', 'Unknown Item')
        product = getattr(watchItem, 'product', None)
        prefix = getattr(watchItem, 'prefix', None)
        watchPrice = float(getattr(watchItem, 'price', 0))
        enabled = getattr(watchItem, 'enabled', True)  # Default to True if not specified
        guestAgeString = str(getattr(watchItem, 'guestAgeString', "adult")).lower()
        currency = getattr(watchItem, 'currency', "USD")
        
        reservationList = getattr(watchItem, 'reservations', None)
        reservationId = booking.get("bookingId")
        
        if reservationList:
            if reservationId not in reservationList:
                continue
            
        # Skip disabled watchlist items
        if not enabled:
            continue
        
        if not product or not prefix or watchPrice <= 0:
            print(f"\t{YELLOW}Skipping {name} - missing required fields{RESET}")
            continue
            
        # Format: [WATCH] Item Name - Passenger (Room): Message
        watchDisplayName = f"[WATCH] {passengerName} (Cabin {room})"
        
        # Pack up the transient items into a context object
        ctx = WatchItemContext(
            prefix=prefix,
            product=product,
            passengerId=passengerId,
            passengerName=watchDisplayName,
            room=room,
            paidPrice=watchPrice,
            currency=currency,
            guestAgeString=guestAgeString,
            salesUnit=None,
            forWatch=True,
            orderCode="WATCH-LIST",
            orderDate="Watch List",
            owner=True,
            reservations=getattr(watchItem, 'reservations', [])
        )
        
        getNewBeveragePrice(accountInfo, booking, apobj, ctx)

def getNumberOfNights(accountInfo: AccountInfo, loyaltyNumber: str) -> Tuple[int, int]:
#def getNumberOfNights(accountInfo, loyaltyNumber):
    """
    Queries cumulative night metrics and cruise totals for a specified loyalty profile.
    
    Queries corporate historical data points. Runs with 'exit_on_fail=False' inside the 
    request core so historical lookup dropouts won't crash critical root execution pipelines.
    """
    totalNights, totalTrips = -1, -1

    url = f"https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/v1/guestAccounts/loyalty/history/summary"
    
    response = _execute_api_request(
        accountInfo, "GET", url, 
        params={'loyaltyNumber': loyaltyNumber}, 
        timeout=10, 
        exit_on_fail=False
    )
    
    if response and response.status_code == 200:
        payload = response.json().get("payload", {})
        totalNights = payload.get("totalNights", totalNights)
        totalTrips = payload.get("totalTrips", totalTrips)
        
    return totalNights, totalTrips
    
def getProfile(accountInfo: AccountInfo) -> Tuple[Optional[str], Optional[str], int]:
#def getProfile(accountInfo):
    """
    Retrieves personal profile properties to extract valid residency codes and loyalty tiers.
    
    Inspects user contact records to locate primary residency states and tracks concurrent 
    loyalty modules (Crown & Anchor, Club Royale, Captain's Club, and Blue Chip). Returns 
    the active brand tracking index to route downstream web requests correctly.
    """
    url = f"https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/v3/guestAccounts/{accountInfo.access.id}"
    response = _execute_api_request(accountInfo, "GET", url)
    payload = response.json().get("payload")

    state = None
    loyaltyNumber = None
    cAndASharedPoints = 0
    
    address = payload.get("contactInformation", {}).get("address", {})
    if address.get("residencyCountryCode") in ("USA", "CAN"):
        state = address.get("state")
    
    loyalty = payload.get("loyaltyInformation")
    captainsClubId = loyalty.get("captainsClubId")
    cAndANumber = loyalty.get("crownAndAnchorId")
    cAndALevel = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
    cAndAPoints = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints")
    cAndASharedPoints = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints")
   
    # Get and display Royal Caribbean (Crown & Anchor and Club Royale) information
    if cAndANumber and cAndASharedPoints > 0:
        print(f"\tC&A: {cAndANumber} {cAndALevel} - {cAndASharedPoints} Shared Points ({cAndAPoints} Individual Points)")

        totalNights, totalTrips = getNumberOfNights(accountInfo, cAndANumber)
        if totalNights > 0:
            print(f"\tTotal Trips on Royal: {totalTrips} - Total Nights: {totalNights}")
    
    # TO ASK: Can one have a Club Royal id w/o a C&A id?  Move under if?
    clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier","Unknown") 
    if clubRoyaleLoyaltyTier != "Unknown":
        casino_points = loyalty.get("clubRoyaleLoyaltyIndividualPoints",0)
        print(f"\tCasino Royale Tier: {clubRoyaleLoyaltyTier} - {casino_points} Credits")

    # Get and display Celebrity (Captain's Club and Blue Chip) information
    if captainsClubId:
        cc_level = loyalty.get("captainsClubLoyaltyTier")
        cc_individual = loyalty.get("captainsClubLoyaltyIndividualPoints", 0)
        cc_shared = loyalty.get("captainsClubLoyaltyRelationshipPoints", 0)
        print(f"\tCaptain's Club Number: {captainsClubId} {cc_level} TIER ({cc_shared} Shared Points, {cc_individual} Individual Points)")
        
        totalNights, totalTrips = getNumberOfNights(accountInfo, captainsClubId)
        if totalNights > 0:
            print(f"\tTotal Trips on Celebrity: {totalTrips} - Total Nights: {totalNights}")

    # TO ASK: Can one have a Blue Chip id w/o a Captain's Club id?
    celebrityBlueChipLoyaltyTier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
    if celebrityBlueChipLoyaltyTier != "Unknown":
        celebrityBlueChipLoyaltyIndividualPoints = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints",0)
        print(f"\tBlue Chip Tier: {celebrityBlueChipLoyaltyTier} - {celebrityBlueChipLoyaltyIndividualPoints} Points")

    # Return the correct loyality number based on the account being used
    loyaltyNumberToUse = captainsClubId if accountInfo.is_celebrity else cAndANumber

    # Return Royal shared points to determine if eligible for dp340
    return state, loyaltyNumberToUse, cAndASharedPoints

def _parse_stateroom_type(room_type_code: Optional[str]) -> str:
#def _parse_stateroom_type(room_type_code):
    """
    Translates raw single-character stateroom types into explicit checkout parameters.
    
    Maps internal character letters (such as 'I', 'O', 'B') to explicit structural 
    keywords expected by corporate inventory checkout paths (e.g., 'INTERIOR', 'OUTSIDE', 'BALCONY').
    """
    mapping = {
        "I": "INTERIOR",
        "O": "OUTSIDE",
        "B": "BALCONY",
        "D": "DELUXE",
        "C": "CONCIERGE"
    }
    return mapping.get(room_type_code, "NONE")

def _calculate_passenger_metrics(
    guests: List[Dict[str, Any]], 
    sail_date: str, 
    booking: Dict[str, Any], 
    brand_code: str, 
    display_prices: bool
) -> Dict[str, Any]:
#def _calculate_passenger_metrics(guests, sail_date, booking, brand_code, display_prices):
    """
    Parses structural guest files to calculate age milestones, check-in windows, and demographic flags.
    
    Evaluates age metrics on departure day to isolate senior statuses, tracks child/adult ratios, 
    extracts boarding windows, and applies legacy GTY profile patches to fix missing API elements.
    """
    passenger_names = []
    checkin_strings = []
    num_adults = 0
    num_children = 0
    have_a_senior = False

    # Track distinct room tracking variables to safely prevent outer loop corruption
    stateroom_type = booking.get("stateroomType")
    stateroom_subtype = booking.get("stateroomSubtype")

    for guest in guests:
        stateroom_category_code = guest.get("stateroomCategoryCode")
        
        # Apply legacy GTY room structure workarounds
        if stateroom_category_code is None and stateroom_subtype is None:
            if display_prices:
                print(YELLOW + "Data is missing from API. Code is taking a guess to fixing" + RESET)
                print(YELLOW + "Add category override in config.yaml if wrong category" + RESET)
            
            # TO ASK: Is it intentional that these stateroom_* variables apply to all guests?
            if stateroom_type == "B" and brand_code == "C":
                stateroom_category_code = "XC"
                stateroom_subtype = "XC"
            elif stateroom_type == "I" and brand_code == "R":
                stateroom_category_code = "ZI"
                stateroom_subtype = "ZI"

        # Names & Demographic verification
        first_name = guest.get("firstName", "").capitalize()
        passenger_names.append(first_name)
        
        birth_date = guest.get("birthdate")
        if not have_a_senior:
            have_a_senior = aboveAgeOnSailDate(birth_date, sail_date, 55)
            
        if aboveAgeOnSailDate(birth_date, sail_date, 12):
            num_adults += 1
        else:
            num_children += 1

        # Calculate Check-in Windows
        if guest.get("onlineCheckinStatus") == "COMPLETED" and guest.get("arrivalTime"):
            time_str = guest.get("arrivalTime")
            boarding_hour = time_str[9:11]
            boarding_min = time_str[11:13]
            checkin_strings.append(f"{first_name} Boarding Time {boarding_hour}:{boarding_min}")

    return {
        "passengerNames": ", ".join(passenger_names),
        "checkinString": ", ".join(checkin_strings),
        "numAdults": num_adults,
        "numChildren": num_children,
        "haveASenior": have_a_senior,
        "categoryCode": stateroom_category_code,
        "subType": stateroom_subtype
    }

def _build_checkout_url(
    booking: Dict[str, Any], 
    metrics: Dict[str, Any], 
    accountInfo: AccountInfo, 
    discounts: CruiseURLParams
) -> str:
#def _build_checkout_url(booking, metrics, accountInfo, discounts):
    """
    Generates a live corporate web URL mirroring the parameters used during price tracking.
    
    Assembles passenger counts, ship short codes, voyage targets, regional residency codes, 
    and senior or military indicators into url parameters. Provides users with a direct 
    browser link to confirm or purchase the rate.
    """
    cruiseLineName = accountInfo.cruiseLine
    brandCode = "R" if accountInfo.is_royal else "C"
    
    # Map the boolean flags from the discounts dataclass to web-URL strings ('y'/'n')
    # and safely apply the 'senior' override locally
    is_senior = "y" if (discounts.senior or metrics['haveASenior']) else "n"
    is_military = "y" if discounts.military else "n"
    is_police = "y" if discounts.police else "n"

    sailDate = booking.get("sailDate")
    urlSailDate = f"{sailDate[0:4]}-{sailDate[4:6]}-{sailDate[6:8]}"
    stateroomNumber = booking.get("stateroomNumber")
    
    # Build the dictionary of parameters that URLs for GTY and non-GTY share completely
    params = {
        'packageCode': booking.get("packageCode"),
        'sailDate': urlSailDate,
        'country': booking.get("bookingOfficeCountryCode"),
        'selectedCurrencyCode': booking.get("bookingCurrency"),
        'shipCode': booking.get("shipCode"),
        'roomIndex': '0',
        'r0a': metrics['numAdults'],
        'r0c': metrics['numChildren'],
        'r0d': _parse_stateroom_type(booking.get("stateroomType")),
        'r0e': metrics['subType'],
        'r0f': metrics['categoryCode'],
        'r0b': 'n',
        'r0r': is_police,
        'r0s': 'n',
        'r0q': is_military,
        'r0t': is_senior,
        'r0D': 'y'
    }
    
    # Handle optional properties from the dataclass
    if discounts.dp340 and brandCode == "R" and metrics['numAdults'] == 1 and metrics['numChildren'] == 0:
        params['r0i'] = 'DP340'
        
    if discounts.loyaltyNumber is not None:
        params['r0l'] = discounts.loyaltyNumber
        
    if discounts.state is not None:
        params['r0k'] = discounts.state

    # Define the base URL and add the GTY-specific parameters as needed
    if stateroomNumber == "GTY":
        baseUrl = f"https://www.{accountInfo.url_brand}.com/checkout/add-ons"
        params['r0g'] = 'BESTRATE'
        params['r0h'] = 'n'
        params['r0C'] = 'y'
    else:
        baseUrl = f"https://www.{accountInfo.url_brand}.com/room-selection/room-location"

    # Seamlessly combine the base URL and the safely encoded string
    return f"{baseUrl}?{urlencode(params)}"

def getVoyages(accountInfo: AccountInfo, discounts: CruiseURLParams, shipDictionary: ShipRegistry) -> None:
#def getVoyages(accountInfo, discounts, shipDictionary):
    """
    Extracts all current, valid upcoming cruise bookings linked to an active account profile.
    
    Submits account tokens to retrieve profile booking manifests. For each identified 
    reservation, it parses ship names, evaluates deadlines, loops through cabin passengers, 
    tracks addon planner purchases, and coordinates live cabin pricing checks.
    """
    # Gather the variables we need from the data classes
    access_token = accountInfo.access.token
    accountId = accountInfo.access.id
    session = accountInfo.access.session

    apobj = config.apobj
    watchListItems = config.watch_list
    displayCruisePrices = config.displayCruisePrices
    reservationPricePaid = config.reservation_prices
    reservationFriendlyNames = config.reservation_names
    showPromos = config.showPromos
    dateDisplayFormat = config.dateDisplayFormat

    loyaltyNumber = discounts.loyaltyNumber
    state = discounts.state

    brandCode = "R" if accountInfo.is_royal else "C"
    params = {'brand': brandCode, 'includeCheckin': 'false'}
    url = f'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/{accountId}'
    response = _execute_api_request(accountInfo, "GET", url, params=params)
    bookings = response.json().get("payload", {}).get("profileBookings", [])

    for booking in bookings:        
        reservationId = booking.get("bookingId")
        passengerId = booking.get("passengerId")
        sailDate = booking.get("sailDate")
        numberOfNights = booking.get("numberOfNights")
        shipCode = booking.get("shipCode")
        guests = booking.get("passengersInStateroom", [])
        packageCode = booking.get("packageCode")
        bookingCurrency = booking.get("bookingCurrency")
        bookingOfficeCountryCode = booking.get("bookingOfficeCountryCode")
        stateroomNumber = booking.get("stateroomNumber")
        amendToken = booking.get("amendToken")
        
        # Translate room letter code
        stateroomTypeName = _parse_stateroom_type(booking.get("stateroomType"))
        
        # Unpack cabin occupants & boarding windows safely
        metrics = _calculate_passenger_metrics(guests, sailDate, booking, brandCode, displayCruisePrices)
        
        # Display Reservation Information Header
        reservationDisplay = f"Reservation #{reservationId}"
        if str(reservationId) in reservationFriendlyNames:
            reservationDisplay += f" ({reservationFriendlyNames.get(str(reservationId))})"
        print(f"\n{reservationDisplay}")
        
        print(f"{config.format_date(sailDate)} {shipDictionary.get_ship(shipCode)} Room {stateroomNumber} (In this cabin: {metrics['passengerNames']})")
        
        # Print Boarding Info or call fallback check-in handler
        if metrics['checkinString']:
            print(metrics['checkinString'])
        else:
            GetCheckinInfo(accountInfo, reservationId, passengerId, shipCode, sailDate, apobj)
        
        # Process Dining Setup
        result = getDiningAndPrices(accountInfo, booking)
        diningSelection = result.get("diningSelection", [])
        for selection in diningSelection:
            if selection.get("sittingTime", "") == "MY TIME":
                print("Dining: My Time Open Sitting")
            else:
                diningString = f"Dining: {selection.get('sittingType', '')} {selection.get('sittingTime', '')}"
                tableSize = selection.get("tableSize", "")
                if tableSize and tableSize != "00":
                    diningString += f" Table Size: {tableSize}"
                print(diningString)
        
        # Unpack Ledger Pricing Matrix
        paymentString = ""
        gross_totals = None
        prepaidGratsFlag = False
        insuranceFlag = False
        allIncludedFlag = False
        cruisePaidPriceFromAPI = result.get("prices", [])

        for curPrice in cruisePaidPriceFromAPI:
            priceTypeCode = curPrice.get("priceTypeCode", "")
            amount = curPrice.get("amount")
            if not amount:
                continue
                
            if priceTypeCode == "GROSS_TOTALS":
                gross_totals = amount
            elif priceTypeCode == "GRATUITIES":
                prepaidGratsFlag = True
                paymentString += f" Including: {amount} Gratuities"
            elif priceTypeCode == "TRIP_INSURANCE":
                insuranceFlag = True
                paymentString += f" Including: {amount} Insurance"
            elif "ALL_INC" in priceTypeCode or "INCLUDED" in priceTypeCode:
                allIncludedFlag = True
                paymentString += f" Including: {amount} All Included Drinks/WiFi"    
            elif priceTypeCode == "BALANCE_DUE":
                paymentString += f" You Still Owe: {amount}"    
        
        paidPriceStruct = {}
        if gross_totals is not None:
            paidPriceStruct['reservation'] = reservationId
            paidPriceStruct['paidPrice'] = gross_totals
            paidPriceStruct['gratuities'] = prepaidGratsFlag
            paidPriceStruct['tripInsurance'] = insuranceFlag
            paidPriceStruct['allInUpgrade'] = allIncludedFlag
            print(f"Cruise Fare - Total {gross_totals}{paymentString}")
        
        finalPaymentDate = getFinalPaymentDate(numberOfNights, sailDate)
        finalPaymentDateDisplay = finalPaymentDate.strftime(dateDisplayFormat)
        
        if booking.get("balanceDue") is True:
            print(YELLOW + f"Remaining Cruise Payment Balance is {booking.get('balanceDueAmount')} due {finalPaymentDateDisplay}" + RESET)
            
        GetOBC(accountInfo, booking)
        
        if showPromos:
            getAllPromotions(accountInfo, booking)
        
        # Current Web Market Pricing Block
        if displayCruisePrices:
            # Build the complex Checkout/Room Selection URL
            cruisePriceURL = _build_checkout_url(booking, metrics, accountInfo, discounts)
            
            # Map legacy manual pricing text overrides from configuration yaml
            if isinstance(reservationPricePaid, dict) and reservationPricePaid:
                if str(reservationId) in reservationPricePaid:
                    paidPrice = reservationPricePaid.get(str(reservationId))
                    if paidPrice is not None:
                        paidPriceStruct[paidPrice] = float(paidPrice)
            elif isinstance(reservationPricePaid, list):        
                for reservation in reservationPricePaid:
                    if int(reservationId) == int(reservation.get("reservation")):
                        for item in reservation:
                            paidPriceStruct[item] = reservation.get(item)
                
            if booking.get("stateroomType") != "NONE":
                GetCheckinInfo(accountInfo, reservationId, passengerId, shipCode, sailDate, apobj)
            else:
                print(YELLOW + "Cannot Check Cruise Price - Use Manual URL Method" + RESET)
                
        getOrders(accountInfo, booking, metrics)
        print(" ")
        
        # Process watchlists on a per-occupant layout instead of per-booking line
        if watchListItems:
            for guest in guests:
                passenger_info = {
                   "passengerId": guest.get("passengerId"),
                   "passengerName": guest.get("firstName", "").capitalize(),
                   "room": guest.get("stateroomNumber") or stateroomNumber
                }
                
                processWatchListForBooking(accountInfo, booking, watchListItems, apobj, passenger_info)

            print(" ")

def getOrders(accountInfo: AccountInfo, booking: Dict[str, Any], metrics: Dict[str, Any]) -> None:
#def getOrders(accountInfo, booking, metrics):
    """
    Retrieves the digital order history or itinerary manifest for an active booking.
    
    Queries corporate transactional endpoints to pull details on pre-purchased items,
    shore excursions, or specialty configurations. Essential for auditing what 
    add-ons have already been tied to a passenger's profile.
    """
    # Extract voyage characteristics from booking payload
    ship = booking.get("shipCode", "")
    startDate = booking.get("sailDate", "")
    passengerId = booking.get("passengerId")
    reservationId = booking.get("bookingId")
    numberOfNights = int(booking.get("numberOfNights", 0))
    
    # Handle global currency overrides cleanly
    if globals().get('currencyOverride', "") != "":
        currency = currencyOverride
    else:
        currency = booking.get("bookingCurrency", "USD")
    
    params = {
        'passengerId': passengerId,
        'reservationId': reservationId,
        'sailingId': f"{ship}{startDate}",
        'currencyIso': currency,
        'includeMedia': 'false',
    }
    
    url_history = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/commerce-api/calendar/v1/{ship}/orderHistory'
    response = _execute_api_request(accountInfo, "GET", url_history, params=params, timeout=15)
    payload = response.json().get("payload")
    if not payload:
        return

    # Merge my orders and orders booked on my behalf
    all_orders = (payload.get("myOrders") or []) + (payload.get("ordersOthersHaveBookedForMe") or [])
    
    for order in all_orders:
        orderCode = order.get("orderCode")

        # Reference global date format cleanly
        date_obj = datetime.strptime(order.get("orderDate"), "%Y-%m-%d")
        orderDate = date_obj.strftime(config.dateDisplayFormat)
        owner = order.get("owner")
            
        # Only process valid paid orders
        if order.get("orderTotals", {}).get("total", 0) > 0:             

            url_detail = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/commerce-api/calendar/v1/{ship}/orderHistory/{orderCode}'            
            response = _execute_api_request(accountInfo, "GET", url_detail, params=params, timeout=15)
            order_data = response.json()
            if not order_data or not order_data.get("payload"):
                continue
                
            for orderDetail in order_data.get("payload", {}).get("orderHistoryDetailItems", []):
                quantity = orderDetail.get("priceDetails", {}).get("quantity", 0)
                order_title = orderDetail.get("productSummary", {}).get("title")
                
                # Pre-6 Feb 2026 API structure safety hook
                try:
                    product = orderDetail.get("productSummary", {}).get("baseOptions")[0].get("selected", {}).get("code")
                except Exception:
                    product = orderDetail.get("productSummary", {}).get("defaultVariantId")
                    
                prefix = orderDetail.get("productSummary", {}).get("productTypeCategory", {}).get("id", "")
                salesUnit = orderDetail.get("productSummary", {}).get("salesUnit")
                guests = orderDetail.get("guests", [])
                
                for guest in guests:
                    if guest.get("orderStatus") == "CANCELLED":
                        continue
                        
                    paidPrice = guest.get("priceDetails", {}).get("subtotal", 0)
                    paidQuantity = guest.get("priceDetails", {}).get("quantity", 0)
                    
                    if paidPrice == 0:
                        continue
                        
                    guestPassengerId = guest.get("id")
                    firstName = guest.get("firstName", "").capitalize()
                    guestReservationId = guest.get("reservationId")
                    guestAgeString = guest.get("guestType", "").lower()
                    
                    # Deduplication filtering
                    newKey = f"{guestPassengerId}{guestReservationId}{prefix}{product}"
                    if newKey in accountInfo.found_items:
                        continue
                    accountInfo.found_items.append(newKey)
                    
                    # Compute specialized per-day or per-night calculations
                    if salesUnit in ['PER_NIGHT', 'PER_DAY'] and numberOfNights > 0:
                        paidPrice = round(paidPrice / numberOfNights, 2)
                
                    if paidQuantity > 0:
                        paidPrice = round(paidPrice / paidQuantity, 2)
                        
                    currency = guest.get("priceDetails", {}).get("currency")
                    room = guest.get("stateroomNumber") 
                    
                    # Pack up the transient items into a context object
                    ctx = WatchItemContext(
                        prefix=prefix,
                        product=product,
                        passengerId=guestPassengerId,
                        passengerName=firstName,
                        room=room,
                        paidPrice=paidPrice,
                        currency=currency,
                        guestAgeString=guestAgeString,
                        salesUnit=salesUnit,
                        forWatch=False,
                        orderCode=orderCode,
                        orderDate=orderDate,
                        owner=owner,
                        reservations=getattr(watchItem, 'reservations', [])
                    )
        
                    getNewBeveragePrice(accountInfo, booking, apobj, ctx)

def parseProvidedURL(url: str) -> CruiseURLParams:
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
    cabinClassType_list = params.get("cabinClassType")
    
    if cabinClassType_list:
        cabin_string = cabinClassType_list[0]
    elif r0d_list:
        cabin_string = r0d_list[0]
    else:
        cabin_string = ""

    return CruiseURLParams(
        isRoyal="royal" in domain,
        sailDate=params.get("sailDate", [None])[0],
        currencyCode=params.get("selectedCurrencyCode", ["USD"])[0],
        bookingOfficeCountryCode=params.get("country", ["USA"])[0],
        shipCode=params.get("shipCode", [None])[0],
        cabinClassString=cabin_string,
        stateroomTypeName=r0d_list[0] if r0d_list else None,
        stateroomSubtype=params.get("r0e", [None])[0],
        stateroomCategoryCode=params.get("r0f", [None])[0],
        packageCode=params.get("packageCode", [None])[0],
        numberOfAdults=params.get("r0a", ["2"])[0],
        numberOfChildren=params.get("r0c", ["0"])[0],
        loyaltyNumber=params.get("r0l", [None])[0],
        username=params.get("r0H", [None])[0],             # Now safely mapped!
        state=params.get("r0k", [None])[0],
        allIncluded=params.get("r0o", ["XXX"])[0] != "XXX",  # Now safely mapped!
        refundable=params.get("r0u", ["XXX"])[0] != "XXX",   # Now safely mapped!
        travelInsurance=params.get("r0n", ["n"])[0] != "n", # Now safely mapped!
        prepaidGrats=params.get("r0m", ["n"])[0] != "n",    # Now safely mapped!
        couponCode=params.get("r0i", [None])[0],
        senior=(r0t_val == "y"),
        military=(r0q_val == "y"),
        police=(r0r_val == "y"),
        fire=(r0s_val == "y")
    )

def getCruisePrice(accountInfo: AccountInfo, booking: Dict[str, Any], shipDictionary: ShipDictionary, automaticURL: bool = True) -> None:
#def getCruisePrice(accountInfo, booking, shipDictionary, automaticURL=True):
    """
    Performs dynamic live web-pricing evaluations for a specific stateroom or prospective cruise.
    
    Simulates consumer search requests to locate real-time pricing and tax figures. 
    Compares current market pricing options against the original booked price, logs 
    pricing changes to the console, and triggers deal notifications for verified drops.
    """
    # Pull properties from the foundational domain entities
    session = accountInfo.access.session
    paidPriceStruct = booking.get("paidPriceStruct")  # Dict containing target metrics
    finalPaymentDate = booking.get("finalPaymentDate")  # Optional datetime field
    apobj = config.apobj
    
    urlParams = parseProvidedURL(booking.get("url", ""))

    # Absorb any YAML overrides
    urlParams.apply_overrides(paidPriceStruct)
    
    # Capture target price bounds if they exist
    paidPrice = paidPriceStruct.get("paidPrice") if paidPriceStruct else None
    roomNumber = None    

    # Primary API pricing check pass
    results = getRoomPriceViaAPI(urlParams, roomNumber)
    
    # Defensive Fallback: If a coupon code explicitly bricks availability, retry without it
    roomAvailable = results.get("roomAvailable")
    if not roomAvailable and urlParams.couponCode is not None:
        print(f"Coupon Code {urlParams.couponCode} may have failed, trying without using it")
        urlParams.couponCode = None
        results = getRoomPriceViaAPI(urlParams, roomNumber)
        roomAvailable = results.get("roomAvailable")
        
    numberOfNights = results.get("sailingNights")
    
    # Reach into the global ship mapper object natively
    shipName = shipDictionary.get_ship(urlParams.shipCode)
    sailDateDisplay = config.format_date(urlParams.sailDate) 
    preString = f"{sailDateDisplay} {shipName} {urlParams.cabinClassString} {urlParams.stateroomCategoryCode}"
    
    # Build active discount labels
    usedDiscounts = ""
    if urlParams.loyaltyNumber is not None: usedDiscounts += "Loyalty, "
    if urlParams.state is not None:         usedDiscounts += "Residency, " 
    if urlParams.senior == "y":             usedDiscounts += "Senior, " 
    if urlParams.police == "y":             usedDiscounts += "Police, " 
    if urlParams.military == "y":           usedDiscounts += "Military, " 
    if urlParams.couponCode is not None:    usedDiscounts += f"Coupon {urlParams.couponCode}, " 
    
    if usedDiscounts != "":
        preString = f"{preString} ({usedDiscounts[:-2]} Discount)"
    
    addons = ""
    refundNotFound = False
    
    if roomAvailable:
        baseFareString = "allIncludedFare" if urlParams.allIncluded else "baseFare"
        refundFareString = "allIncludedRefundableFare" if urlParams.allIncluded else "baseRefundableFare"
            
        fareStruct = results.get(baseFareString)
        if fareStruct is None:
            print(f"{RED}All Included Fare is Not Available - Reverting to Non-refundable fare{RESET}")
            fareStruct = results.get("baseFare")
            
        if fareStruct is not None:
            price = fareStruct.get("fare", 0.0)
            grats = fareStruct.get("gratuities", 0.0)
            ins = fareStruct.get("insurance", 0.0)
            obc = fareStruct.get("obc", "0.0")
        
        basePrice = price
        baseGrats = grats
        baseIns = ins
        
        desireRefundPrice = False
        if urlParams.refundable:
            desireRefundPrice = True
            addons += "Refundable Deposit, "
            fareStruct = results.get(refundFareString)
            if fareStruct is not None:
                price = fareStruct.get("fare", 0.0)
                grats = fareStruct.get("gratuities", 0.0)
                ins = fareStruct.get("insurance", 0.0)
                obc = fareStruct.get("obc", "0.0")
            else:
                refundNotFound = True
           
        if urlParams.travelInsurance:
            addons += "Travel Protection, "
            price += ins
            basePrice += baseIns
        if urlParams.prepaidGrats:
            addons += "Prepaid grats, "
            price += grats
            basePrice += baseGrats
        if urlParams.allIncluded:
            addons += "All Included, "
            
        if addons != "":
            preString = f"{preString} ({addons[:-2]})"  
        
    # Calculate final payment window limits dynamically if missing
    if finalPaymentDate is None:
        finalPaymentDate = getFinalPaymentDate(numberOfNights, urlParams.sailDate.replace('-', ''))
        
    finalPaymentDateDisplay = finalPaymentDate.strftime(config.dateDisplayFormat)
    pastFinalPaymentDate = date.today() > finalPaymentDate
    
    # Path A: Room is completely unlisted or sold out
    if not roomAvailable:
        textString = f"{preString} Not For Sale"
        if automaticURL and pastFinalPaymentDate:
            textString += f". Past Final Payment Date of {finalPaymentDateDisplay}"
            
        print(YELLOW + textString + RESET)
        
        if not automaticURL and apobj is not None:
            apobj.notify(body=textString, title='Cruise Room Not Available')
        
        if urlParams.packageCode and not automaticURL:
            print(f"\tAvailable Rooms (non-discounted price) for {urlParams.numberOfAdults} Adult and {urlParams.numberOfChildren} Child on This Sailing Are:")
            for availableRoom in results.get("availableRooms", []):
                roomsLeft = availableRoom.get('roomsLeft')
                if roomsLeft is not None and roomsLeft > 0:
                    print(f"\t{availableRoom.get('name')} {availableRoom.get('price')} - Rooms Left {roomsLeft}")
        return
        
    # Path B: Standard Pricing Evaluation
    if paidPrice is None:
        print(GREEN + f"{preString}: Current Price {price} {urlParams.currencyCode}" + RESET)
        return
    
    obcValue = float(obc or 0.0)
    obcString = str(obc)
    
    if price < paidPrice: 
        saving = round(paidPrice - price, 2)
        
        # Sub-branch 1: Actionable booked drop before final lock dates
        if automaticURL and not pastFinalPaymentDate:
            textString = f"Rebook! {preString} New price of {price} {urlParams.currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} USD OBC"
            textString += f" is lower than {paidPrice}"
            
            if config.minimumSavingAlert is not None and saving < config.minimumSavingAlert:
                textString += f" (Saving {saving} < minimumSavingAlert {config.minimumSavingAlert}; no notification sent)"
                print(YELLOW + textString + RESET)
            else:
                print(RED + textString + RESET)
                if apobj is not None:
                    apobj.notify(body=textString, title='Cruise Price Alert')

        # Sub-branch 2: Booked drop but locked behind final lock dates
        if automaticURL and pastFinalPaymentDate:
            textString = f"Past Final Payment Date of {finalPaymentDateDisplay} : {preString} New price of {price} {urlParams.currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} USD OBC"
            textString += f" is lower than {paidPrice}"
            print(YELLOW + textString + RESET)

        # Sub-branch 3: Speculative prospective watchlist match
        if not automaticURL:
            textString = f"Consider Booking! {preString} New price of {price} {urlParams.currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} OBC"
            textString += f" is lower than watchlist price of {paidPrice}"
            
            if config.minimumSavingAlert is not None and saving < config.minimumSavingAlert:
                textString += f" (Saving {saving} < minimumSavingAlert {config.minimumSavingAlert}; no notification sent)"
                print(YELLOW + textString + RESET)
            else:
                print(RED + textString + RESET)
                if apobj is not None:
                    apobj.notify(body=textString, title='Cruise Price Alert')
    else:
        # Current catalog price is equal to or higher than target price thresholds
        tempString = GREEN + f"{preString}: You have best price of {paidPrice} {urlParams.currencyCode}" + RESET
        if price > paidPrice:
            tempString += GREEN + f" (now {price} {urlParams.currencyCode}"
            if obcValue > 0:
                tempString += f" not including {obcString} OBC"
            tempString += ")" + RESET   
        
        if desireRefundPrice and paidPrice > basePrice:
            tempString += f"{YELLOW} Non-Refundable price {basePrice} {urlParams.currencyCode} is lower than you paid{RESET}"
        elif desireRefundPrice:
            tempString += f" Non-refundable price is {basePrice} {urlParams.currencyCode}"
            
        print(tempString)

def getShipDictionaryWeb(registry: ShipRegistry) -> None:
#def getShipDictionaryWeb(registry):
    """
    Queries corporate servers to construct a dictionary tracking active fleet ship profiles.
    
    Populates an in-memory ship lookup container mapping corporate short codes 
    (e.g., 'AL', 'SY') to user-friendly vessel names, preventing structural lookups 
    from displaying blank codes during reporting.
    """
    headers = {
        'User-Agent': user_agent_web,
        'Accept': 'application/json',
        'appkey': appkey_web,
    }

    params = {
        'sort': 'name',
    }

    try:
        response = requests.get('https://aws-prd.api.rccl.com/en/royal/web/v2/ships', params=params, headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)
    ships = response.json().get("payload").get("ships")
    registry.add_from_payload(ships)
 
def getAllPromotions(accountInfo: AccountInfo, booking: Dict[str, Any]) -> None:
#def getAllPromotions(accountInfo, booking):
    """
    Queries corporate promotion catalog directories for applicable public or loyalty fare discount codes.
    
    Gathers combinations of eligible code matrices (such as 'BESTRATE') active for a specific 
    vessel and departure timeline. Provides a foundational dictionary array used by the pricing 
    engines to determine valid discount paths.
    """
    def fetchPromos(page: str) -> List[Dict[str, Any]]:
#    def fetchPromos(page):
        """
        Submits specific voyage parameters to corporate servers to harvest eligible discount code strings.

        Acts as the targeted fetching layer for promotion matrices. Isolates public rate adjustments 
        and client loyalty discounts available for a precise ship, cabin code, and departure window, 
        returning a clean index array used by downstream pricing validation engines.
        """
        # Accesses the pooled session inside accountInfo
        try:
            resp = accountInfo.access.session.get(
                base_url, 
                params={'sailingId': sailingId, 'page': page, 'currencyIso': currency}, 
                headers=headers,
                timeout=15
            )
            return resp.json().get("payload") or [] if resp.status_code == 200 else []
        except Exception:
            return []

    headers = {
        'Access-Token': accountInfo.access.token,
        'AppKey': appkey_web,  # Global constant
        'vds-id': accountInfo.access.id,
    }

    base_url = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/commerce-api/catalog/v2/promotions/list'
    
    # Safely extract routing identifiers from the booking dictionary
    ship = booking.get("shipCode", "")
    startDate = booking.get("sailDate", "")
    currency = booking.get("bookingCurrency")
    
    sailingId = f"{ship}{startDate}"

    all_promos = fetchPromos('homepage')
    if not all_promos:
        return

    banner_by_id = {}
    for promo in fetchPromos('pdp'):
        for template in promo.get("templates", []):
            if template.get("type") == "SITEWIDE_BANNER":
                banner_by_id[promo.get("id")] = template
                break

    seenIds = set()
    for promo in all_promos:
        promoId = promo.get("id")
        if promoId in seenIds:
            continue
        seenIds.add(promoId)

        promoStart = promo.get("startDate", "")[:10]
        promoEnd = promo.get("endDate", "")[:10]
        dateRange = f"(Valid {promoStart} to {promoEnd})"

        banner = banner_by_id.get(promoId)
        if banner:
            promoLine = f"[PROMO] {banner.get('heading3', '')} {banner.get('heading4', '')} - {banner.get('heading1', '')} {dateRange}"
        else:
            template = next((t for t in promo.get("templates", []) if t.get("type") == "HOME_HERO_LOCKUP"), None)
            if not template:
                continue

            description = ""
            lockupMedia = template.get("lockupMedia")
            if lockupMedia and lockupMedia.get("source"):
                filename = lockupMedia["source"].get("path", "").split("/")[-1]
                match = re.search(r'lockup-(.+?)_[A-Z]{2}\.', filename)
                if match:
                    description = match.group(1).replace("-", " ").upper()

            categoryCode = template.get("categoryCode", "")
            promoLine = f"[PROMO] {description or promoId}"
            if categoryCode:
                promoLine += f" ({categoryCode})"
            promoLine += f" {dateRange}"

        print(YELLOW + promoLine + RESET)

def GetCruisePriceFromAPI(currency: str, packageCode: str, sailDate: str, bookingType: str, numAdults: Union[int, str], numChildren: Union[int, str]) -> None:
#def GetCruisePriceFromAPI(currency, packageCode, sailDate, bookingType, numAdults, numChildren):
    """
    High-level orchestration manager that pulls live retail cabin pricing directly via the API.
    
    Acts as the main bridge between raw parsed parameters and structural request assemblies. 
    Pre-formats inventory query arrays and submits them through the target pricing API 
    endpoint to calculate current base fares, port taxes, and total room options.
    """
    cookies = {
        'currency': currency,
    }
    
    headers = {
        'User-Agent': user_agent_web,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'currency': currency,
    }
    
    filterString = f"id:{packageCode}|adults:{numAdults}|children:{numChildren}|startDate:{sailDate}~{sailDate}"
    
    json_data = {
        'operationName': 'cruiseSearch_Cruises',
        'variables': {
            'filters': filterString,
            'enableNewCasinoExperience': False,
            'sort': {
                'by': 'RECOMMENDED',
            },
            'pagination': {
                'count': 100,
                'skip': 0,
            },
        },
        'query': 'query cruiseSearch_Cruises($filters: String) {cruiseSearch(filters: $filters) {results {cruises {id sailings {sailDate stateroomClassPricing {price {value currency { code }} stateroomClass {id name content { code } }}}}}}}',
    }

    resp = requests.post('https://www.royalcaribbean.com/cruises/graph', cookies=cookies, headers=headers, json=json_data)
    cruises = resp.json()["data"]["cruiseSearch"]["results"]["cruises"]
    if cruises:
        sailings = cruises[0]["sailings"]
    else:
        print("         Sailing is sold out")
        return
   
    for sailing in sailings:
        if sailing["sailDate"].replace("-", "") != sailDate and sailing["sailDate"] != sailDate:
            continue
            
        prices = sailing["stateroomClassPricing"]
        for price in prices:
            cabinCode = price["stateroomClass"]["content"]["code"] 
 
            if cabinCode == bookingType:
                postString = " (your current room class) "
            else:
                postString = ""
                
            cabinType = price["stateroomClass"]["name"]
            
            if price["price"] is None:
                print(f"\t\t{cabinType} sold out")
            else:    
                numPassengers = int(numAdults) + int(numChildren)
                totalCabinCost = float(price["price"]["value"]) * numPassengers
                print(f"\t\t{totalCabinCost} {currency}: Cheapest {cabinType} Price for {numPassengers}" + postString)

def GetOBC(accountInfo: AccountInfo, booking: Dict[str, Any]) -> None:
#def GetOBC(accountInfo, booking):
    """
    Extracts Onboard Credit (OBC) balances and promotional credit allocations for a booking.
    
    Inspects transaction summaries and pricing breakdowns within an active reservation. 
    Aggregates split credit lines into a single friendly number, letting users see exactly 
    how much total spending money is attached to their account.
    """
    # Pull authenticated identity elements from accountInfo
    access_token = accountInfo.access.token
    accountId = accountInfo.access.id
    session = accountInfo.access.session

    # Safely pull transaction metrics directly from the booking dictionary
    reservationId = booking.get("bookingId")
    shipCode = booking.get("shipCode", "")
    sailDate = booking.get("sailDate", "")
    
    params = {
        'passengerId': booking.get("passengerId"),
        'sailingId': f"{shipCode}{sailDate}",
        'currencyIso': booking.get("bookingCurrency"),
    }

    url = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/commerce-api/cart/v1/obc/reservations/{reservationId}'
    response = _execute_api_request(accountInfo, "GET", url, params=params, timeout=15)
    payload = response.json().get("payload")
    if not payload:
        return
    
    amount = payload.get("amount")
    cur = payload.get("currencyIso")
    
    if amount and amount > 0:
        print(f"\tOnboard Credit of {amount} {cur}")

def GetCheckinInfo(accountInfo: AccountInfo, reservationId: str, passengerId: str, shipCode: str, sailDate: str, apobj: Optional[Apprise]) -> None:
#def GetCheckinInfo(accountInfo, reservationId, passengerId, shipCode, sailDate, apobj):
    """
    Retrieves mandatory pre-cruise check-in statuses and digital health manifest timelines.
    
    Queries check-in tracking endpoints to verify if passengers have completed passport data entry,
    selected their physical arrival times, or if their profile documents are still pending review.
    """
    url = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/v3/ships/voyages/{shipCode}{sailDate}/enriched'
    response = _execute_api_request(accountInfo, "GET", url, timeout=10)
    
    payload = response.json().get("payload")
    if not payload:
        return

    sailing_info = payload.get("sailingInfo")
    if not sailing_info:
        return
        
    isCheckinAvailable = sailing_info[0].get("isCheckinAvailable")
    checkWindowOpenStartDateTime = sailing_info[0].get("checkWindowOpenStartDateTime")
    
    if isCheckinAvailable:
        print(f"{RED}Check In Available and Not Completed{RESET}")
    else:
        try:
            dt = datetime.fromisoformat(checkWindowOpenStartDateTime)
            # Reaching out to the global 'config' object directly
            local_dt = dt.astimezone().strftime(config.dateDisplayFormat + " %X %Z")
            print(f"Check In opens on: {local_dt}")
        except Exception:
            pass

def checkIfRoomIsAvailable(params: CruiseURLParams) -> tuple[bool, List[Dict[str, Any]]]:
#def checkIfRoomIsAvailable(params: CruiseURLParams):
    """
    RSC Scraper Engine wrapper that verifies physical cabin availability on active voyages.
    
    Simulates a Next.js React Server Component web interaction (/room-selection/type-and-subtype) 
    to see if an active booking's specific room style is still available. Employs hardcoded baseline 
    testing states ('n') for profile criteria to cleanly monitor general inventory health.
    """
    # Optimized Next.js Server Component payload headers
    headers = {
        'user-agent': user_agent_web,
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        "Accept": "text/x-component",
        "RSC": "1",
    }

    # Map directly from the dataclass, maintaining the passenger qualifiers
    request_params = {
        'packageCode': params.packageCode,
        'sailDate': params.sailDate,
        'country': params.bookingOfficeCountryCode,
        'selectedCurrencyCode': params.currencyCode,
        'shipCode': params.packageCode[0:2] if params.packageCode else "",
        'cabinClassType': 'INTERIOR', # Endpoint defaults; returns all categories
        'roomIndex': '0',
        'r0a': params.numberOfAdults,
        'r0c': params.numberOfChildren,
        'r0b': 'n',

        # TO ASK: Why were passenger qualifiers hardcoded to 'n' in availability check?
        'r0r': 'n', # 'y' if params.police else 'n',
        'r0s': 'n', # 'y' if params.fire else 'n',
        'r0q': 'n', # 'y' if params.military else 'n',
        'r0t': 'n', # 'y' if params.senior else 'n',

        'r0d': 'INTERIOR',
        'r0D': 'y',
        'rgVisited': 'true',
        'r0C': 'y',
    }
    
    apiURL = f'https://www.{params.url_brand}.com/room-selection/type-and-subtype'    
    response = requests.get(apiURL, params=request_params, headers=headers)

    availableRooms = []
    
    # Extract structural array matrix out of the component text stream
    rooms = _extract_json_array(response.text, "rooms")
    
    if not rooms:
        return False, availableRooms
        
    try:
        stateroomTypes = rooms[0].get("options", {}).get("stateroomTypes", [])
    except (IndexError, AttributeError):
        return False, availableRooms
       
    for stateroomType in stateroomTypes:
        stateroomSubtypes = stateroomType.get("stateroomSubtypes", [])
        for stateroomSubtype in stateroomSubtypes:
            cur_subTypeCode = stateroomSubtype.get("code")
            cur_categoryCode = stateroomSubtype.get("categoryCode")
            
            # Exact structural target match found
            if cur_subTypeCode == params.stateroomSubtype and cur_categoryCode == params.stateroomCategoryCode:
                return True, []
                
            # FIXED: Defensively extract pricing trees to protect against missing API sub-keys
            pricing_struct = stateroomSubtype.get("pricing", {})
            invoice_struct = pricing_struct.get("invoice", {}) if pricing_struct else {}
            price = invoice_struct.get("total") if invoice_struct else None
            
            roomsLeft = stateroomSubtype.get("roomsLeft")
            
            # Formulate the alternative room tracking records
            room_display_name = f"{stateroomSubtype.get('name', '')} {cur_categoryCode} {cur_subTypeCode}".strip()
            availableRooms.append({
                "name": room_display_name,
                "price": price,
                "roomsLeft": roomsLeft
            })
                
    return False, availableRooms

def getRoomPriceViaAPI(urlParams: CruiseURLParams, roomNumber: Optional[str] = None) -> Dict[str, Any]:
#def getRoomPriceViaAPI(urlParams: CruiseURLParams, roomNumber=None):
    """
    Executes the micro-targeted API request payload to pull precise category room pricing.
    
    Assembles the deep, nested corporate schema request payload, maps strict JSON Booleans 
    (true/false) for qualifiers (military, senior, firefighter), and posts directly to 
    the pricing backend. Automatically intercepts schema violations or BAD_INPUT responses.
    """
    # Check room availability against the downstream checker
    roomAvailable, availableRooms = checkIfRoomIsAvailable(urlParams)    

    results = {
        'sailingNights': 0,
        'roomAvailable': roomAvailable
    }
    
    if not roomAvailable:
        results['availableRooms'] = availableRooms
        return results
    
    # Build tracking payloads and request contexts
    headers = {
        'user-agent': user_agent_web,
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
    }
        
    json_data = {
        'countryCode': urlParams.bookingOfficeCountryCode,
        'packageId': urlParams.packageCode,
        'sailDate': urlParams.sailDate,
        'currencyCode': urlParams.currencyCode,
        'language': 'en',
        'rooms': [
            {
                'stateroomTypeCode': urlParams.stateroomTypeName,
                'stateroomSubtypeCode': urlParams.stateroomSubtype,
                'categoryCode': urlParams.stateroomCategoryCode,
                'fareCode': 'BESTRATE',
                'accessible': False,
                'qualifiers': {
                    'fireFighter': urlParams.fire,
                    'military': urlParams.military,
                    'police': urlParams.police,
                    'senior': urlParams.senior,
                },
                'occupancy': {
                    'adultCount': urlParams.numberOfAdults,
                    'childCount': urlParams.numberOfChildren,
                },
            },
        ],
    }
    
    # Create a clean, direct reference alias to the target room dictionary
    room_config = json_data['rooms'][0]
    
    # Inject targeted elements if they are populated
    if urlParams.couponCode is not None:
        room_config['couponCode'] = urlParams.couponCode
        
    if roomNumber is not None:
        room_config['roomNumber'] = roomNumber
        
    if urlParams.state is not None:
        room_config['qualifiers']['stateCode'] = urlParams.state
        
    if urlParams.loyaltyNumber is not None:
        room_config['qualifiers']['loyaltyNumber'] = urlParams.loyaltyNumber    

    # Handle routing endpoints dynamically
    apiURL = f'https://www.{urlParams.url_brand}.com/checkout/api/v1/rooms/checkout'
  
    response = _execute_api_request(
          account_info=None,
          method="POST",
          url=apiURL,
          data=json.dumps(json_data),
          headers=headers,
          exit_on_fail=False
    )
    
    if response is not None:
        try:
            response_json = response.json()
            rooms = response_json.get("rooms")
        except Exception:
             rooms = None
    else:
        rooms = None
        
    if not rooms:
        print(f"apiURL: {apiURL}")
        print("Room Price Not Found")
        results['roomAvailable'] = False
        results['availableRooms'] = availableRooms
        return results
        
    room = rooms[0]
    
    # Safe multi-layered extraction for sailing nights metrics
    try:
        sailingNights = response_json.get("sailing", {}).get("itinerary", {}).get("sailingNights", 0)
    except AttributeError:
        sailingNights = 0
        
    results['sailingNights'] = sailingNights
    
    # 4. Extract pricing structures with bulletproof inner-dict fallbacks
    fare_mappings = {
        'baseFare': 'baseFare',
        'baseRefundableFare': 'baseRefundableFare',
        'allIncludedFare': 'allIncludedFare',
        'allIncludedRefundableFare': 'allIncludedRefundableFare'
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
            
    results['availableRooms'] = availableRooms
    return results

def getBoardingPass(accessInfo, booking, guestId: str) -> dict:
    """
    [FUTURE USE}
    Retrieves digital check-in boarding passes or luggage tag documentation assets.
    
    Pulls technical verification receipts and barcode metadata maps showing if a booking is 
    cleared to print standard pier entry documentation or if profile records require active 
    terminal management.
    """
    bookingId = booking.get("bookingId")
    accountId = accountInfo.access.id

    headers = {
        'content-type': 'application/json',
        'accept': 'application/json',
    }
    
    payload = {
        'guestReservationIds': [
            {
                'bookingId': bookingId,
                'guestId': guestId,
            },
        ],
    }
    
    api_url = f'https://aws-prd.api.rccl.com/en/{accountInfo.api_brand}/web/v2/guestCheckin/statuses/{accountId}'
    response = _execute_api_request(
            account_info=accountInfo,
            method="POST",
            url=api_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=10,
            exit_on_fail=False
    )

    ret_val = {} if resonse is None else response.json()
    return ret_val

##################################
# Dead/Obsolete/Unused functions
##################################
appkey_mobile = 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc'
appversion_mobile = '1.73.4'
user_agent_mobile = 'royal/1.73.4 (com.rccl.royalcaribbean; build:2528; android 16) okhttp/4.12.0'

def string_to_float(s: str) -> float:
    if not s:
        return 0.0

    s = s.strip()

    if "," in s and "." in s:
        # Both present → last one is decimal separator
        if s.rfind(",") > s.rfind("."):
            # European: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # American: 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # Only comma present
        parts = s.split(",")
        if len(parts[-1]) == 3 and parts[-1].isdigit():
            # 4,000 → thousands
            s = s.replace(",", "")
        else:
            # 4,0 → decimal
            s = s.replace(",", ".")
    elif "." in s:
        # Only dot present
        parts = s.split(".")
        if len(parts[-1]) == 3 and parts[-1].isdigit():
            # 4.000 → thousands
            s = s.replace(".", "")
        # else: 4.0 or 4.00 → decimal → keep dot
    # else: plain integer
    return float(s)

def days_between(d1, d2):
    dt1 = datetime.strptime(d1, "%Y%m%d")
    dt2 = datetime.strptime(d2, "%Y%m%d")
    return (dt2 - dt1).days

def getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest, passengerId,passengerName,room, orderCode, orderDate, owner):
        
    headers = {
    'User-Agent': user_agent_web,
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    'X-Requested-With': 'XMLHttpRequest',
    'Access-Token': access_token,
    'AppKey': appkey_web,
    'vds-id': accountId,
    'Account-Id': accountId,
    'channel': 'web',
    'Req-App-Id': 'Royal.Web.PlanMyCruise',
    'Req-App-Vers': '1.81.3',
    'Content-Type': 'application/json',
    'Origin': 'https://www.royalcaribbean.com',
    'DNT': '1',
    'Sec-GPC': '1',
    'Connection': 'keep-alive',
    'Referer': 'https://www.royalcaribbean.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    'Priority': 'u=0',
    # Requests doesn't support trailers
    # 'TE': 'trailers',
    }

    params = {
        'sailingId': ship + startDate,
        'currencyIso': currency,
        'categoryId': prefix,
    }

    
    json_data = {
        'productCode': product,
        'quantity': quantity,
        'signOnReservationId': reservationId,
        'signOnPassengerId': passengerId,
        'guests': [
            {
                'id': passengerId,
                'firstName': guest.get("firstName"),
                'lastName': guest.get("lastName"),
                'selected': False,
                'dob': guest.get("dob"),
                'reservationId': reservationId,
                'attachedToReservation': False,
            },
        ],
        'offeringId': product,
    }

    try:
        response = requests.post(
            'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/cart/v1/price',
            params=params,
            headers=headers,
            json=json_data,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)
    
    payload = response.json().get("payload")
    if payload is None:
        print("Payload Not Returned")
        return
        
    unitType = payload.get("prices")[0].get("unitType")
    
    if unitType in [ 'perNight', 'perDay' ]:
        price = payload.get("prices")[0].get("promoDailyPrice")
    else:
        price = payload.get("prices")[0].get("promoPrice")

    print(f"Paid Price: {paidPrice} Cart Price: {price}")

def getLoyalty(access_token,accountId,session):

    loyaltyNumber = None
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'account-id': accountId,
    }

    try:
        response = session.get('https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/loyalty/info', headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    loyalty = response.json().get("payload").get("loyaltyInformation")
    cAndANumber = loyalty.get("crownAndAnchorId")
    cAndALevel = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
    cAndAPoints = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints")
    cAndASharedPoints = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints")
   
    if cAndANumber is not None and cAndASharedPoints is not None and cAndASharedPoints > 0:
        print(f"\tC&A: {cAndANumber} {cAndALevel} - {cAndASharedPoints} Shared Points ({cAndAPoints} Individual Points)")
        loyaltyNumber = cAndANumber
    
    clubRoyaleLoyaltyIndividualPoints = loyalty.get("clubRoyaleLoyaltyIndividualPoints")
    if clubRoyaleLoyaltyIndividualPoints is not None and clubRoyaleLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier")
        print(f"\tCasino Royale Tier: {clubRoyaleLoyaltyTier} - {clubRoyaleLoyaltyIndividualPoints} Credits")

    captainsClubId = loyalty.get("captainsClubId")
    if captainsClubId is not None:
        captainsClubLoyaltyTier = loyalty.get("captainsClubLoyaltyTier")
        captainsClubLoyaltyIndividualPoints = loyalty.get("captainsClubLoyaltyIndividualPoints")
        captainsClubLoyaltyRelationshipPoints = loyalty.get("captainsClubLoyaltyRelationshipPoints")
        print(f"\tCaptain's Club Number: {captainsClubId} {captainsClubLoyaltyTier} TIER ({captainsClubLoyaltyRelationshipPoints} Shared Points, {captainsClubLoyaltyIndividualPoints} Individual Points)")
        loyaltyNumber = captainsClubId
        print("Using Captains Club Id To Check Cruise Prices")

    celebrityBlueChipLoyaltyIndividualPoints = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints")
    if celebrityBlueChipLoyaltyIndividualPoints is not None and celebrityBlueChipLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        print(f"\tBlue Chip Tier: {clubRoyaleLoyaltyTier} - {celebrityBlueChipLoyaltyIndividualPoints} Credits")

    return loyaltyNumber

def getShipDictionary():

    headers = {
        'appkey': appkey_mobile,
        'accept': 'application/json',
        'appversion': appversion_mobile,
        'accept-language': 'en',
        'user-agent': user_agent_mobile,
    }

    params = {
        'sort': 'name',
    }

    try:
        response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    ships = response.json().get("payload").get("ships")

    shipCodes = {}    
    for ship in ships:
        shipCode = ship.get("shipCode")
        name = ship.get("name")
        shipCodes[shipCode] = name
    return shipCodes

def getRoyalUp(access_token,accountId,cruiseLineName,session,apobj):
    # Unused, need javascript parsing to see offer
    # Could notify when Royal Up is available, but not too useful.
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.5',
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'X-Requested-With': 'XMLHttpRequest',
        'AppKey': 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm',
        'Access-Token': access_token,
        'vds-id': accountId,
        'Account-Id': accountId,
        'X-Request-Id': '67e0a0c8e15b1c327581b154',
        'Req-App-Id': 'Royal.Web.PlanMyCruise',
        'Req-App-Vers': '1.73.0',
        'Content-Type': 'application/json',
        'Origin': 'https://www.'+cruiseLineName+'.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Referer': 'https://www.'+cruiseLineName+'.com/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        'Priority': 'u=0',
        # Requests doesn't support trailers
        # 'TE': 'trailers',
    }
    
    try:
        response = requests.get('https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/upgrades', headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    for booking in response.json().get("payload"):
        print( booking.get("bookingId") + " " + booking.get("offerUrl") )

##################################
# End Dead/Obsolete/Unused functions
##################################

if __name__ == "__main__":
    config_path = get_config_path()
    
    try:
        # Load everything once. Logging, Apprise, and YAML values are now armed.
        config = load_config_objects(config_path)
        
        # Pass the fully built config object to main
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

        user_input = input("Enter y if want me to make the file: ")
        if user_input == "y":
            try:
                print("Downloading sample configuaration file...")
                url = 'https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/SAMPLE-SIMPLE-config.yaml'
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                localFileName = "config.yaml"
                if platform.system() == "iOS":
                    localFileName = os.path.expanduser('~/Documents') + "/config.yaml"    

                with open(localFileName, "wb") as f:
                    f.write(response.content)

                print(f"\n[+] Success: Created '{localFileName}' in the current directory.")
                print("--> Please edit Username/password then run the tool again")

            except requests.RequestException as req_err:
                sys.stderr.write(f"Failed to download sample configuration file from GitHub: {req_err}\n")
                sys.exit(1)
        else:
            print("Exiting. Please create a valid config.yaml file manually.")
            sys.exit(1)

    except Exception as exc:
        error_summary = f"{type(exc).__name__}: {exc}"
        
        # Standard fallback if the config failed to load entirely before the try block
        if 'config' in locals() and config:
            date_part = config.format_date(datetime.now().strftime("%Y%m%d"))
        else:
            date_part = datetime.now().strftime("%m/%d/%Y")
        timestamp = f"{date_part} {datetime.now().strftime('%X')}"
        
        # Using sys.stderr here is correct for standard error streams
        sys.stderr.write(f"ERROR: {error_summary}\n")
        traceback.print_exc()
        
        # Safe structural verification for notifications
        if 'config' in locals() and config and config.notifyOnError and config.apobj:
            if len(config.apobj) > 0:
                body = f"Script failed at {timestamp}\n{error_summary}"
                config.apobj.notify(body=body, title='Cruise Price Script Error')
                
        sys.exit(1)
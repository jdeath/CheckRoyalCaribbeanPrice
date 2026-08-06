# curl_cffi impersonates a real browser's TLS fingerprint so the cruise line's
# edge servers do not reject some IPs/systems as bots with 403 Access Denied
# (see GitHub issue #64). Fall back to plain requests where it is not
# installed (e.g. iOS), which works fine for most people.
try:
    from curl_cffi import requests
    impersonateArgs = {"impersonate": "chrome"}
except ImportError:
    import requests
    impersonateArgs = {}
import yaml
from datetime import datetime,date, timedelta, timezone
from urllib.parse import urlparse, parse_qs, quote
import re
import base64
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
try:
    from curl_cffi import requests
    impersonate_args = {"impersonate": "chrome"}
except ImportError:
    import requests
    impersonate_args = {}
import sys
import traceback
import time
import yaml

from apprise import Apprise
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
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

# Define global logging hooks so they are available everywhere in the script module
log = None
log_warn = None
log_err = None

# Rows collected across all accounts/bookings during a run, printed at the end as a
# compact check-in + final-payment summary table (see print_checkin_payment_table)
checkin_payment_rows: List[Dict[str, Any]] = []

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


foundItems = set()

# Seconds before giving up on an API call so a stalled connection cannot hang the run forever
# Override with requestTimeout in config.yaml if the API is slow for you
REQUEST_TIMEOUT = 30

# Transient failures worth retrying: throttling and server-side errors
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


# Windows PowerShell 5.x and the classic console host do not interpret ANSI color
# escapes by default, so the codes above print literally (e.g. a raw "<-[33m...").
# Enable virtual-terminal processing on the console so they render as color. Harmless
# where already enabled (Windows Terminal) or unsupported; failures are ignored.
if sys.platform == "win32":
    try:
        import ctypes
        _kernel32 = ctypes.windll.kernel32
        _handle = _kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        _mode = ctypes.c_uint32()
        if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
            # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
            _kernel32.SetConsoleMode(_handle, _mode.value | 0x0004)
    except Exception:
        pass

dateDisplayFormat = "%x"  # Uses the locale date format unless overridden by config


class TimeoutSession(requests.Session):
    # Retries transient failures (connection errors, timeouts, throttling, server errors)
    # with exponential backoff so a brief API blip does not cost items until the next run.
    # After the last attempt, exceptions raise and error statuses return to the caller as before.
    def __init__(self):
        super().__init__(**impersonateArgs)

    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', REQUEST_TIMEOUT)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = super().request(*args, **kwargs)
                if response.status_code not in RETRYABLE_STATUS_CODES or attempt == MAX_ATTEMPTS:
                    return response
                failure = f"server returned {response.status_code}"
            except Exception as e:
                if attempt == MAX_ATTEMPTS:
                    raise
                failure = str(e)
            wait = 2 ** attempt
            print(YELLOW + f"API request failed ({failure}); retrying in {wait}s (attempt {attempt} of {MAX_ATTEMPTS})" + RESET)
            time.sleep(wait)
            
class Logger(object):
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"
        self.log.write(delimiter)
        self.log.flush()


class PrintRedirector:
    """
    Intercepts and routes standard Python print() streams directly into the log engine.

    Replaces sys.stdout. When a developer executes a raw print() statement, this
    handler intercepts the text stream, strips trailing line breaks to protect against
    empty blank rows, and channels content cleanly into the active root logging handlers.

def expandEnvVars(value):
    # Config values that are exactly ${VAR_NAME} are replaced with that environment
    # variable, so secrets like passwords can stay out of config.yaml.
    # Only whole-value matches are expanded so passwords containing $ are untouched.
    if isinstance(value, dict):
        return {k: expandEnvVars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expandEnvVars(v) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', value)
        if match and match.group(1) in os.environ:
            return os.environ[match.group(1)]
    return value

def build_apprise_from_config(config_path):

    apobj = None
    notify_on_error = False
    try:
        with open(config_path, 'r') as file:
            data = expandEnvVars(yaml.safe_load(file) or {})
            notify_on_error = bool(data.get('notifyOnError', False))
            if 'apprise' in data:
                from apprise import Apprise
                apobj = Apprise()
                for apprise in data['apprise']:
                    url = apprise['url']
                    apobj.add(url)
    except Exception:
        # Config load errors will be handled by top-level error handler.
        pass
    return apobj, notify_on_error

def main(config_path=None):
    if config_path is None:
        config_path = get_config_path()

    # Set Time with AM/PM or 24h based on locale    
    locale.setlocale(locale.LC_TIME,'')
    timestamp = datetime.now()
    print(" ")
    
    #apobj = Apprise()
    
    if platform.system() == "iOS":
        global GREEN
        global RESET
        global BLUE
        global YELLOW
        GREEN = ""
        RESET = ""
        BLUE = ""
        YELLOW = ""
        
    try:    
        with open(config_path, 'r') as file:
            data = expandEnvVars(yaml.safe_load(file))
            if 'dateDisplayFormat' in data:
                global dateDisplayFormat
                dateDisplayFormat = data['dateDisplayFormat']
            if 'logFile' in data:
                logFile = data['logFile']
                if platform.system() == "iOS":
                    logFile = os.path.expanduser('~/Documents') + "/" + logFile
                print(f"Logging run to file: {logFile}")
                sys.stdout = Logger(logFile)
                sys.stderr = sys.stdout
                
            print("Report generated " + timestamp.strftime(dateDisplayFormat + " %X"))
            
            if 'apprise' in data:
                from apprise import Apprise
                apobj = Apprise()
                for apprise in data['apprise']:
                    url = apprise['url']
                    apobj.add(url)
            else:
                apobj = None
            
            if 'apprise' in data and 'apprise_test' in data and data['apprise_test']:
                apobj.notify(body="This is only a test. Apprise is set up correctly", title='Cruise Price Notification Test')
                print("Apprise Notification Sent...quitting")
                quit()

            reservationFriendlyNames = {}
            if 'reservationFriendlyNames' in data:
                reservationFriendlyNames=data.get('reservationFriendlyNames', {})

            if 'currencyOverride' in data:
                global currencyOverride
                currencyOverride = data['currencyOverride']
                print(YELLOW + f"Overriding Current Price Currency to {currencyOverride}" + RESET)
            
            if 'minimumSavingAlert' in data:
                global minimumSavingAlert
                minimumSavingAlert = float(data['minimumSavingAlert'])
                print(YELLOW + f"Only alerting for savings >= {minimumSavingAlert}" + RESET)

            if 'requestTimeout' in data:
                global REQUEST_TIMEOUT
                REQUEST_TIMEOUT = float(data['requestTimeout'])
                print(YELLOW + f"Using API request timeout of {REQUEST_TIMEOUT:g} seconds" + RESET)

            global shipDictionary
            shipDictionary = getShipDictionaryWeb()
            
            # Load watch list configuration
            watchListItems = []
            if 'watchList' in data:
                watchListItems = data['watchList']
            
            displayCruisePrices = False
            if 'displayCruisePrices' in data:
                displayCruisePrices = data['displayCruisePrices']
            
            showPromos = False
            if 'showPromos' in data:
                showPromos = data['showPromos']
            
            reservationPricePaid = {}
            if 'reservationPricePaid' in data:
                reservationPricePaid=data.get('reservationPricePaid', {})
                
            if 'accountInfo' in data:
                numAccounts = len(data['accountInfo'])
                for accountInfo in data['accountInfo']:
                    username = accountInfo['username']
                    password = accountInfo['password']
                    state = accountInfo.get("state",None)
                    senior = 'y' if accountInfo.get("senior",False) else 'n' 
                    military = 'y' if accountInfo.get("military",False) else 'n'
                    police = 'y' if accountInfo.get("police",False) else 'n'
                    
                    if 'cruiseLine' in accountInfo:
                       if accountInfo['cruiseLine'].lower().startswith("c"):
                        cruiseLineName = "celebritycruises"
                        friendlyCruiseLine = "Celebrity Cruises"
                       else:
                        cruiseLineName =  "royalcaribbean"
                        friendlyCruiseLine = "Royal Caribbean"
                    else:
                       cruiseLineName =  "royalcaribbean"
                       friendlyCruiseLine = "Royal Caribbean"

                    print(f"\n  Using {friendlyCruiseLine} for user {username}")
                    print(f"        {friendlyCruiseLine} loyalty number will be used for checking cabin prices")
                    session = TimeoutSession()
                    global foundItems # Clear found items between accounts
                    foundItems = set() # Clear found items between accounts
                    access_token,accountId,session = login(username,password,session,cruiseLineName)
                    stateFromProfile, loyaltyNumber, cAndAPoints = getProfile(access_token,accountId,cruiseLineName,session)
                    if state is None:
                        state = stateFromProfile
                    if cAndAPoints is None:
                        print("Error finding C&A Points")
                        cAndAPoints = 0
                    discountFlags = [loyaltyNumber, state, senior, military, police, cAndAPoints >= 340]
                    getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid,showPromos,discountFlags)
                
                    if numAccounts > 1:
                        session.close()
                        print("Sleeping for 5 seconds to allow API to cool down between accounts")
                        time.sleep(5)
                
            if 'cruises' in data:
                for cruises in data['cruises']:
                        cruiseURL = cruises['cruiseURL'] 
                        paidPrice = cruises['paidPrice']
                        paidPriceStruct = {"paidPrice":float(paidPrice)} # For New structure
                        session = TimeoutSession()
                        get_cruise_price(cruiseURL, session, paidPriceStruct, apobj, False, None,None)
                        
    except FileNotFoundError:
        print("No Configuration File Found")
        print("Would You like me to make a barebones file for you?")
        user_input = input("Enter y if want me to make the file: ")
        if user_input == "y":
            url = 'https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/SAMPLE-SIMPLE-config.yaml'
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            localFileName = "config.yaml"
            if platform.system() == "iOS":
                localFileName = os.path.expanduser('~/Documents') + "/config.yaml"    
            with open(localFileName, "wb") as f:
                f.write(response.content)
            print("File in current directory. Edit Username/password then run tool again")

def _extract_json_array(text: str, key: str):
    """Find "key": [ ... ] handling arbitrary nesting."""
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


def get_final_payment_date(number_of_nights: int, sail_date: Union[str, date, datetime]) -> date:
    """
    Calculates final payment settlement timelines based on duration rules.

    Accepts string timestamps or explicit date objects. Computes strict policy deadlines
    by calculating offsets from the ship's departure date (75 days for short sailings,
    90 days for standard voyages, 120 days for extended itineraries).
    """
    # Standardize the input into a solid date object defensively
    if isinstance(sail_date, (datetime, date)):
        # If it's a datetime, extract just the date portion
        date_of_sailing = sail_date.date() if isinstance(sail_date, datetime) else sail_date
    elif isinstance(sail_date, str):
        # Strip out any potential dash or slash delimiters left over by the caller
        clean_date_str = sail_date.replace("-", "").replace("/", "")
        try:
            date_of_sailing = datetime.strptime(clean_date_str, "%Y%m%d").date()
        except ValueError as e:
            raise ValueError(f"Invalid sail_date string format '{sail_date}'. Expected YYYYMMDD or YYYY-MM-DD.") from e
    else:
        raise TypeError("sail_date must be a string, date, or datetime object.")

    # Apply final payment window rules (from Royal Caribbean FAQ)
    if number_of_nights < 5:
        final_payment_deadline = 75
    elif number_of_nights < 15:
        final_payment_deadline = 90
    else:
        final_payment_deadline = 120

    return date_of_sailing - timedelta(days=final_payment_deadline)


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

    # Parse the URL parameters and save in a class instance
    return CruiseURLParams(
        is_royal="royal" in domain,
        sail_date=params.get("sailDate", [None])[0],
        currency_code=params.get("selectedCurrencyCode", ["USD"])[0],
        booking_office_country_code=params.get("country", ["USA"])[0],
        ship_code=params.get("ship_code", [None])[0],
        cabin_class_string=cabin_string,
        stateroom_type_name=r0d_list[0] if r0d_list else None,
        stateroom_subtype=params.get("r0e", [None])[0],
        stateroom_category_code=params.get("r0f", [None])[0],
        package_code=params.get("package_code", [None])[0],
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
    mapping = {
        "I": "INTERIOR",
        "O": "OUTSIDE",
        "B": "BALCONY",
        "D": "DELUXE",
        "C": "CONCIERGE"
    }
    return mapping.get(room_type_code, "NONE")


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
        sys.exit(1)

    if response.status_code != 200:
        log(f"Login attempt got return code {response.status_code}")
        log(f"{account_info.cruise_line} website might be down, username/password incorrect, or have unsupported symbol in password. Quitting.")
        sys.exit(1)

    # Parse out the account's ID and access token
    access_token = response.json().get("access_token")

    try:
        list_of_strings = access_token.split(".")
        if len(list_of_strings) < 2:
            raise ValueError("Token does not contain a valid JWT payload segment.")
        string1 = list_of_strings[1]
        decoded_bytes = base64.b64decode(string1 + '==')
        auth_info = json.loads(decoded_bytes.decode('utf-8'))
        account_ID = auth_info["sub"]
    except(IndexError, ValueError, KeyError, AttributeError, TypeError) as parse_err:
        # AttributeError/TypeError: a 200 with no access_token leaves it None
        log(f"Error parsing authentication token structure: {parse_err}")
        sys.exit(1)

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
    the active brand tracking index to route downstream web requests correctly.
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

        total_nights, total_trips = get_number_of_nights(account_info, c_and_a_number)
        if total_nights > 0:
            log(f"\tTotal Trips on Royal: {total_trips} - Total Nights: {total_nights}")

        # Club Royale tier currently is not part of the loyalty payload; use a helper to compute it
        # but keep the payload check in case it ever comes back (key name may need to change)
        casino_points = loyalty.get("clubRoyaleLoyaltyIndividualPoints",0) or 0
        club_royale_loyalty_tier = loyalty.get("clubRoyaleLoyaltyTier") or get_club_royale_tier(casino_points)
        if club_royale_loyalty_tier:
            log(f"\tCasino Royale Tier: {club_royale_loyalty_tier} - {casino_points} Credits")

    # Get and display Celebrity (Captain's Club and Blue Chip) information
    if captains_club_ID:
        cc_level = loyalty.get("captainsClubLoyaltyTier")
        cc_individual = loyalty.get("captainsClubLoyaltyIndividualPoints", 0)
        cc_shared = loyalty.get("captainsClubLoyaltyRelationshipPoints", 0)
        log(f"\tCaptain's Club Number: {captains_club_ID} {cc_level} TIER ({cc_shared} Shared Points, {cc_individual} Individual Points)")

        total_nights, total_trips = get_number_of_nights(account_info, captains_club_ID)
        if total_nights > 0:
            log(f"\tTotal Trips on Celebrity: {total_trips} - Total Nights: {total_nights}")

        celebrity_blue_chip_loyalty_tier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        if celebrity_blue_chip_loyalty_tier != "Unknown":
            celebrity_blue_chip_loyalty_individual_points = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints",0)
            log(f"\tBlue Chip Tier: {celebrity_blue_chip_loyalty_tier} - {celebrity_blue_chip_loyalty_individual_points} Points")

    # Return the correct loyality number based on the account being used
    loyalty_number_to_use = captains_club_ID if account_info.is_celebrity else c_and_a_number

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
def get_voyages(account_info: AccountInfo, discounts: CruiseURLParams, ship_dictionary: ShipRegistry) -> None:
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
    apobj = config.apobj
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
        guests = booking.get("passengersInStateroom", [])
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
        metrics = _calculate_passenger_metrics(guests, sail_date, booking, brand_code, display_cruise_prices)

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
        cruise_paid_price_from_API = result.get("prices", [])

        for cur_price in cruise_paid_price_from_API:
            price_type_code = cur_price.get("priceTypeCode", "")
            amount = cur_price.get("amount")
            if not amount:
                continue

            # Parse the price gathered from the server
            if price_type_code == "GROSS_TOTALS":
                gross_totals = amount
            elif price_type_code == "GRATUITIES":
                prepaid_grats_flag = True
                payment_string += f" Including: {amount:.2f} Gratuities"
            elif price_type_code == "TRIP_INSURANCE":
                insurance_flag = True
                payment_string += f" Including: {amount:.2f} Insurance"
            elif "ALL_INC" in price_type_code or "INCLUDED" in price_type_code:
                all_included_flag = True
                payment_string += f" Including: {amount:.2f} All Included Drinks/WiFi"
            elif price_type_code == "BALANCE_DUE":
                payment_string += f" You Still Owe: {amount:.2f}"

        # Store the parsed information into a dictionary for easy passing around
        paid_price_struct = {}
        if gross_totals is not None:
            paid_price_struct['reservation'] = reservation_ID
            paid_price_struct['paid_price'] = gross_totals
            paid_price_struct['gratuities'] = prepaid_grats_flag
            paid_price_struct['trip_insurance'] = insurance_flag
            paid_price_struct['all_in_upgrade'] = all_included_flag
            log(f"Cruise Fare - Total {gross_totals:.2f}{payment_string}")

        final_payment_date = get_final_payment_date(number_of_nights, sail_date)
        final_payment_date_display = final_payment_date.strftime(date_display_format)

        # Record this booking for the end-of-run check-in / final-payment summary table.
        # Include the room number so multiple cabins on the same sailing are distinct.
        summary_name = ship_dictionary.get_ship(ship_code)
        if stateroom_number:
            summary_name += f" ({stateroom_number})"
        summary_reservation = str(reservation_ID)
        if summary_reservation in reservation_friendly_names:
            summary_reservation += f" ({reservation_friendly_names.get(summary_reservation)})"
        balance_due = derive_balance_due(booking)
        balance_due_amount = booking.get("balanceDueAmount")
        if str(reservation_ID) in config.paid_reservations:
            balance_due = False   # user vouches for it (reservationsPaidInFull)
        record_checkin_payment_row({
            "name": summary_name,
            "reservation": summary_reservation,
            "sail_date": sail_date,
            "checkin_label": checkin_label or "TBD",
            "final_payment": final_payment_date,
            "past_final_payment": date.today() > final_payment_date,
            "balance_due": balance_due,
            "dedupe_key": f"{reservation_ID}|{sail_date}",
        })

        if balance_due is True:
            owed = (f"{balance_due_amount:.2f}" if isinstance(balance_due_amount, (int, float))
                    else "unknown")
            log(YELLOW + f"Remaining Cruise Payment Balance is {owed} due {final_payment_date_display}" + RESET)

        paid_price_struct['booked_obc'] = get_OBC(account_info, booking)

        if show_promos:
            get_all_promotions(account_info, booking)

        # Current Web Market Pricing Block
        if display_cruise_prices:
            # Build the complex Checkout/Room Selection URL

            # Map legacy manual pricing text overrides from configuration yaml
            if isinstance(reservation_price_paid, dict) and reservation_price_paid:
                if str(reservation_ID) in reservation_price_paid:
                    paid_price = reservation_price_paid.get(str(reservation_ID))
                    if paid_price is not None:
                        paid_price_struct['paid_price'] = float(paid_price)
            elif isinstance(reservation_price_paid, list):
                for reservation in reservation_price_paid:
                    # str-compare: a missing/non-numeric 'reservation' key must
                    # not crash the whole booking loop
                    if str(reservation_ID) == str(reservation.get("reservation")):
                        for item in reservation:
                            paid_price_struct[item] = reservation.get(item)

            if booking.get("stateroomType") != "NONE":
                get_cruise_price(account_info,
                                 booking,
                                 ship_dictionary,
                                 automatic_URL=True,
                                 paid_price_struct=paid_price_struct,
                                 discounts=discounts)
            else:
                log(YELLOW + "Cannot Check Cruise Price - Use Manual URL Method" + RESET)

        # Get the extra add-ons purchased for this voyage
        get_orders(account_info, booking, metrics)
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
                process_watch_list_for_booking(account_info, booking, watch_list_items, apobj, passenger_info)

            log(" ")


def get_dining_and_prices(account_info: AccountInfo, booking: Dict[str, Any]) -> Dict[str, List[Any]]:
    """
    Extracts explicit reservation pricing details and dining choices from booked summaries.

    Queries specific reservation components using transient amendment keys. Implements
    safety fallbacks to return blank lists if network timeouts or structural processing
    faults occur, ensuring downstream processes don't break.
    """
    # Safely pull the token and country straight from the booking payload
    amendtoken = booking.get("amendToken")
    country = booking.get("bookingOfficeCountryCode", "USA")

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


def get_cruise_price(account_info: AccountInfo,
                     booking: Dict[str, Any],
                     ship_dictionary: ShipRegistry,
                     automatic_URL: bool = True,
                     paid_price_struct: Dict[str, Any] = None,
                     discounts: Optional[DiscountProfile] = None
) -> None:
    """
    Performs dynamic live web-pricing evaluations for a specific stateroom or prospective cruise.

    Simulates consumer search requests to locate real-time pricing and tax figures.
    Compares current market pricing options against the original booked price, logs
    pricing changes to the console, and triggers deal notifications for verified drops.
    """
    # Pull properties from the foundational domain entities
    session = account_info.access.session
    apobj = config.apobj
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
        guests = booking.get("passengersInStateroom", booking.get("passengers", []))
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

#        if have_a_senior:
#            url_params.senior = True
#
    # Absorb any YAML overrides safely now that url_params is guaranteed to be an object
    url_params.apply_overrides(paid_price_struct)

    # Capture target price bounds if they exist
    # NOTE: both paid_price and paidPrice are valid keys,
    #       depending on booked vs. prospective cruises
    paid_price = paid_price_struct.get("paid_price") or paid_price_struct.get("paidPrice") if paid_price_struct else None
    room_number = None

    # Primary API pricing check pass
    results = get_room_price_via_API(url_params, room_number)
    room_available = results.get("room_available")

    # Defensive Fallback: If a coupon code explicitly bricks availability, retry without it
    if not room_available and url_params.coupon_code is not None:
        log(f"Coupon Code {url_params.coupon_code} may have failed, trying without using it")
        url_params.coupon_code = None
        results = get_room_price_via_API(url_params, room_number)
        room_available = results.get("room_available")

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
        final_payment_date = get_final_payment_date(resolved_nights, url_params.sail_date)
    except (TypeError, ValueError):
        final_payment_date = date.max

    # Reach into the global ship mapper object natively
    ship_name = ship_dictionary.get_ship(url_params.ship_code)
    sail_date_display = config.format_date(url_params.sail_date)
    pre_string = f"{sail_date_display} {ship_name} {url_params.cabin_class_string} {url_params.stateroom_category_code}"

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
            return

        # The keys always exist (so .get defaults never apply) but their values
        # can be JSON null - treat a null fare like missing fare data instead of
        # crashing on the first {price:.2f} format below
        if fare_struct.get("fare") is None:
            log(f"{YELLOW}{pre_string}: No fare pricing returned; cannot compare price{RESET}")
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

    # Path 1: Room is completely unlisted or sold out
    if not room_available:
        text_string = f"{pre_string} Not For Sale"
        if automatic_URL and past_final_payment_date:
            text_string += f". Past Final Payment Date of {final_payment_date_display}"

        log(YELLOW + text_string + RESET)

        # Only notify if it's a watchlist item (automatic_URL is False)
        if not automatic_URL and apobj is not None:
            apobj.notify(body=text_string, title='Cruise Room Not Available')

        # TODO: This code block will print the "Available Rooms" line even if the count is 0;
        #       do we want to use this commented-out block instead
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
        return

    obc_value = float(obc or 0.0)
    obc_string = f"{obc_value:.2f}"

    # Path 2: Standard Pricing Evaluation
    if paid_price is None:
        log(GREEN + f"{pre_string}:" + RESET + f" Current Price {price:.2f} {url_params.currency_code}")
        return

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
            else:
                log(RED + text_string + RESET)
                if apobj is not None:
                    apobj.notify(body=text_string, title='Cruise Price Alert')

        # Sub-branch 2: Booked drop but locked behind final lock dates
        if automatic_URL and past_final_payment_date:
            text_string = f"Past Final Payment Date of {final_payment_date_display}: {pre_string} New price of {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                text_string += f", not including {obc_string} USD OBC,"
            text_string += f" is lower than {paid_price:.2f}"
            log(YELLOW + text_string + RESET)

        # Sub-branch 3: Speculative prospective watchlist match
        if not automatic_URL:
            text_string = f"Consider Booking! {pre_string}: New price of {price:.2f} {url_params.currency_code}"
            if obc_value > 0:
                text_string += f", not including {obc_string} OBC,"
            text_string += f" is lower than watchlist price of {paid_price:.2f}"

            if config.minimum_saving_alert is not None and saving < config.minimum_saving_alert:
                text_string += f" (Saving {saving:.2f} < minimumSavingAlert {config.minimum_saving_alert:.2f}; no notification sent)"
                log(YELLOW + text_string + RESET)
            else:
                log(RED + text_string + RESET)
                if apobj is not None:
                    apobj.notify(body=text_string, title='Cruise Price Alert')
    else:
        # Current catalog price is equal to or higher than target price thresholds
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

        log(temp_string)


def get_room_price_via_API(url_params: CruiseURLParams, room_number: Optional[str] = None) -> Dict[str, Any]:
    # Check room availability against the downstream checker
    room_available, available_rooms = check_if_room_is_available(url_params)

    results = {
        'sailing_nights': 0,
        'room_available': room_available
    }

    if not room_available:
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
        'language': 'en',
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
    else:
        rooms = None

    if not rooms:
        log("Room Price Not Found")
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

    results['available_rooms'] = available_rooms
    return results


def check_if_room_is_available(params: CruiseURLParams) -> tuple[bool, List[Dict[str, Any]]]:
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

    # TODO: Migrate to _execute_api_request() with on_failure="retry".
    # This loop lookup is an excellent candidate for our new exponential backoff
    # engine, but is left single-shot for this PR to keep the price-tracking loop
    # scope completely isolated and test-stabilized.
    api_URL = f'https://www.{params.url_brand}.com/room-selection/type-and-subtype'
    try:
        response = requests.get(api_URL, params=request_params, headers=headers,
                                timeout=config.request_timeout if config else REQUEST_TIMEOUT)
    except Exception as err:
        log (f"Unable to check room availability with server ({err})")
        return False, []

    # Extract structural array matrix out of the component text stream
    available_rooms = []
    rooms = _extract_json_array(response.text, "rooms")

    if not rooms:
        return False, available_rooms

    try:
        stateroom_types = rooms[0].get("options", {}).get("stateroomTypes", [])
    except (IndexError, AttributeError):
        return False, available_rooms

    for stateroom_type in stateroom_types:
        stateroom_subtypes = stateroom_type.get("stateroomSubtypes", [])
        for stateroom_subtype in stateroom_subtypes:
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
                return True, []

            # Defensively extract pricing trees to protect against missing API sub-keys
            pricing_struct = stateroom_subtype.get("pricing", {})
            invoice_struct = pricing_struct.get("invoice", {}) if pricing_struct else {}
            price = invoice_struct.get("total") if invoice_struct else None

            rooms_left = stateroom_subtype.get("roomsLeft")

            # Formulate the alternative room tracking records
            room_display_name = f"{stateroom_subtype.get('name', '')} {cur_category_code} {cur_subtype_code}".strip()
            available_rooms.append({
                "name": room_display_name,
                "price": price,
                "rooms_left": rooms_left
            })

    # Fall-through state: The loops completed without finding our exact cabin style.
    # The room is sold out, so we return False along with the collected alternative options.
    return False, available_rooms


####################################
# Add-On/Order/Cart Engine functions
####################################
def get_new_order_price(
    account_info: AccountInfo,
    booking: Dict[str, Any],
    apobj: Optional[Apprise],
    ctx: WatchItemContext
) -> None:
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

    currency = config.currency_override if config.currency_override else ctx.currency
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

    params = {
        'reservationId': reservation_ID,
        'startDate': start_date,
        'currencyIso': currency,
        'passengerId': passenger_ID,
    }

    # Get the information on the watched item from the server
    url = f'https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/commerce-api/catalog/v2/{ship}/categories/{prefix}/products/{product}'
    response = _execute_api_request(account_info, "GET", url, params=params)

    try:
        payload = response.json().get("payload")
        if payload is None:
            # Force an exception if the payload layer itself is None
            raise ValueError
    except (AttributeError, ValueError, TypeError):
        log(f"{prefix} {product} not available for passenger")
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

    per_day_price = sales_unit in ['PER_NIGHT', 'PER_DAY']
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
        return

    # Process Deal Alerts
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
#        if for_watch:
#            text = f"{passenger_name}: Book! {title} Price "
#            if per_day_price:
#                text += "per night "
#            text += f"is lower: {current_price} {currency} than {paid_price} {currency}"
#        else:
#            text = f"{passenger_name}: Rebook! {title} Price "
#            if per_day_price:
#                text += "per night "
#            text += f"is lower: {current_price} {currency} than {paid_price} {currency}"

        # Reaching into global config for alerts configuration
        if config.minimum_saving_alert is not None:
            text += f" ({saving_label})"

        promo_description = payload.get("promoDescription")
        if promo_description:
            promotion_title = promo_description.get("displayName")
            text += f'\n\t\tPromotion:{promotion_title}'

        if for_watch:
            text += f'\n\tBook at https://www.{account_info.url_brand}.com/account/cruise-planner/category/{prefix}/product/{product}?bookingId={reservation_ID}&shipCode={ship}&sailDate={start_date}'
        else:
            text += f'\n\tCancel Order {order_date} {order_code} at https://www.{account_info.url_brand}.com/account/cruise-planner/order-history?bookingId={reservation_ID}&shipCode={ship}&sailDate={start_date}'

        if not owner:
            text += "\tThis was booked by another in your party. They will have to cancel/rebook for you!"

        if config.minimum_saving_alert is not None and saving_for_alert < config.minimum_saving_alert:
            text += f" ({saving_label} < minimumSavingAlert {config.minimum_saving_alert:.2f}; no notification sent)"
            log(YELLOW + text + RESET)
        else:
            log(RED + text + RESET)
            if apobj is not None:
                apobj.notify(body=text, title='Cruise Addon Price Alert')
    else:
        # Current price on server is higher than the paid price ("currently best price" path)
        if for_watch:
            temp_string = GREEN + f"[WATCH] {display_name} (Cabin {room}) {title} price is higher than watch price: {paid_price:.2f} {currency}" + RESET
        else:
            temp_string = GREEN + f"{display_name} (Cabin {room}) has best price "
            if per_day_price:
                temp_string += "per night "
            temp_string += f"for {title} of: {paid_price:.2f} {currency}" + RESET
#        if for_watch:
#            temp_string = GREEN + f"{passenger_name.ljust(10)} {title} price "
#            if per_day_price:
#                temp_string += "per night "
#            temp_string += f"is higher than watch price: {paid_price:.2f} {currency}" + RESET
#        else:
#            temp_string = GREEN + f"{passenger_name.ljust(10)} (Cabin {room}) has best price "
#            if per_day_price:
#                temp_string += "per night "
#            temp_string += f"for {title} of: {paid_price:.2f} {currency}" + RESET
        if current_price > paid_price:
            temp_string += f" (now {current_price:.2f} {currency})"
        log(temp_string)


def process_watch_list_for_booking(
    account_info: AccountInfo,
    booking: Dict[str, Any],
    watch_list_items: List[WatchListItem],
    apobj: Optional[Apprise],
    passenger_info: Dict[str, Any]
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
        currency = getattr(watch_item, 'currency', "USD")

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
            currency=currency,
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
        get_new_order_price(account_info, booking, apobj, ctx)


def get_orders(account_info: AccountInfo, booking: Dict[str, Any], metrics: Dict[str, Any]) -> None:
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

    # Handle global currency overrides cleanly
    if config.currency_override:
        currency = config.currency_override
    else:
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
            'currencyIso': currency,
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

                        currency = guest.get("priceDetails", {}).get("currency")
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
                            currency=currency,
                            guest_age_string=guest_age_string,
                            sales_unit=sales_unit,
                            for_watch=False,
                            order_code=order_code,
                            order_date=order_date,
                            owner=owner,
                            reservations=[],
                            reservation_id=guestreservation_ID
                        )

                        get_new_order_price(account_info, booking, config.apobj, ctx)


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
            promo_line = f"[PROMO] {banner.get('heading3', '')} {banner.get('heading4', '')} - {banner.get('heading1', '')} {date_range}"
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
            promo_line = f"[PROMO] {description or promo_ID}"
            if category_code:
                promo_line += f" ({category_code})"
            promo_line += f" {date_range}"

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
        'currencyIso': booking.get("bookingCurrency"),
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

    # Build the dictionary of parameters that URLs for GTY and non-GTY share completely
    params = {
        'packageCode': booking.get("packageCode"),
        'sailDate': url_sail_date,
        'country': booking.get("bookingOfficeCountryCode"),
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
#        'r0s': 'n', # Formerly hardcoded; kept for a revert in case of problems
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
    if stateroom_number == "GTY":
        base_url = f"https://www.{account_info.url_brand}.com/checkout/add-ons"
        params['r0g'] = 'BESTRATE'
        params['r0h'] = 'n'
        params['r0C'] = 'y'
    else:
        base_url = f"https://www.{account_info.url_brand}.com/room-selection/room-location"

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
def get_number_of_nights(account_info: AccountInfo, loyalty_number: str) -> Tuple[int, int]:
    """
    Queries cumulative night metrics and cruise totals for a specified loyalty profile.

    Queries corporate historical data points. Runs with 'on_failure="retry"' inside the
    request core so historical lookup dropouts won't crash critical root execution pipelines.
    """
    total_nights, total_trips = -1, -1

    url = f"https://aws-prd.api.rccl.com/en/{account_info.api_brand}/web/v1/guestAccounts/loyalty/history/summary"

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
    display_prices: bool
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

    for guest in guests:
        stateroom_category_code = guest.get("stateroomCategoryCode")

        # Apply legacy GTY room structure workarounds
        if stateroom_category_code is None and stateroom_subtype is None:
            if display_prices:
                log(YELLOW + "Data is missing from API. Code is taking a guess to fixing" + RESET)
                log(YELLOW + "Add category override in config.yaml if wrong category" + RESET)

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
            have_a_senior = above_age_on_sail_date(birth_date, sail_date, 55)

        if above_age_on_sail_date(birth_date, sail_date, 12):
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
        "sub_type": stateroom_subtype
    }

'''
######################################################
# Dead/Obsolete/Unused functions
# WARNING: These were NOT refactored to use snake_case
# or renamed functions; these will need to be updated
# if resurrected
######################################################
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

def getDiningAndPrices(session, amendtoken, isRoyal: bool = True , country: str = "USA") -> dict:

    if isRoyal:
        RSC_URL = "https://www.royalcaribbean.com/usa/en/booked/overview"
    else:
        RSC_URL = "https://www.celebritycruises.com/usa/en/booked/overview"

    HEADERS = {
        "User-Agent": user_agent_web,
        "Accept": "text/x-component",
        "RSC": "1",
    }

    try:
        resp = session.get(RSC_URL, params={"token": amendtoken, "country": country}, headers=HEADERS)
    except Exception as e:
        print(f"Can't get dining/pricing info; skipping\n(program exception '{e}')")
        return {}

    text = resp.text
    result = {}

    dining = _extract_json_array(text, "diningSelection")
    if dining is not None:
        result["diningSelection"] = dining

    prices = _extract_json_array(text, "prices")
    if prices is not None:
        result["prices"] = prices
    
    pricingAddOns = _extract_json_array(text, "pricingAddOns")
    if pricingAddOns is not None:
        result["pricingAddOns"] = pricingAddOns
        
    return result


def getFinalPaymentDate(numberOfNights, sailDate):
    
    dateOfSailing = date(int(sailDate[0:4]), int(sailDate[4:6]), int(sailDate[6:8]))

    # From Royal Caribbean FAQ
    if numberOfNights < 5:
        finalPaymentDeadline = 75
    elif numberOfNights < 15:
        finalPaymentDeadline = 90
    else:
        finalPaymentDeadline = 120
    
    return dateOfSailing - timedelta(days=finalPaymentDeadline)
   
def login(username,password,session,cruiseLineName):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': user_agent_web,
    }
    
    urlSafePassword  = quote(password, safe='')
    
    data = f'grant_type=password&username={username}&password={urlSafePassword}&scope=openid+profile+email+vdsid'
    #print(data)
    #quit()
    try:
        response = session.post('https://www.'+cruiseLineName+'.com/auth/oauth2/access_token', headers=headers, data=data)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)
    
    if response.status_code != 200:
        print(f"{cruiseLineName} website might be down, username/password incorrect, or have unsupported symbol in password. Quitting.")
        sys.exit(1)
          
    access_token = response.json().get("access_token")
    
    list_of_strings = access_token.split(".")
    string1 = list_of_strings[1]
    decoded_bytes = base64.b64decode(string1 + '==')
    auth_info = json.loads(decoded_bytes.decode('utf-8'))
    accountId = auth_info["sub"]
    return access_token,accountId,session


def getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest, passengerId,passengerName,room, orderCode, orderDate, owner):

    headers = {
    'User-Agent': USER_AGENT_WEB,
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    'X-Requested-With': 'XMLHttpRequest',
    'Access-Token': access_token,
    'AppKey': APPKEY_WEB,
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
        response = session.post(
            'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/cart/v1/price',
            params=params,
            headers=headers,
            json=json_data,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; skipping this item\n(program exception '{e}')")
        return
    
    payload = response.json().get("payload")
    if payload is None:
        log("Payload Not Returned")
        return

    unitType = payload.get("prices")[0].get("unitType")

    if unitType in [ 'perNight', 'perDay' ]:
        price = payload.get("prices")[0].get("promoDailyPrice")
    else:
        price = payload.get("prices")[0].get("promoPrice")

    print(f"Paid Price: {paidPrice} Cart Price: {price}")
    
def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,guestAgeString,passengerName,room, orderCode, orderDate, owner, forWatch, cruiseLineName, salesUnit=None, numberOfNights=None):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'vds-id': accountId,
    }
    
    if currencyOverride != "":
        currency = currencyOverride
    
    params = {
        'reservationId': reservationId,
        'startDate': startDate,
        'currencyIso': currency,
        'passengerId': passengerId,
    }
    
    try:
        response = session.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog/v2/{ship}/categories/{prefix}/products/{product}',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; skipping this item\n(program exception '{e}')")
        return

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
        title = title + f" ({variant})"

    perDayPrice = salesUnit in [ 'PER_NIGHT', 'PER_DAY' ]
    
    newPricePayload = payload.get("startingFromPrice")
    
    if newPricePayload is None:
        if not forWatch:
            tempString = YELLOW + f"\t{passengerName.ljust(10)} ({room}) has best price " 
            if perDayPrice:
                tempString += "per night "
            tempString += f"for {title} of: {paidPrice} {currency} (No Longer for Sale)" + RESET
        else:
            tempString = YELLOW + f"\t{passengerName.ljust(10)} {title} not available or already booked" + RESET
            
        print(tempString)
        return
    
    # This should pull correct infant, child, or adult price
    currentPrice = newPricePayload.get(guestAgeString + "PromotionalPrice")
    if not currentPrice:
        currentPrice = newPricePayload.get(guestAgeString + "ShipboardPrice")

    # Infant price is often None, this just sets to 0 to avoid error
    # Should never happen since should not check prices that are 0 to begin with
    
    if not currentPrice:
        currentPrice = 0
    
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
        if minimumSavingAlert is not None:
            text += f" ({savingLabel})"
        
        promoDescription = payload.get("promoDescription")
        if promoDescription:
            promotionTitle = promoDescription.get("displayName")
            text += f'\n\t\tPromotion:{promotionTitle}'

        if forWatch:
            text += f'\n\tBook at https://www.{cruiseLineName}.com/account/cruise-planner/category/{prefix}/product/{product}?bookingId={reservationId}&shipCode={ship}&sailDate={startDate}'
        else:
            text += f'\n\tCancel Order {orderDate} {orderCode} at https://www.{cruiseLineName}.com/account/cruise-planner/order-history?bookingId={reservationId}&shipCode={ship}&sailDate={startDate}'
        
        if not owner:
            text += "\tThis was booked by another in your party. They will have to cancel/rebook for you!"
            
        if minimumSavingAlert is not None and savingForAlert < minimumSavingAlert:
            text += f" ({savingLabel} < minimumSavingAlert {minimumSavingAlert}; no notification sent)"
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
            tempString = GREEN + f"{passengerName.ljust(10)} ({room}) has best price "
            if perDayPrice:
                tempString += "per night "
            tempString += f"for {title} of: {paidPrice} {currency}" + RESET
        if currentPrice > paidPrice:
            tempString += f" (now {currentPrice} {currency})"
        print(tempString)
        
def processWatchListForBooking(access_token, accountId, session, reservationId, ship, startDate, passengerId, passengerName, room, watchListItems, apobj, cruiseLineName):
    """
    Process watch list items for a specific passenger to check for price drops
    """
    if not watchListItems:
        return
    
    for watchItem in watchListItems:
        name = watchItem.get('name', 'Unknown Item')
        product = watchItem.get('product')
        prefix = watchItem.get('prefix')
        watchPrice = float(watchItem.get('price', 0))
        enabled = watchItem.get('enabled', True)  # Default to True if not specified
        guestAgeString = (watchItem.get('guestAgeString',"adult")).lower()
        currency = watchItem.get('currency',"USD")
        
        reservationList = watchItem.get('reservations',None)
        
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
        #watchDisplayName = f"[WATCH] {name} - {passengerName} ({room})"
        
        # The real name is displayed, so no need to display user provided name
        watchDisplayName = f"[WATCH] {passengerName} ({room})"
        
        # Set placeholder values for order-specific fields since these aren't actual orders
        getNewBeveragePrice(
            access_token, accountId, session, reservationId, ship, startDate,
            prefix, watchPrice, currency, product, apobj, passengerId,guestAgeString,
            watchDisplayName, room, "WATCH-LIST", "Watch List", True, True, cruiseLineName, None, None
        )

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

def getNumberOfNights(access_token,accountId,session,loyaltyNumber):

    # Set to -1 as this API call sometimes fails
    # But not worth quiting over
    totalNights = -1
    totalTrips = -1
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'account-id': accountId,
    }

    params = {
        'loyaltyNumber': loyaltyNumber,
    }

    try:
        response = session.get(
            'https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/loyalty/history/summary',
            params=params,
            headers=headers,
        )
        payload = response.json().get("payload")
    except Exception:
        payload = None

    if payload is not None:
        totalNights = payload.get("totalNights", -1)
        totalTrips = payload.get("totalTrips", -1)

    return totalNights, totalTrips
    
def getProfile(access_token,accountId,cruiseLineName,session):

    loyaltyNumber = None
    cAndASharedPoints = 0
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'account-id': accountId,
    }

    try:
        response = session.get(f"https://aws-prd.api.rccl.com/en/royal/web/v3/guestAccounts/{accountId}", headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    state = None
    payload = response.json().get("payload")
    contactInfo = payload.get("contactInformation",None)
    if contactInfo is not None:
        address = contactInfo.get("address",None)
        if address is not None:
            residencyCountryCode = address.get("residencyCountryCode",None)
            if residencyCountryCode == "USA" or residencyCountryCode == "CAN":
                state = address.get("state",None)    
    
    loyalty = payload.get("loyaltyInformation")
    cAndANumber = loyalty.get("crownAndAnchorId")
    cAndALevel = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
    cAndAPoints = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints")
    cAndASharedPoints = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints")
   
    if cAndANumber is not None and cAndASharedPoints is not None and cAndASharedPoints > 0:
        print(f"\tC&A: {cAndANumber} {cAndALevel} - {cAndASharedPoints} Shared Points ({cAndAPoints} Individual Points)")
        loyaltyNumber = cAndANumber
        totalNights, totalTrips = getNumberOfNights(access_token,accountId,session,loyaltyNumber)
        if totalNights > 0:
            print(f"\tTotal Trips on Royal: {totalTrips} - Total Nights: {totalNights}")
    
    clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier","Unknown") 
    if clubRoyaleLoyaltyTier != "Unknown":
        clubRoyaleLoyaltyIndividualPoints = loyalty.get("clubRoyaleLoyaltyIndividualPoints",0)
        print(f"\tCasino Royale Tier: {clubRoyaleLoyaltyTier} - {clubRoyaleLoyaltyIndividualPoints} Credits")

    captainsClubId = loyalty.get("captainsClubId")
    if captainsClubId is not None:
        captainsClubLoyaltyTier = loyalty.get("captainsClubLoyaltyTier")
        captainsClubLoyaltyIndividualPoints = loyalty.get("captainsClubLoyaltyIndividualPoints")
        captainsClubLoyaltyRelationshipPoints = loyalty.get("captainsClubLoyaltyRelationshipPoints")
        print(f"\tCaptain's Club Number: {captainsClubId} {captainsClubLoyaltyTier} TIER ({captainsClubLoyaltyRelationshipPoints} Shared Points, {captainsClubLoyaltyIndividualPoints} Individual Points)")
        loyaltyNumber = captainsClubId
        totalNights, totalTrips = getNumberOfNights(access_token,accountId,session,loyaltyNumber)
        if totalNights > 0:
            print(f"\tTotal Trips on Celebrity: {totalTrips} - Total Nights: {totalNights}")

    clubRoyaleLoyaltyTier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
    if clubRoyaleLoyaltyTier != "Unknown":
        celebrityBlueChipLoyaltyIndividualPoints = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints",0)
        print(f"\tBlue Chip Tier: {clubRoyaleLoyaltyTier} - {celebrityBlueChipLoyaltyIndividualPoints} Points")

    # Return the correct loyality number based on the account being used
    if cruiseLineName == "royalcaribbean":
        loyaltyNumberToUse = cAndANumber
    else:
        loyaltyNumberToUse = captainsClubId
        
    # Use Royal shared points to determine if eligible for dp340
    return state, loyaltyNumberToUse, cAndASharedPoints

def getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid,showPromos,discountFlags):

    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'vds-id': accountId,
    }
    
    if cruiseLineName == "royalcaribbean":
        brandCode = "R"
    else:
        brandCode = "C"
        
    params = {
        'brand': brandCode,
        'includeCheckin': 'true',
    }

    try:
        response = session.get(
            f'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/{accountId}',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; skipping this account\n(program exception '{e}')")
        return

    payload = response.json().get("payload")
    if payload is None:
        print("No bookings returned; skipping this account")
        return

    for booking in payload.get("profileBookings"):
        
        reservationId = booking.get("bookingId")
        passengerId = booking.get("passengerId")
        sailDate = booking.get("sailDate")
        numberOfNights = booking.get("numberOfNights")
        shipCode = booking.get("shipCode")
        guests = booking.get("passengersInStateroom")
        packageCode = booking.get("packageCode")
        bookingCurrency = booking.get("bookingCurrency")
        bookingOfficeCountryCode = booking.get("bookingOfficeCountryCode")
        stateroomType = booking.get("stateroomType")
        stateroomNumber = booking.get("stateroomNumber")

        amendToken = booking.get("amendToken")

        stateroomTypeNames = {"I": "INTERIOR", "O": "OUTSIDE", "B": "BALCONY", "D": "DELUXE", "C": "CONCIERGE"}
        stateroomTypeName = stateroomTypeNames.get(stateroomType, "NONE")

        passengerNames = ""
        numberOfPassengers = 0
        numberOfChildren = 0
        numberOfAdults = 0
        haveASenior = False
        
        checkinString = ""
        
        for guest in guests:
            stateroomCategoryCode = guest.get("stateroomCategoryCode")
            stateroomSubtype = booking.get("stateroomSubtype")
            stateroomType = booking.get("stateroomType")
            # Work around for Celebrity Concierge GTY which does not return this info
            # Work around for Royal Interior GTY which does not return this info
            if stateroomCategoryCode is None and stateroomSubtype is None:
                 if displayCruisePrices:
                    print(YELLOW + "Data is missing from API. Code is taking a guess to fixing" + RESET)
                    print(YELLOW + "Add category override in config.yaml if wrong category" + RESET)
                    
                 if stateroomType == "B" and brandCode == "C":
                     stateroomCategoryCode = "XC"
                     stateroomSubtype = "XC"
                 if stateroomType == "I" and brandCode == "R":
                     stateroomCategoryCode = "ZI"
                     stateroomSubtype = "ZI"
                        
            numberOfPassengers = numberOfPassengers + 1
            firstName = guest.get("firstName").capitalize()
            birthDate = guest.get("birthdate")
            
            # Find any seniors to check for cruise prices
            if not haveASenior:
                haveASenior = aboveAgeOnSailDate(birthDate, sailDate, 55)
            
            isAdult = aboveAgeOnSailDate(birthDate, sailDate, 12)
            if isAdult:
                numberOfAdults = numberOfAdults + 1
            else:
                numberOfChildren = numberOfChildren + 1
                
            passengerNames += f"{firstName}, "
            
            # API says Boarding Time is in UTC, but I think in local time
            if guest.get("onlineCheckinStatus") == "COMPLETED":
                possibleArrivalTime = guest.get("arrivalTime",None)
                if possibleArrivalTime is not None:
                    boarding_hour = possibleArrivalTime[9:11]
                    boarding_min = possibleArrivalTime[11:13]
                    if checkinString == "":
                        checkinString = f"{firstName} Boarding Time {boarding_hour}:{boarding_min}"
                    else:
                        checkinString += f", {firstName} Boarding Time {boarding_hour}:{boarding_min}"
            
            if guest.get("onlineCheckinStatus") == "IN_PROGRESS":
                possibleArrivalTime = guest.get("arrivalTime",None)
                if possibleArrivalTime is not None:
                    boarding_hour = possibleArrivalTime[9:11]
                    boarding_min = possibleArrivalTime[11:13]
                    boardingTimeString = f", Boarding Time {boarding_hour}:{boarding_min}"
                else:
                    boardingTimeString = ""
                    
                if checkinString == "":
                    checkinString = f"{firstName} Check in Partially Complete{boardingTimeString}"
                else:
                    checkinString += f", {firstName} Check in Partially Complete{boardingTimeString}"
                    
        passengerNames = passengerNames.rstrip()
        passengerNames = passengerNames[:-1]

        reservationDisplay = f"Reservation #{reservationId}"
        # Use friendly name if available
        if str(reservationId) in reservationFriendlyNames:
            reservationDisplay += f" ({reservationFriendlyNames.get(str(reservationId))})"
        print(f"\n{reservationDisplay}")
        
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        
        # If ship name not found, just use code
        if shipCode not in shipDictionary:
            shipDictionary[shipCode] = shipCode
            
        print(f"{sailDateDisplay} {shipDictionary[shipCode]} Room {stateroomNumber} (In this cabin: {passengerNames})")
        
        # Print Boarding Info or provide check in information
        if checkinString != "":
            print(checkinString)
        else:
            GetCheckinInfo(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,apobj)
        
        result = getDiningAndPrices(session, amendToken, brandCode == "R", bookingOfficeCountryCode)
        #print(result) # comment out if have all-in or refundable fare and create github issue
  
        diningSelection = result.get("diningSelection",[])
        for selection in diningSelection:
            if selection.get("sittingTime","") == "MY TIME":
                print("Dining: My Time Open Sitting")
            else:
                diningString = "Dining: " + selection.get("sittingType","") + " " + selection.get("sittingTime","")
                tableSize = selection.get("tableSize","")
                if tableSize != "" and tableSize != "00":
                    diningString += " Table Size: " + selection.get("tableSize","")
                print(diningString)
        
        paymentString = ""
        gross_totals = None
        prepaidGratsFlag = False
        insuranceFlag = False
        allIncludedFlag = False
        cruisePaidPriceFromAPI = result.get("prices",[])
        for curPrice in cruisePaidPriceFromAPI:
            priceTypeCode = curPrice.get("priceTypeCode","")
            amount = curPrice.get("amount",None)
            if amount == 0:
                continue
                
            if priceTypeCode == "GROSS_TOTALS":
                gross_totals = amount
                #paymentString += f" Total: {amount} "
            if priceTypeCode == "GRATUITIES":
                prepaidGratsFlag = True
                paymentString += f" Including: {amount} Gratituties"
            if priceTypeCode == "TRIP_INSURANCE":
                insuranceFlag = True
                paymentString += f" Including: {amount} Insurance"
            if "ALL_INC" in priceTypeCode or "INCLUDED" in priceTypeCode:
                allIncludedFlag = True
                print("please post an issue with this info")
                print(priceTypeCode)
                paymentString += f" Including: {amount} All Included Drinks/WiFi"    
            #if priceTypeCode == "PAYMENTS_APPLIED": # This may account for TA Take
            #    paymentString += f" You Already Paid: {amount}"
            if priceTypeCode == "BALANCE_DUE":
                paymentString += f" You Still Owe: {amount}"    
        
        paidPriceStruct = {}
        
        if gross_totals is not None:
            # Force Total Shown First. Other order less important
            
            paidPriceStruct['reservation'] = reservationId
            paidPriceStruct['paidPrice'] = gross_totals
            paidPriceStruct['gratuities'] = prepaidGratsFlag
            paidPriceStruct['tripInsurance'] = insuranceFlag
            paidPriceStruct['allInUpgrade'] = allIncludedFlag
            paymentString = f"Cruise Fare - Total {gross_totals}{paymentString}"
            print(paymentString)
 
        
        
        finalPaymentDate = getFinalPaymentDate(numberOfNights, sailDate)
        finalPaymentDateDisplay = finalPaymentDate.strftime(dateDisplayFormat)
        
        if booking.get("balanceDue") is True:
            print(YELLOW + f"Remaining Cruise Payment Balance is {booking.get('balanceDueAmount')} due {finalPaymentDateDisplay}" + RESET)
            
            
        # testing shows OBC is returned for each passenger, but really only for the stateroom
        GetOBC(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj,cruiseLineName,bookingCurrency)
        
        # Show active promotions for this sailing
        if showPromos:
            getAllPromotions(access_token,accountId,session,shipCode,sailDate,bookingCurrency)
        
        # Print Current Prices
        if displayCruisePrices:
    
            [loyaltyNumber, state, senior, military, police, dp340] = discountFlags
            # Override if booking says will have a senior
            if senior == "n" and haveASenior:
                senior = "y"
                
            urlSailDate = f"{sailDate[0:4]}-{sailDate[4:6]}-{sailDate[6:8]}"
            
            if stateroomNumber == "GTY": #GTY Room needs a different URL
                cruisePriceURL = f"https://www.{cruiseLineName}.com/checkout/add-ons?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0b=n&r0r={police}&r0s=n&r0q={military}&r0t={senior}&r0D=y&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0g=BESTRATE&r0h=n&r0C=y"
            else:
                cruisePriceURL = f"https://www.{cruiseLineName}.com/room-selection/room-location?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0b=n&r0r={police}&r0s=n&r0q={military}&r0t={senior}&r0D=y"
                
            if dp340 and cruiseLineName == "royalcaribbean" and numberOfAdults == 1 and numberOfChildren == 0:
                cruisePriceURL += "&r0i=DP340"
            
            if loyaltyNumber is not None:
                cruisePriceURL += f"&r0l={loyaltyNumber}"
            if state is not None:
                cruisePriceURL += f"&r0k={state}"
            
            #print(cruisePriceURL)
            if isinstance(reservationPricePaid,dict) and reservationPricePaid:
                print("Update Config To Use New Paid Price Structure - See Readme")
                if str(reservationId) in reservationPricePaid:
                    paidPrice = reservationPricePaid.get(str(reservationId),None)
                    if paidPrice is not None:
                        if paidPriceStruct:
                            print("Can remove paidPrice in config.yaml if above cruise price is correct")
                            print("Overriding with config.yaml values")
                        paidPriceStruct['paidPrice'] = float(paidPrice) # Override Price
            elif isinstance(reservationPricePaid,list):        
                for reservation in reservationPricePaid:
                    if int(reservationId) == int(reservation.get("reservation")):
                        if paidPriceStruct:
                            print("Can remove reservationPricePaid entry config.yaml if above cruise price is correct")
                            print("Price/Gratuities/Insurance should handled")
                            print("Need to keep (if needed) category Override, military, police, fire, coupon code, or a refundable fare")
                            print("Overriding with config.yaml values")
                        for item in reservation:
                            paidPriceStruct[item] = reservation.get(item)
                
            if stateroomType != "NONE":
                get_cruise_price(cruisePriceURL, session, paidPriceStruct, apobj, True, finalPaymentDate, loyaltyNumber, state)
            else:
                print(YELLOW + "Cannot Check Cruise Price - Use Manual URL Method" + RESET)
                
        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj,cruiseLineName)
        print(" ")
        
        if watchListItems:
            # Process watchlist for each individual passenger instead of per booking
            for guest in guests:
                firstName = guest.get("firstName").capitalize()
                guestPassengerId = guest.get("passengerId")
                # Use the guest's specific room number if available, otherwise fall back to booking room
                guestRoom = guest.get("stateroomNumber") or booking.get("stateroomNumber")
                
                processWatchListForBooking(access_token,accountId,session,reservationId,shipCode,sailDate,
                                         guestPassengerId,firstName,guestRoom,watchListItems,apobj,cruiseLineName)
            print(" ")
          

    
def getOrders(access_token,accountId,session,reservationId,passengerId,ship,startDate,numberOfNights,apobj,cruiseLineName):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'Account-Id': accountId,
    }
    
    if currencyOverride != "":
        currency = currencyOverride
    else:
        currency = "USD"
    
    params = {
        'passengerId': passengerId,
        'reservationId': reservationId,
        'sailingId': ship + startDate,
        'currencyIso': currency,
        'includeMedia': 'false',
    }
    
    try:
        response = session.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/{ship}/orderHistory',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; skipping order history for this booking\n(program exception '{e}')")
        return

    if response.status_code != 200:
        print(f"Error getting order history (returned error code {response.status_code}). Skipping this booking; try again later.")
        return

    payload = response.json().get("payload")
    if payload is None:
        return

    # Check for my orders and orders others booked for me
    for order in (payload.get("myOrders") or []) + (payload.get("ordersOthersHaveBookedForMe") or []):
        orderCode = order.get("orderCode")

        # Match Order Date with Website (assuming Website follows locale)
        date_obj = datetime.strptime(order.get("orderDate"), "%Y-%m-%d")
        orderDate = date_obj.strftime(dateDisplayFormat)
        owner = order.get("owner")
            
        # Only get Valid Orders That Cost Money
        if order.get("orderTotals").get("total") > 0: 
            
            # Get Order Details
            try:
                response = session.get(
                    f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/{ship}/orderHistory/{orderCode}',
                    params=params,
                    headers=headers,
                )
                orderPayload = response.json().get("payload")
            except Exception as e:
                print(f"Can't get details for order {orderCode}; skipping\n(program exception '{e}')")
                continue

            if orderPayload is None:
                continue

            for orderDetail in orderPayload.get("orderHistoryDetailItems"):
                # check for canceled status at item-level
                
                quantity = orderDetail.get("priceDetails").get("quantity")
                order_title = orderDetail.get("productSummary").get("title")
                
                #product = orderDetail.get("productSummary").get("id")
                #product = orderDetail.get("productSummary").get("baseId")
                #product = orderDetail.get("productSummary").get("defaultVariantId")
                # API Change on 6 Feb 2026 - Properly handle variants
                # I do the except just as a precaution
                try:
                    product = orderDetail.get("productSummary").get("baseOptions")[0].get("selected").get("code")
                except Exception:
                    product = orderDetail.get("productSummary").get("defaultVariantId")
                    
                prefix = orderDetail.get("productSummary").get("productTypeCategory").get("id")
              
                salesUnit = orderDetail.get("productSummary").get("salesUnit")
                guests = orderDetail.get("guests")
                
                for guest in guests:
                    
                    if guest.get("orderStatus") == "CANCELLED":
                        continue
                        
                    paidPrice = guest.get("priceDetails").get("subtotal")
                    paidQuantity = guest.get("priceDetails").get("quantity")
                    
                    if paidPrice == 0:
                        continue
                        
                    guestPassengerId = guest.get("id")
                    firstName = guest.get("firstName").capitalize()
                    reservationId = guest.get("reservationId")
                    guestAgeString = guest.get("guestType").lower()
                    
                    # Skip if item checked already
                    newKey = guestPassengerId + reservationId + prefix + product
                    if newKey in foundItems:
                        continue
                    foundItems.add(newKey)
                    
                    # New Per Day Logic From cyntil8 fork
                    if salesUnit in [ 'PER_NIGHT', 'PER_DAY' ]:
                        paidPrice = round(paidPrice / numberOfNights,2)
                
                    if paidQuantity > 0:
                        paidPrice = round(paidPrice / paidQuantity,2)
                        
                    currency = guest.get("priceDetails").get("currency")
                    room = guest.get("stateroomNumber") 
                    #getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest,guestPassengerId,firstName,room,orderCode,orderDate,owner)
                    getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, guestPassengerId,guestAgeString,firstName,room,orderCode,orderDate,owner,False,cruiseLineName, salesUnit, numberOfNights)

def parseProvidedURL(url):
    
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    
    domain = parsed_url.netloc
    isRoyal = "royal" in domain
    
    sailDate = params.get("sailDate")[0]
    currencyCodeList = params.get("selectedCurrencyCode")
    if currencyCodeList is None:
        currencyCode = "USD"
    else:
        currencyCode = currencyCodeList[0]
    
    bookingOfficeCountryCode = params.get("country")[0] 
    shipCode = params.get("shipCode")[0]
    
    cabinClassString = ""
    if params.get("cabinClassType") is not None:
        cabinClassString = params.get("cabinClassType")[0]
    elif params.get("r0d") is not None:
        cabinClassString = params.get("r0d")[0]
    
    stateroomTypeName = params.get("r0d")[0]
    stateroomSubtype = params.get("r0e")[0]
    stateroomCategoryCode = params.get("r0f")[0]
    
    packageCode = params.get("packageCode")[0]
    numberOfAdults = params.get("r0a")[0]
    numberOfChildren = params.get("r0c")[0]
        
    loyaltyNumber = params.get("r0l",[None])[0]
    username = params.get("r0H",[None])[0]
    state = params.get("r0k",[None])[0]
    
    allIncluded = params.get("r0o",["XXX"])[0] != "XXX"
    
    refundable = params.get("r0u",["XXX"])[0] != "XXX"
    travelInsurance = params.get("r0n",["n"])[0] != "n"
    prepaidGrats = params.get("r0m",["n"])[0] != "n"
    
    couponCode = params.get("r0i",[None])[0]
    senior = params.get("r0t",["n"])[0] == "y"
    military = params.get("r0q",["n"])[0] == "y"
    police = params.get("r0r",["n"])[0] == "y"
    fire = params.get("r0s",["n"])[0] == "y"
    
    return isRoyal,sailDate,currencyCode,bookingOfficeCountryCode,shipCode,cabinClassString,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,packageCode,numberOfAdults,numberOfChildren,loyaltyNumber,username,state,refundable,travelInsurance,prepaidGrats,allIncluded,senior,military,police,fire,couponCode
    

def get_cruise_price(url, session, paidPriceStruct, apobj, automaticURL,finalPaymentDate,loyaltyNumber=None,state=None):
    
    #print(url)
    isRoyal,sailDate,currencyCode,bookingOfficeCountryCode,shipCode,cabinClassString,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,packageCode,numberOfAdults,numberOfChildren,loyaltyNumber,username,state,refundable,travelInsurance,prepaidGrats,allIncluded,senior,military,police,fire,couponCode = parseProvidedURL(url)
    roomNumber = None
    
    # Get Overrides if needed
    paidPrice = None
    if paidPriceStruct is not None:
        paidPrice = paidPriceStruct.get("paidPrice",None)
        allIncluded = paidPriceStruct.get("allInUpgrade",allIncluded)
        prepaidGrats = paidPriceStruct.get("gratuities",prepaidGrats)
        travelInsurance = paidPriceStruct.get("tripInsurance",travelInsurance)
        refundable = paidPriceStruct.get("refundable",refundable)
        couponCode = paidPriceStruct.get("couponCode",couponCode)
        stateroomCategoryCode =  paidPriceStruct.get("categoryOverride",stateroomCategoryCode)
        stateroomSubtype =  paidPriceStruct.get("subcategoryOverride",stateroomSubtype)
        senior = paidPriceStruct.get("senior",senior)
        military = paidPriceStruct.get("military",military)
        police = paidPriceStruct.get("police",police)
        fire = paidPriceStruct.get("fire",fire)
        loyaltyNumber = paidPriceStruct.get("loyaltyNumber",loyaltyNumber)
        state = paidPriceStruct.get("state",state)
        
        if allIncluded and isRoyal:
            print("Royal Does Not Have All In Fare")
            print("Removing All In Fare. Check Documentation")
            allIncluded = False
            
    #print(locals())
    results = getRoomPriceViaAPI(session,isRoyal,bookingOfficeCountryCode,packageCode,sailDate,currencyCode,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,roomNumber,loyaltyNumber,state,fire,military,police,senior,couponCode,numberOfAdults,numberOfChildren)

    roomAvailable = results.get("roomAvailable")
    if not roomAvailable and couponCode is not None:
        print(f"Coupon Code {couponCode} May Have Failed - Trying Without")
        couponCode = None
        results = getRoomPriceViaAPI(session,isRoyal,bookingOfficeCountryCode,packageCode,sailDate,currencyCode,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,roomNumber,loyaltyNumber,state,fire,military,police,senior,couponCode,numberOfAdults,numberOfChildren)
        
    roomAvailable = results.get("roomAvailable")
    
    numberOfNights = results.get("sailingNights")
    shipName = shipDictionary.get(shipCode)

    sailDateDisplay = datetime.strptime(sailDate, "%Y-%m-%d").strftime(dateDisplayFormat) 
    
    preString = f"{sailDateDisplay} {shipName} {cabinClassString} {stateroomCategoryCode}"
    
    usedDiscounts = ""
    if loyaltyNumber is not None:
       usedDiscounts += "Loyalty, "
    if state is not None:
       usedDiscounts += "Residency, " 
    if senior == "y":
       usedDiscounts += "Senior, " 
    if police == "y":
       usedDiscounts += "Police, " 
    if military == "y":
       usedDiscounts += "Military, " 
    if couponCode is not None:
       usedDiscounts += f"Coupon {couponCode}, " 
    if usedDiscounts != "":
       preString = preString + " (" + usedDiscounts[:-2] + " Discount)"
    
    # Add discount addon information into string to print
    addons = ""
    
    refundNotFound = False
    
    if roomAvailable:
        
        baseFareString = "baseFare"
        refundFareString = "baseRefundableFare"
        if allIncluded:
            baseFareString = "allIncludedFare"
            refundFareString = "allIncludedRefundableFare" 
            
        fareStruct = results.get(baseFareString)
        if fareStruct is None and baseFareString != "baseFare":
            #print(results)
            print(f"{RED}All Included Fare is Not Available - Reverting to Non-refundable fare{RESET}")
            fareStruct = results.get("baseFare")
        if fareStruct is None:
            print(YELLOW + f"{preString}: No fare pricing returned; cannot compare price" + RESET)
            return
        price = fareStruct.get("fare")
        grats = fareStruct.get("gratuities")
        ins = fareStruct.get("insurance")
        obc = fareStruct.get("obc")

        # Save this for later
        basePrice = price
        baseGrats = grats
        baseIns = ins
        
        desireRefundPrice = False
        if refundable:
           desireRefundPrice = True
           addons += "Refundable Deposit, "
           fareStruct = results.get(refundFareString)
           if fareStruct is not None:
               price = fareStruct.get("fare")
               grats = fareStruct.get("gratuities")
               ins = fareStruct.get("insurance")
               obc = fareStruct.get("obc")
           else:
               refundNotFound = True
           
        if travelInsurance:
           addons += "Travel Protection, "
           price += ins
           basePrice += baseIns
        if prepaidGrats:
           addons += "Prepaid grats, "
           price += grats
           basePrice += baseGrats
        if allIncluded:
           addons += "All Included, "
            
        # Strip last ,
        if addons != "":
            preString = preString + " (" + addons[:-2] + ")"  
        
    if finalPaymentDate is None:
        finalPaymentDate = getFinalPaymentDate(numberOfNights,sailDate.replace('-', ''))
        
    finalPaymentDateDisplay = finalPaymentDate.strftime(dateDisplayFormat)
    pastFinalPaymentDate = date.today() > finalPaymentDate
    
    if not roomAvailable:
        textString = f"{preString} Not For Sale"
        if automaticURL and pastFinalPaymentDate:
            textString += ". Past Final Payment Date of " + finalPaymentDateDisplay
            
        print(YELLOW + textString + RESET)
        
        # If you specified the URL, provide a notification to update the URL
        if not automaticURL and apobj is not None:
            apobj.notify(body=textString, title='Cruise Room Not Available')
        
        # If cruise room not available, print other room prices
        # Only do this for watchlist rooms
        if packageCode and not automaticURL:
            #GetCruisePriceFromAPI(currencyCode, packageCode, sailDate, cabinClassString, numberOfAdults, numberOfChildren)
            # Save API call since I have the rooms already!
            print(f"\tAvailable Rooms (non-discounted price) for {numberOfAdults} Adult and {numberOfChildren} Child on This Sailing Are:")
            for availableRoom in results.get("availableRooms"):
                roomsLeft = availableRoom.get('roomsLeft')
                if roomsLeft is not None and roomsLeft > 0:
                    print(f"\t{availableRoom.get('name')} {availableRoom.get('price')} - Rooms Left {roomsLeft}")
        return
        
    
    if paidPrice is None:
        tempString = GREEN + f"{preString}: Current Price {price} {currencyCode}" + RESET
        print(tempString)
        return
    
    # Find OBC
    obcValue = float(obc)
    obcString = obc
    
    if price < paidPrice: 
        saving = round(paidPrice - price, 2)
        # Notify if should rebook
        if automaticURL and not pastFinalPaymentDate:
            textString = f"Rebook! {preString} New price of {price} {currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} USD OBC"
            textString += f" is lower than {paidPrice}"
            
            if minimumSavingAlert is not None and saving < minimumSavingAlert:
                textString += f" (Saving {saving} < minimumSavingAlert {minimumSavingAlert}; no notification sent)"
                print(YELLOW + textString + RESET)
            else:
                print(RED + textString + RESET)
                if apobj is not None:
                    apobj.notify(body=textString, title='Cruise Price Alert')

        # Don't notify if rebooking not possible
        if  automaticURL and pastFinalPaymentDate:
            textString = f"Past Final Payment Date of {finalPaymentDateDisplay} : {preString} New price of {price} {currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} USD OBC"
            textString += f" is lower than {paidPrice}"
            print(YELLOW + textString + RESET)
            # Do not notify as no need!
            #apobj.notify(body=textString, title='Cruise Price Alert')

        # Always notify if URL is manually provided, assuming you have not booked it yet
        if not automaticURL:
            textString = f"Consider Booking! {preString} New price of {price} {currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} OBC"
            textString +=  f" is lower than watchlist price of {paidPrice}"
            if minimumSavingAlert is not None and saving < minimumSavingAlert:
                textString += f" (Saving {saving} < minimumSavingAlert {minimumSavingAlert}; no notification sent)"
                print(YELLOW + textString + RESET)
            else:
                print(RED + textString + RESET)
                if apobj is not None:
                    apobj.notify(body=textString, title='Cruise Price Alert')
    else:
        tempString = GREEN + f"{preString}: You have best price of {paidPrice} {currencyCode}" + RESET
        extras = ""
        if price > paidPrice:
            extras = f"now {price} {currencyCode}"
        if obcValue > 0:
            extras += f" not including {obcString} OBC"
        if extras:
            tempString += f" ({extras.strip()})"

        if desireRefundPrice and paidPrice > basePrice:
            tempString += f"{YELLOW} Non-Refunable price {basePrice} {currencyCode} is lower than you paid{RESET}"
        elif desireRefundPrice:
            tempString += f" Non-refundable price is {basePrice} {currencyCode}"
            
        print(tempString)
        

def getShipDictionaryWeb():
    headers = {
        'User-Agent': user_agent_web,
        'Accept': 'application/json',
        'appkey': appkey_web,
    }

def getLoyalty(access_token,accountId,session):

    loyaltyNumber = None
    headers = {
        'Access-Token': access_token,
        'AppKey': APPKEY_WEB,
        'account-id': accountId,
    }

    try:
        response = TimeoutSession().get('https://aws-prd.api.rccl.com/en/royal/web/v2/ships', params=params, headers=headers)
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

# Get all available promotions for a sailing
promoCache = {} # Promotions are per-sailing, so fetch each sailing only once per run

def getAllPromotions(access_token, accountId, session, ship, startDate, currency):
    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'vds-id': accountId,
    }

    base_url = 'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog/v2/promotions/list'
    sailingId = ship + startDate

    def fetchPromos(page):
        cacheKey = (sailingId, currency, page)
        if cacheKey in promoCache:
            return promoCache[cacheKey]
        try:
            resp = session.get(base_url, params={'sailingId': sailingId, 'page': page, 'currencyIso': currency}, headers=headers)
            promos = resp.json().get("payload") or [] if resp.status_code == 200 else []
        except Exception as e:
            print(f"Can't get promotions; skipping\n(program exception '{e}')")
            promos = []
        promoCache[cacheKey] = promos
        return promos

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

def get_cruise_price_from_API(
    currency: str,
    package_code: str,
    sail_date: str,
    booking_type: str,
    num_adults: Union[int, str],
    num_children: Union[int, str]
) -> None:
    """
    High-level orchestration manager that pulls live retail cabin pricing directly via the API.

    Acts as the main bridge between raw parsed parameters and structural request assemblies.
    Pre-formats inventory query arrays and submits them through the target pricing API
    endpoint to calculate current base fares, port taxes, and total room options.
    """
    cookies: Dict[str, str] = {
        'currency': currency,
    }

    # Custom headers requested specifically by this GraphQL engine endpoint
    headers: Dict[str, str] = {
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'currency': currency,
    }

    filter_string: str = f"id:{package_code}|adults:{num_adults}|children:{num_children}|startDate:{sail_date}~{sail_date}"

    json_data: Dict[str, Any] = {
        'operationName': 'cruiseSearch_Cruises',
        'variables': {
            'filters': filter_string,
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

    # Route using the centralized execution platform
    # Passing cookies as an additional named parameter via keyword args extraction or direct tracking
    resp = _execute_api_request(
        account_info=None, # Public consumer catalog endpoint, no authentication required
        method="POST",
        url='https://www.royalcaribbean.com/cruises/graph',
        data=json.dumps(json_data),
        headers=headers,
        on_failure="retry" # Prevent a transient pricing lookup failure from killing the tracking pipeline
    )

    if resp is None:
        log("\tUnable to fetch public live API pricing stream at this time.")
        return

    try:
        response_json = resp.json()
        cruises = response_json.get("data", {}).get("cruiseSearch", {}).get("results", {}).get("cruises", [])
    except Exception:
        cruises = []

    if cruises:
        sailings = cruises[0].get("sailings", [])
    else:
        log("         Sailing is sold out")
        return

    for sailing in sailings:
        # Standardize matching criteria format
        current_sail_date: str = sailing.get("sailDate", "")
        if current_sail_date.replace("-", "") != sail_date and current_sail_date != sail_date:
            continue

        prices = sailing.get("stateroomClassPricing", [])
        for price in prices:
            stateroom_class = price.get("stateroomClass", {})
            content_struct = stateroom_class.get("content", {}) if stateroom_class else {}
            cabin_code = content_struct.get("code") if content_struct else None

            if cabin_code == booking_type:
                post_string = " (your current room class) "
            else:
                post_string = ""

    try:
        response = session.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/cart/v1/obc/reservations/{reservationId}',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't get onboard credit info; skipping\n(program exception '{e}')")
        return

            if price_data is None:
                log(f"\t\t{cabin_type} sold out")
            else:
                num_passengers = int(num_adults) + int(num_children)
                total_cabin_cost = float(price_data.get("value", 0.0)) * num_passengers
                log(f"\t\t{total_cabin_cost} {currency}: Cheapest {cabin_type} Price for {num_passengers}" + post_string)


####################################
# End Dead/Obsolete/Unused functions
####################################
'''
#####################################
# Main execution path and Run Control
#####################################
def setup_hybrid_logging(log_file_path: Optional[str] = None) -> None:
    """
    Initializes the tracking environment, functional logging aliases, and file captures.

voyageInfoCache = {} # Same-sailing voyage info is identical, so fetch it only once per run

def GetCheckinInfo(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,apobj):

    headers = {
        'Access-Token': access_token,
        'AppKey': appkey_web,
        'Account-Id': accountId,
    }

    payload = voyageInfoCache.get(shipCode + sailDate)
    if payload is None:
        try:
            response = session.get(
                f'https://aws-prd.api.rccl.com/en/royal/web/v3/ships/voyages/{shipCode}{sailDate}/enriched',
                headers=headers,
            )
        except Exception as e:
            print(f"Can't get check-in info; skipping\n(program exception '{e}')")
            return

        payload = response.json().get("payload")
        if not payload:
            return
        voyageInfoCache[shipCode + sailDate] = payload

        try:
            dt = datetime.fromisoformat(checkWindowOpenStartDateTime)
            local_dt = dt.astimezone().strftime(dateDisplayFormat + " %X %Z")
            print(f"Check In opens on: {local_dt}")
        except Exception:
            pass

def checkIfRoomIsAvailable(session,isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomSubtypeCode,categoryCode,adultCount,childCount):
    
    #Use a More Effecient RSC command. Reduces Data returned by 30%
    #Allows easier json parsing 
    headers = {
        'user-agent': user_agent_web,
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        "Accept": "text/x-component",
        "RSC": "1",
        }

    params = {
        'packageCode': packageId,
        'sailDate': sailDate,
        'country': countryCode,
        'selectedCurrencyCode': currencyCode,
        'shipCode': packageId[0:2],
        'cabinClassType': 'INTERIOR', # Still returns all
        'roomIndex': '0',
        'r0a': adultCount,
        'r0c': childCount,
        'r0b': 'n',
        'r0r': 'n',
        'r0s': 'n',
        'r0q': 'n',
        'r0t': 'n',
        'r0d': 'INTERIOR', # Still returns all
        'r0D': 'y',
        'rgVisited': 'true',
        'r0C': 'y',
    }
    
    if isRoyal:
        apiURL = 'https://www.royalcaribbean.com/room-selection/type-and-subtype'
    else:
        apiURL = 'https://www.celebritycruises.com/room-selection/type-and-subtype'

    response = session.get(
        apiURL,
        params=params,
        headers=headers,
    )

    availableRooms = []
    
    # Just extract json out of data
    rooms = _extract_json_array(response.text, "rooms")
    
    # If no rooms, quit
    if rooms is None:
        return False, availableRooms
        
    stateroomTypes = rooms[0].get("options").get("stateroomTypes")
       
    for stateroomType in stateroomTypes:
        stateroomSubtypes = stateroomType.get("stateroomSubtypes")
        for stateroomSubtype in stateroomSubtypes:
            cur_subTypeCode = stateroomSubtype.get("code")
            cur_categoryCode = stateroomSubtype.get("categoryCode")
            #print("Desired: " + stateroomSubtypeCode + " " + categoryCode)
            #print("Cur:     " + cur_subTypeCode + " " + categoryCode)
            #print(f"{stateroomSubtype.get('name')} {cur_categoryCode} {cur_subTypeCode}")
            # Gate on the subtype code alone (not categoryCode). Royal's room-selection
            # page now returns a single lead-in row per subtype: its "code" still equals the
            # booking's stateroomSubtype, but its "categoryCode" is only the subtype's lead-in
            # category, no longer the exhaustive per-category list. So it stops equalling the
            # booked categoryCode for cabins booked above the lead-in (e.g. a 2D booking when
            # the endpoint now lists 4D as the D lead-in), which made those bookings read
            # "Not For Sale". The exact price is unaffected - the checkout POST below still
            # uses the specific booked categoryCode.
            if cur_subTypeCode == stateroomSubtypeCode:
                # Need to check if rooms are left.
                # This is not always provided, so cannot use it to check inventory
                # roomsLeft = stateroomSubtype.get("roomsLeft",0)
                # However, sometimes it is not filled and it means it is not available
                # We just need to deal with that
                
                return True, []
                
            # Here we add available rooms. I will only use it if sold out, so ok if exits if room found
            price = stateroomSubtype.get("pricing").get("invoice").get("total")
            roomsLeft = stateroomSubtype.get("roomsLeft")
            availableRooms.append({"name":stateroomSubtype.get('name') + " " + cur_categoryCode + " " + cur_subTypeCode,"price":price,"roomsLeft":roomsLeft})
                
    
    return False, availableRooms

def getRoomPriceViaAPI(session,isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomTypeCode,stateroomSubtypeCode,categoryCode,roomNumber,loyaltyNumber,stateCode,fireFighter,military,police,senior,couponCode,adultCount,childCount):

    roomAvailable, availableRooms = checkIfRoomIsAvailable(session,isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomSubtypeCode,categoryCode,adultCount,childCount)
    
    results = {}
    sailingNights = 0
    results['sailingNights'] = sailingNights
    results['roomAvailable'] = roomAvailable
    
    # If Room is not Available, do not check the price
    if roomAvailable is False:
        # Print Error Message and set room available to false
        #print("Room Not Found - Not Checking Price")
        results['roomAvailable'] = False
        results['availableRooms'] = availableRooms
        return results
    
    
    headers = {
    'user-agent': user_agent_web,
    'appkey': appkey_web,
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    }

    json_data = {
        'countryCode': countryCode,
        'packageId': packageId,
        'sailDate': sailDate,
        'currencyCode': currencyCode,
        'language': 'en',
        'rooms': [
            {
                'stateroomTypeCode': stateroomTypeCode,
                'stateroomSubtypeCode': stateroomSubtypeCode,
                'categoryCode': categoryCode,
                'fareCode': 'BESTRATE',
                'accessible': False,
                #'roomNumber': roomNumber,
                #'couponCode': couponCode,
                'qualifiers': {
                    #'loyaltyNumber': loyaltyNumber,
                    #'stateCode': stateCode,
                    'fireFighter': fireFighter,
                    'military': military,
                    'police': police,
                    'senior': senior,
                },
                'occupancy': {
                    'adultCount': adultCount,
                    'childCount': childCount,
                },
            },
        ],
    }
    
    if couponCode is not None:
        json_data['rooms'][0]['couponCode'] = couponCode
    if roomNumber is not None:
        json_data['rooms'][0]['roomNumber'] = roomNumber
    if stateCode is not None:
        json_data['rooms'][0]['qualifiers']['stateCode'] = stateCode
    if loyaltyNumber is not None:
        json_data['rooms'][0]['qualifiers']['loyaltyNumber'] = loyaltyNumber
    
    if isRoyal:
        apiURL = 'https://www.royalcaribbean.com/checkout/api/v1/rooms/checkout'
    else:
        apiURL = 'https://www.celebritycruises.com/checkout/api/v1/rooms/checkout'

    try:
        response = session.post(apiURL,
            headers=headers,
            json=json_data,
        )
        responseData = response.json()
        rooms = responseData.get("rooms")
    except Exception:
        rooms = None

    if rooms is None:
        # Print Error Message and set room available to false
        print("Room Price Not Found")
        results['roomAvailable'] = False
        results['availableRooms'] = availableRooms
        return results

    room = rooms[0]
    sailingNights = responseData.get("sailing").get("itinerary").get("sailingNights")
    results['sailingNights'] = sailingNights

    for fareKey in ("baseFare", "baseRefundableFare", "allIncludedFare", "allIncludedRefundableFare"):
        fare = room.get(fareKey)
        if fare is not None:
            results[fareKey] = {
                'fare': fare.get("pricing").get("amount"),
                'gratuities': fare.get("gratuities"),
                'insurance': fare.get("insurance"),
                'obc': fare.get("pricing").get("invoice").get("onboardCredits", 0),
            }

    results['availableRooms'] = availableRooms
    return results


if __name__ == "__main__":
    config_path = get_config_path()

    try:
        # Load everything once. Logging, Apprise, and YAML values are now armed.
        config = load_config_objects(config_path)

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
                sys.exit(1)
        else:
            print("Exiting. Please create a valid config.yaml file manually.")
            sys.exit(1)

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
                config.apobj.notify(body=body, title='Cruise Price Script Error')

        sys.exit(1)
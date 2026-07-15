from __future__ import annotations
import argparse
import json
import locale
import logging
import os
import platform
import re
import sys

# curl_cffi impersonates a real browser's TLS fingerprint so the cruise line's
# edge servers do not reject some IPs/systems as bots with 403 Access Denied
# (see jdeath/CheckRoyalCaribbeanPrice issue #64, where the Browse script was
# confirmed affected). Fall back to plain requests where it is not installed
# (e.g. iOS), which works fine for most people.
try:
    from curl_cffi import requests
    IMPERSONATE_ARGS = {"impersonate": "chrome"}
except ImportError:
    import requests
    IMPERSONATE_ARGS = {}

from datetime import datetime, date
from typing import Any, Dict, List, Optional, Union
from unicodedata import combining, normalize

##################################
# Global Constants & Variables
##################################
# Immutable configuration settings
DATE_DISPLAY_FORMAT = "%x"

APPKEY_MOBILE_GRAPH = '5pWJwSTvu30Dkw5GHLVQ5PsmoKRE1arh'
USER_AGENT_MOBILE_GRAPH = 'okhttp/4.12.0'

APPKEY_WEB = 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm'
USER_AGENT_WEB = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0'

# ANSI color codes
# Original values
RED = '\033[91m'
GREEN = '\033[1;32m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m' # Resets color to default

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

# Environmental overrides for terminals struggling with Unicode glyphs (e.g., MobaXterm)
PROBLEM_ENVS = ["MOBAEXTRACTONTHEFLY", "MOBANOACL"]

has_terminal_issues = False;
log = None
log_warn = None
log_err = None


##################################
# Classes
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


##################################
# Helper functions
##################################
def _execute_api_request(
    method: str,
    url: str,
    params: Optional[dict] = None,
    data: Optional[Union[str, dict]] = None,
    json_data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 15,
    exit_on_fail: bool = True
) -> Optional[requests.Response]:
    """
    Unified API execution engine for anonymous browser network interactions.

    Centralizes connection tracking parameters, developer keys, connect timeouts,
    and handles graceful handling or program exit states during connection issues.
    """
    # Start with any caller-specified override headers, or an empty base
    final_headers = headers.copy() if headers else {}

    # Always include the baseline developer web key if not overridden
    if "appkey" not in final_headers:
        final_headers["appkey"] = APPKEY_WEB

    # Fire the request dynamically using the ephemeral requests pipeline
    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            params=params,
            data=data,
            json=json_data,
            headers=final_headers,
            timeout=timeout,
            **IMPERSONATE_ARGS
        )
        response.raise_for_status()
        return response

    except Exception as e:
        error_msg = f"Can't contact cruise line servers; please try again later\n(program exception '{e}')"
        if exit_on_fail:
            log(error_msg)
            sys.exit(1)
        else:
            logging.warning(f"Non-critical API interaction skipped (exception: {e})")
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


def days_between(sail_date: str, activity_date: str) -> str:
    d0 = date(int(sail_date[0:4]), int(sail_date[4:6]), int(sail_date[6:8]))
    d1 = date(int(activity_date[0:4]), int(activity_date[4:6]), int(activity_date[6:8]))
    delta = d1 - d0
    return str(delta.days + 1)


def get_system_currency() -> str:
    # Set the locale to the system's default
    # An empty string "" makes setlocale search the appropriate environment variables.
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error as e:
        log(f"Warning: Could not set locale. Using default 'C' locale. Error: {e}")
        # Fallback to 'C' locale or handle as needed
        locale.setlocale(locale.LC_ALL, 'C')

    # Get a dictionary of the local formatting conventions
    conventions = locale.localeconv()

    # Extract the international currency symbol (e.g., "USD ")
    international_symbol = conventions.get('int_curr_symbol', '').strip()
    return international_symbol


def sanitize_string(string_to_clean: str) -> str:
    # Some unicode characters don't properly print to ASCII terminals
    # Convert unicode non-printable punctuation characters
    cleaned_string = string_to_clean.lstrip()
    cleaned_string = cleaned_string.replace('\u00AE', '(R)')    # replace registered trademark symbol with (R)
    cleaned_string = cleaned_string.replace('\u200B', ' ')      # replace zero-width space with space
    cleaned_string = cleaned_string.replace('\u2013', '-')      # replace en dash with -
    cleaned_string = cleaned_string.replace('\u2014', '-')      # replace em dash with -
    cleaned_string = cleaned_string.replace('\u2018', '`')      # replace left single quotation with `
    cleaned_string = cleaned_string.replace('\u2019', '\'')     # replace right single quotation with '
    cleaned_string = cleaned_string.replace('\u201C', '"')      # replace left double quotation with "
    cleaned_string = cleaned_string.replace('\u201D', '"')      # replace right double quotation with "
    cleaned_string = cleaned_string.replace('\u2120', '(SM)')   # replace service mark symbol with (SM)

    if has_terminal_issues:
        cleaned_string = cleaned_string.replace('\u2191', '^')  # replace ↑ with ^
        cleaned_string = cleaned_string.replace('\u2193', 'v')  # replace ↓ with v

    # Convert unicode non-printable accented characters
    cleaned_string = normalize('NFKD', cleaned_string)
    return ''.join([c for c in cleaned_string if not combining(c)])


############################################################
# Initial Discovery & Routing (Web Gateway Domain) functions
############################################################
def get_ships_web() -> List[Dict[str, str]]:
    """
    Fetches the complete active commercial fleet index from the web API.

    Queries the public endpoints to map two-character ship identifier tokens
    to their full user-facing marketing titles, sorting results alphabetically.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing 'code' and 'name' keys.
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    params = {
        'sort': 'name',
    }

    response = _execute_api_request(
        method="GET",
        url="https://aws-prd.api.rccl.com/en/royal/web/v2/ships",
        params=params,
        headers=headers
    )

    if not response:
        return []

    ship_names = []
    payload = response.json().get("payload", {})
    ships = payload.get("ships", []) if payload else []

    for ship in ships:
        ship_code = ship.get("shipCode")
        name = ship.get("name")
        ship_names.append({'code': ship_code, 'name': name})

        # HARDCODING GUARD: The server-side staging environment utilizes 'HE' for
        # preliminary vessel mapping profiles before they hit the global catalog.
        if ship_code == "HM":
            # Force Hero until it is added to the API
            ship_names.append({'code': 'HE', 'name': 'Hero of the Seas'})

    return ship_names


def get_sailings_web(ship_code: str) -> List[Dict[str, Any]]:
    """
    Retrieves all scheduled future voyages and operational itineraries for a given ship.

    Extracts core deployment data attributes including length profiles, system date markers,
    and public destination descriptions needed to present choice indices to the operator.

    Args:
        ship_code (str): The unique two-character cruise ship identification code (e.g., 'AL').

    Returns:
        List[Dict[str, Any]]: Collection of voyage metadata dicts containing dates,
                              display variations, durations, and itinerary definitions.
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    response = _execute_api_request(
        method="GET",
        url=f"https://aws-prd.api.rccl.com/en/royal/web/v3/ships/{ship_code}/voyages",
        headers=headers
    )

    if not response:
        return []

    try:
        resp_json = response.json()
    except Exception:
        return []

    payload = resp_json.get("payload") if resp_json else None
    voyages = payload.get("voyages", []) if payload else []

    if not isinstance(voyages, list):
        voyages = []

    sailings = []
    for voyage in voyages:
        if not voyage or not isinstance(voyage, dict):
            continue

        sail_date = voyage.get("sailDate")
        duration = voyage.get("duration")
        voyage_code = voyage.get("voyageCode")
        voyage_description = voyage.get("voyageDescription")

        sail_date_display = datetime.strptime(sail_date, "%Y%m%d").strftime(DATE_DISPLAY_FORMAT)

        sailings.append({
            'date': sail_date,
            'displayDate': sail_date_display,
            'description': voyage_description,
            'duration': duration,
            'voyageCode': voyage_code
        })

    return sailings


def get_sailing_details_web(ship_code: str, sail_date: str) -> Dict[int, str]:
    """
    Queries the web API itinerary engine to resolve daily ports of call and track operations.

    Resolves timeline sequences for the target sailing, maps daily ports, formats
    operational arrival/departure flags, and displays gangway direction mechanics.

    Args:
        ship_code (str): The unique two-character cruise ship identification code.
        sail_date (str): Cruise departure date tracking string in 'YYYYMMDD' format.

    Returns:
        Dict[int, str]: A lookup mapping of relative cruise days (1-indexed integers)
                        to their clean destination names (e.g., {1: 'Port Canaveral'}).
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    response = _execute_api_request(
        method="GET",
        url=f"https://aws-prd.api.rccl.com/en/royal/web/v3/ships/{ship_code}/sailDate/{sail_date}",
        headers=headers
    )

    ports = {}
    if not response:
        return ports

    try:
        resp_json = response.json()
    except Exception:
        return ports

    # EXPLICIT SAFE PARSING:
    payload = resp_json.get("payload") if resp_json else None
    sailing_info = payload.get("sailingInfo") if payload else None

    # Handle the API variance where sailingInfo can be a list or a dictionary container
    if isinstance(sailing_info, list) and sailing_info:
        sailing_info = sailing_info[0]

    itinerary = sailing_info.get("itinerary") if isinstance(sailing_info, dict) else None
    port_info = itinerary.get("events", []) if isinstance(itinerary, dict) else []

    if not isinstance(port_info, list):
        return ports

    for port in port_info:
        if not port or not isinstance(port, dict):
            continue

        port_data = port.get("port", {}) if port else {}
        title = sanitize_string(port_data.get("portName", ""))
        arrival_date_time = port_data.get("arrivalDateTime")
        departure_date_time = port_data.get("departureDateTime")

        port_type = port_data.get("portType", "Unknown")
        day = port.get("day", "Unknown")

        # Save port names for later
        ports[int(day)] = title

        # Sometimes fields are not filled, so we skip (like international date line changes)
        if arrival_date_time is None:
            log(f"Day {day}: {title}")
            continue

        arrival_date_display = datetime.strptime(arrival_date_time[0:8], "%Y%m%d").strftime(DATE_DISPLAY_FORMAT)
        print_string = f"Day {day} ({arrival_date_display}): {title}"

        if port_type == "EMBARK" and departure_date_time:
            print_string += f" ↑ {departure_date_time[9:13]}"
        elif port_type == "DEBARK":
            print_string += f" ↓ {arrival_date_time[9:13]}"
        elif title != "Cruising":
            if arrival_date_time and departure_date_time:
                print_string += f" ↓ {arrival_date_time[9:13]} ↑ {departure_date_time[9:13]}"

        if port_type == "TENDERED":
            print_string += f" (Tendered)"

        port_string = sanitize_string(print_string)
        log(port_string)

    if has_terminal_issues:
        log("(^ means gangway up; v means gangway down)")
    else:
        log("(↑ means gangway up; ↓ means gangway down)")

    return ports


def get_web_categories(ship: str, saildate: str) -> Dict[str, str]:
    """
    Fetches the available commerce categories (e.g., dining, shorex, beverage)
    for a specific ship and sail date from the web GraphQL endpoint.

    Args:
        ship (str): The unique two-character cruise ship code (e.g., 'OA').
        saildate (str): The sailing start date formatted as 'YYYYMMDD'.

    Returns:
        Dict[str, str]: A dictionary mapping category IDs to their user-facing
                        display names (e.g., {'shorex': 'Shore Excursions'}).
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    # DESIGN NOTE: Breaking the query into a multi-line string exposes the expected GraphQL schema.
    # We look for a successful 'CategoryResultSuccess' type inline and pull the category list.
    graphql_query = """
    query WebCategories($shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $regionCode: String) {
        categories(shipCode: $shipCode, sailDate: $sailDate, regionCode: $regionCode, filter: {limitCategoriesWithProducts: true}) {
            ... on CategoryResultSuccess {
                categories {
                    id
                    name
                }
            }
        }
    }
    """
    json_data = {
        'operationName': 'WebCategories',
        'variables': {
            'sailDate': saildate,
            'shipCode': ship,
        },
        'query': graphql_query,
    }

    response = _execute_api_request(
        method="POST",
        url="https://aws-prd.api.rccl.com/en/royal/web/graphql",
        headers=headers,
        json_data=json_data
    )

    product_map = {}
    if not response:
        return product_map

    data_container = response.json().get("data")
    categories_container = data_container.get("categories") if data_container else None
    if categories_container is None:
        log("No Items for Sale")
        return product_map

    categories_list = categories_container.get("categories")
    if categories_list is None:
        log("No Items for Sale")
        return product_map

    for category in categories_list:
        product_map[category.get("id")] = category.get("name")

    return product_map


############################################################
# Catalog Processing & Pagination (Commerce Domain) functions
############################################################
def get_products_graph_all_pages(
    ship_code: str,
    sail_date: str,
    duration: int,
    currency: str,
    sortkey: str,
    sortorder: str,
    key: str,
    day_number: str = "all"
) -> List[Dict[str, Any]]:
    """
    Queries the web GraphQL catalog iteratively to retrieve all commercial products
    for a given category, automatically handling pagination boundaries.

    Args:
        ship_code (str): The cruise ship identification code.
        sail_date (str): Sailing date string ('YYYYMMDD').
        duration (int): Total night duration of the cruise itinerary.
        currency (str): Three-letter ISO currency filter string (e.g., 'USD').
        sortkey (str): Sorting metric criteria ('price', 'alpha', or 'default').
        sortorder (str): Sequential sort direction order ('asc' or 'desc').
        key (str): The target category identifier string (e.g., 'beverage').
        day_number (str, optional): Filters to a specific calendar itinerary day. Defaults to "all".

    Returns:
        List[Dict[str, Any]]: Consolidated collection of un-paginated product dictionaries.
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    # Map application-friendly sort choices to the strict backend GraphQL Enum types
    if sortkey == "price":
        api_sort_key = "PRICE"
    elif sortkey == "alpha":
        api_sort_key = "TITLE"
    else:
        api_sort_key = "RANK"

    api_sort_order = "DESCENDING" if sortorder == "desc" else "ASCENDING"

    # DESIGN NOTE: Spread out for clear readability. Captures essential item structures
    # including names, descriptions, dynamic prices, and alternate variant dimensions.
    graphql_query = """
    query WebProductsByCategory(
        $category: String!,
        $passengerId: String,
        $shipCode: ShipCodeScalar!,
        $sailDate: LocalDateScalar!,
        $reservationId: String,
        $pageSize: Long,
        $currentPage: Long,
        $sorting: Sorting,
        $filter: FilterInput,
        $currencyCode: String!
    ) {
        products(
            category: $category,
            guestTypes: [ADULT],
            passengerId: $passengerId,
            shipCode: $shipCode,
            sailDate: $sailDate,
            reservationId: $reservationId,
            pageSize: $pageSize,
            currentPage: $currentPage,
            sorting: $sorting,
            filter: $filter,
            currencyIso: $currencyCode
        ) {
            ... on CommerceProductResultSuccess {
                commerceProducts {
                    id
                    title
                    variantOptions {
                        code
                        name
                    }
                    price {
                        currency
                        promotionalPrice
                        shipboardPrice
                        formattedPromotionalPrice
                        formattedBasePrice
                        formattedDailyPrice
                        formattedPromoDailyPrice
                        salesUnit {
                            code
                            name
                            label
                        }
                    }
                }
            }
        }
    }
    """
    json_data = {
        'operationName': 'WebProductsByCategory',
        'variables': {
            'category': key,
            'passengerId': '',
            'shipCode': ship_code,
            'sailDate': sail_date,
            'reservationId': '000000',
            'pageSize': 12,  # WARNING: Hard API ceiling. Setting this above 20 triggers total empty payloads.
            'sorting': {
                'sortKey': api_sort_key,
                'sortKeyOrder': api_sort_order,
            },
            'filter': {
                'includeVariantProducts': False,
            },
            'currencyCode': currency,
            'includeFilterInfo': False,
            'includeIfABexperience': False,
        },
        'query': graphql_query,
    }

    if day_number != "all":
        json_data['variables']['filter']['dayNumber'] = [int(day_number)]

    products = []

    # ALGORITHM: Bounded Infinite Loop pagination mitigation strategy.
    # Loops up to 100 pages, adjusting the 'currentPage' index variable tracking increment.
    # Breaks out immediately when an API response is empty, indicating we hit the terminal page.
    for page in range(100):
        json_data['variables']['currentPage'] = page

        response = _execute_api_request(
            method="POST",
            url="https://aws-prd.api.rccl.com/en/royal/web/graphql",
            headers=headers,
            json_data=json_data,
            exit_on_fail=False
        )

        if not response:
            break

        data_container = response.json().get("data") if response else None
        products_container = data_container.get("products") if data_container else None
        if products_container is None:
            break

        temp_products = products_container.get("commerceProducts")

        # None or an empty list both mean pagination is completely exhausted;
        # continuing instead of breaking would fire the remaining ~99 requests
        if not temp_products:
            break

        products.extend(temp_products)

    return products


def print_and_sort_products(
    products: Optional[List[Dict[str, Any]]],
    sort_key: str,
    sort_order: str,
    currency: str,
    key: str,
    show_watchlist_codes: bool
) -> None:
    """
    Normalizes pricing payloads, processes sorting logic, and prints items to the stream.

    Cleans structural variants, evaluates dynamic pricing conditions, transforms layout
    shorthands, applies localized exchange markers, and exposes underlying database
    identifiers when watchlist inspection handles are active.

    Args:
        products (Optional[List[Dict[str, Any]]]): Raw lists of compiled product data blocks.
        sort_key (str): Chosen sorting metric ('price', 'alpha', or 'default').
        sort_order (str): Sequential ordering trajectory rule ('asc' or 'desc').
        currency (str): Three-character target currency symbol string.
        key (str): Active category identity parent filter string.
        show_watchlist_codes (bool): Toggles inclusion of technical schema IDs in the stream.
    """
    if products is None:
        return

    # Alpha Sort via API does not always work, so do a force sort with @AESternberg code
    # Price Sorting works fine from the API
    # Ultimate dining package always starts with a " ", so this removes any leading spaces
    if sort_key == 'alpha':
        if sort_order == 'desc':
            sorted_products = sorted(products, key=lambda product: product.get('title', '').lstrip(), reverse=True)
        else:
            sorted_products = sorted(products, key=lambda product: product.get('title', '').lstrip())
    else:
        sorted_products = products

    for product in sorted_products:
        if not product or not isinstance(product, dict):
            continue

        current_id = product.get("id")
        price_list = product.get("price")

        # EXPLICIT SAFE PARSING:
        if not price_list or not isinstance(price_list, list) or len(price_list) == 0:
            continue

        price_struct = price_list[0]
        if not price_struct or not isinstance(price_struct, dict):
            continue

        title = sanitize_string(product.get("title", ""))
        price = price_struct.get("formattedPromotionalPrice")
        if price is None:
            price = price_struct.get("formattedBasePrice")

        if price is None:
            price = price_struct.get("shipboardPrice")

        if price is None:
            continue

        # Remove any currency codes/$/Pound Sign and spaces
        price = re.sub(r'[^0-9\.]', '', str(price))
        price = price.replace(" ", "")
        if price == "0" or price == "0.0" or price == "":
            continue

        sales_unit = price_struct.get("salesUnit") or {}
        unit = sales_unit.get("name")

        print_string = f"\t{title} {GREEN}{price} {currency}{RESET}"

        if unit == "Per Night":
            print_string += " per night"

        if unit == "Per Day":
            print_string += " per day"

        if show_watchlist_codes:
            print_string += f" (prefix: {key}, product: {current_id})"

        log(print_string)

        # Skip first variant, as it is the default
        variant_options = product.get("variantOptions", [])
        for variant_option in variant_options[1:]:
            if not variant_option:
                continue

            variant_code = variant_option.get("code")
            variant_name = variant_option.get("name", "")
            if "Bottles" in variant_name:
                variant_name += " (larger option)"

            print_string = f"\t{variant_name} Price Not Available"
            if show_watchlist_codes:
                print_string += f" (prefix: {key}, product: {variant_code})"

            log(print_string)


def print_all_products(
    ship_code: str,
    sail_date: str,
    duration: int,
    currency: str,
    sort_key: str,
    sort_order: str,
    show_watchlist_codes: bool,
    ports: Dict[int, str]
) -> None:
    """
    Orchestrates complete store category extraction loops across the itinerary.

    Resolves available categories, isolates shore excursion tracks for iterative
    day-by-day sub-queries, and maps general catalog products to pagination handlers.

    Args:
        ship_code (str): The unique two-character cruise ship identification code.
        sail_date (str): Cruise departure date tracking string in 'YYYYMMDD' format.
        duration (int): Total onboard night counts defining sailing length boundaries.
        currency (str): Three-character target currency symbol string.
        sort_key (str): Chosen sorting metric string selection parameter.
        sort_order (str): Sequential sorting direction string selection parameter.
        show_watchlist_codes (bool): Controls technical ID metadata stream printing.
        ports (Dict[int, str]): Daily port routing map used to link events to shore excursions.
    """
    product_map = get_web_categories(ship_code, sail_date)

    for key, category_name in product_map.items():
        log(f"{BLUE}{category_name}{RESET}")

        # Display Shore Excursions by day sequentially
        if key == "shorex":
            for day in range(1, duration + 2):
                products = get_products_graph_all_pages(
                    ship_code, sail_date, duration, currency, sort_key, sort_order, key, str(day)
                )
                if products:
                    port_title = ports.get(day, "")
                    log(f"\t{BLUE}Day {day}: {port_title}{RESET}")
                    print_and_sort_products(products, sort_key, sort_order, currency, key, show_watchlist_codes)
        else:
            products = get_products_graph_all_pages(
                ship_code, sail_date, duration, currency, sort_key, sort_order, key, "all"
            )
            print_and_sort_products(products, sort_key, sort_order, currency, key, show_watchlist_codes)


def get_all_activities_web(ship_code: str, sail_date: str) -> List[Dict[str, Any]]:
    """
    Queries the web API catalog via pagination batches to locate all non-revenue cruise schedule activities.

    Loops through sequential offsets to filter out paid amenities, pinpointing free,
    schedulable shipboard events, times, locations, and cruise timeline days.

    Args:
        ship_code (str): The unique two-character cruise ship identification code.
        sail_date (str): Cruise departure date tracking string in 'YYYYMMDD' format.

    Returns:
        List[Dict[str, Any]]: Flat list of parsed activity data points for presentation processing.
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'appkey': APPKEY_WEB,
    }

    products = []
    limit = 200

    for offset in range(0, 10000, limit):
        params = {
            'sailingID': ship_code + sail_date,
            'limit': str(limit),
            'offset': str(offset),
        }

        # If it's the very first page, we run it through the engine normally.
        # If the first page throws a genuine 404 error (like a far future cruise),
        # we accept the engine's warning because it's a genuine empty response boundary.
        # However, to prevent near-term cruises from printing warnings on subsequent empty pages,
        # we track the actual batch size returned.
        response = _execute_api_request(
            method="GET",
            url="https://aws-prd.api.rccl.com/en/royal/web/v3/products",
            params=params,
            headers=headers,
            exit_on_fail=False
        )

        if not response:
            break

        try:
            json_payload = response.json()
        except Exception:
            break

        payload_data = json_payload.get("payload") if json_payload else None
        if payload_data is None:
            break

        temp_products = payload_data.get("products", [])
        if not temp_products:
            break

        for product in temp_products:
            if not product:
                continue

            product_type_container = product.get("productType")
            product_type = product_type_container.get("productType") if product_type_container else None

            if product_type != "NON_REVENUE_SCHEDULABLE":
                continue

            product_title = product.get("productTitle")

            location_container = product.get("productLocation")
            location = location_container.get("locationTitle", "") if location_container else ""
            if location is None:
                location = ""

            offering = product.get("offering", [])
            for offer in offering:
                if offer is not None:
                    offering_date = offer.get("offeringDate")
                    offering_time = offer.get("offeringTime")
                    day = days_between(sail_date, offering_date)

                    products.append({
                        'productTitle': product_title,
                        'location': location,
                        'offeringDate': offering_date,
                        'offeringTime': offering_time,
                        'day': day
                    })

        # CRITICAL FIX: If the number of products returned in this batch is less than
        # our request limit (200), it means we have successfully exhausted the list
        # and reached the final page. Breaking here avoids firing the next trailing
        # request which would otherwise return a 404 and pollute the screen logs.
        if len(temp_products) < limit:
            break

    return products


def print_all_activities(activities: List[Dict[str, Any]], sort_order: str) -> None:
    """
    Sorts and visually structures non-revenue shipboard entertainment entries.

    Accepts raw activity records, enforces custom multi-column lambda sorting properties,
    and handles date display presentation layouts.

    Args:
        activities (List[Dict[str, Any]]): Collected activity arrays containing time/location tracking keys.
        sort_order (str): Token dictating sorting strategies ('date', 'alpha', or 'default').
    """
    if not activities:
        log("No Activities Scheduled")
        return

    if sort_order == 'date':
        sorted_activities = sorted(
            activities,
            key=lambda activity: (activity.get('offeringDate', ''), activity.get('offeringTime', ''))
        )
    elif sort_order == 'alpha':
        # This is likely unnecessary, but here just in case RCCL default is no longer alphabetical order
        sorted_activities = sorted(activities, key=lambda activity: activity.get('productTitle', ''))
    else:
        sorted_activities = activities

    for activity in sorted_activities:
        if not activity:
            continue

        product_title = sanitize_string(activity.get("productTitle", ""))
        location = sanitize_string(activity.get("location", ""))

        raw_date = activity.get("offeringDate")
        if raw_date:
            offering_date = datetime.strptime(raw_date, "%Y%m%d").strftime("%B %d, %Y")
        else:
            offering_date = "Unknown Date"

        offering_time = activity.get("offeringTime", "")
        day = activity.get("day", "")

        log(f"{product_title}\t {location} {GREEN}{offering_date} (Day {day}) {offering_time}{RESET}")


def get_cruise_price_from_API(
    currency: str,
    package_code: str,
    sail_date: str,
    num_adults: int,
    num_children: int
) -> None:
    """
    Fetches and displays the cheapest public stateroom cabin options
    for a specific sailing package via the guest-facing cruise finder graph API.

    Args:
        currency (str): Three-letter string designation targeting output exchange metrics.
        package_code (str): Consolidated ship and voyage profile string (e.g., 'AL20260510').
        sail_date (str): Target departure identifier date string ('YYYYMMDD').
        num_adults (int): Total number of adult passenger units included.
        num_children (int): Total number of minor passenger units included.
    """
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'currency': currency,
    }

    # Format incoming 'YYYYMMDD' string seamlessly to standard 'YYYY-MM-DD' query compliance
    formatted_sail_date = f"{sail_date[0:4]}-{sail_date[4:6]}-{sail_date[6:8]}"

    # ALGORITHM: Strict query filter construction required by the Cruise Search backend engine.
    filter_string = f"id:{package_code}|adults:{num_adults}|children:{num_children}|startDate:{formatted_sail_date}~{formatted_sail_date}"

    graphql_query = """
    query cruiseSearch_Cruises($filters: String) {
        cruiseSearch(filters: $filters) {
            results {
                cruises {
                    id
                    sailings {
                        sailDate
                        stateroomClassPricing {
                            price {
                                value
                                currency {
                                    code
                                }
                            }
                            stateroomClass {
                                id
                                name
                                content {
                                    code
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """
    json_data = {
        'operationName': 'cruiseSearch_Cruises',
        'variables': {
            'filters': filter_string,
            'qualifiers': '',
            'enableNewCasinoExperience': False,
            'sort': {
                'by': 'RECOMMENDED',
            },
            'pagination': {
                'count': 100,
                'skip': 0,
            },
        },
        'query': graphql_query,
    }

    response = _execute_api_request(
        method="POST",
        url="https://www.royalcaribbean.com/cruises/graph",
        headers=headers,
        json_data=json_data
    )

    if not response:
        return

    data_container = response.json().get("data")
    cruise_search = data_container.get("cruiseSearch") if data_container else None
    results = cruise_search.get("results") if cruise_search else None
    cruises = results.get("cruises", []) if results else []

    # If the list is empty, it means the sailing is entirely sold out across all staterooms
    if not cruises:
        log("         Sailing is sold out")
        return

    sailings = cruises[0].get("sailings", [])

    for sailing in sailings:
        sailing_date_raw = sailing.get("sailDate", "")
        if sailing_date_raw.replace("-", "") != sail_date and sailing_date_raw != sail_date:
            continue

        log("Cheapest available cabins for this sailing:")
        prices = sailing.get("stateroomClassPricing", [])
        for price in prices:
            if not price:
                continue
            stateroom_class = price.get("stateroomClass", {}) if price else {}
            cabin_type = stateroom_class.get("name")

            if price.get("price") is None:
                log(f"\t{cabin_type} sold out")
            else:
                num_passengers = int(num_adults) + int(num_children)
                price_value = price["price"].get("value", 0)

                # Math normalization to scale raw decimal numbers to total travel party size
                cabin_cost_per_person = float(price_value) * num_passengers
                log(f"\t{GREEN}{cabin_cost_per_person} {currency}{RESET}: Cheapest {cabin_type} Price for {num_passengers}")


############################################################
# Onboard Infrastructure (Mobile App GraphQL Domain) functions
############################################################
def get_MDR_locations(ship_code: str, sail_date: str, is_royal: bool) -> List[str]:
    """
    Connects to the mobile app infrastructure to pinpoint exact Main Dining Room (MDR)
    location IDs to optimize targeted menu extraction.

    Args:
        ship_code (str): Target vessel tracking shorthand notation code.
        sail_date (str): Explicit date string execution parameter ('YYYYMMDD').
        is_royal (bool): True if Royal Caribbean, False if Celebrity Cruises.

    Returns:
        List[str]: Found system identifier tracking tokens for the primary restaurants.
    """
    # This gets the main dining room name to reduce API data request
    headers = {
        'appkey': APPKEY_MOBILE_GRAPH,
        'content-type': 'application/json',
        'user-agent': USER_AGENT_MOBILE_GRAPH,
    }

    # DESIGN NOTE: Multi-line string query isolating restaurant location IDs based on specific
    # app-layer parameters. This reduces unnecessary network payload size down the line.
    graphql_query = """
    query MobileAppPLPVenues(
        $category: String!,
        $sailDate: LocalDateScalar!,
        $shipCode: ShipCodeScalar!,
        $reservationId: String!,
        $passengerId: String!,
        $guestTypes: [GuestType!],
        $filter: ProductVenueFilterInput
    ) {
        productsByVenueCategories(
            category: $category,
            shipCode: $shipCode,
            sailDate: $sailDate,
            passengerId: $passengerId,
            reservationId: $reservationId,
            filter: $filter,
            guestTypes: $guestTypes
        ) {
            ... on VenueCategoryResultSuccess {
                venueCategories {
                    venueSubCategories {
                        venues {
                            id
                            title
                            categoryIds
                        }
                    }
                }
            }
        }
    }
    """

    json_data = {
        'operationName': 'MobileAppPLPVenues',
        'variables': {
            'category': 'dining',
            'sailDate': sail_date,
            'shipCode': ship_code,
            'reservationId': '',
            'passengerId': '',
            'guestTypes': [
                'ADULT',
            ],
            'filter': {
                'includeNonRevenueProducts': True,
                'subCategories': [
                    'dining_main', 'food_main',
                ],
            },
        },
        'query': graphql_query,
    }

    if not is_royal:
        json_data['variables']['category'] = 'food'

    response = _execute_api_request(
        method="POST",
        url="https://api.rccl.com/en/royal/mobile/graphql",
        headers=headers,
        json_data=json_data
    )

    venue_ids = []
    if not response:
        return venue_ids

    data_container = response.json().get("data")
    products_by_venue_categories = data_container.get("productsByVenueCategories") if data_container else None
    if products_by_venue_categories is None:
        return venue_ids

    venue_category = products_by_venue_categories.get("venueCategories")
    if not venue_category or not isinstance(venue_category, list):
        return venue_ids

    venue_sub_categories = venue_category[0].get("venueSubCategories")
    if not venue_sub_categories or not isinstance(venue_sub_categories, list):
        return venue_ids

    venues = venue_sub_categories[0].get("venues")
    if venues is None:
        return venue_ids

    for venue in venues:
        title = venue.get("title", "")
        venue_id = venue.get("id")

        # ALGORITHM CRITICAL: Royal Caribbean runs identical menus across all split dining room decks.
        # Grabbing the first primary match prevents duplicate network menu fetching requests.
        # Celebrity uses discrete unique menus per venue layout, requiring all IDs to be retained.
        if is_royal:
            if 'Dining Room' in title and "My Time" not in title:
                venue_ids.append(venue_id)
                return venue_ids
        else:
            if venue_id:
                venue_ids.append(venue_id)

    return venue_ids


def print_MDR_menus(ship_code: str, sail_date: str, venue_ids: List[str], ports: Dict[int, str]) -> None:
    """
    Queries the mobile GraphQL gateway to retrieve and format detailed onboard dining
    room menus day-by-day, cross-referencing daily port itineraries.
    """
    headers = {
        'appkey': APPKEY_MOBILE_GRAPH,
        'content-type': 'application/json',
        'user-agent': USER_AGENT_MOBILE_GRAPH,
    }

    # Structured multi-line GraphQL schema for high readability and debugging isolation
    graphql_query = """
    query MobileAppMenuDetails(
        $sailDate: LocalDateScalar!,
        $shipCode: ShipCodeScalar!,
        $filter: VenueFilterInput
    ) {
        venues(sailDate: $sailDate, shipCode: $shipCode, filter: $filter) {
            ... on VenueResultSuccess {
                venues {
                    id
                    title
                    menus {
                        day
                        timeOfDay
                        name
                        sections {
                            name
                            menuItems {
                                title
                            }
                        }
                    }
                }
            }
            ... on VenueExceptions {
                exceptions {
                    ... on VenueNotFound {
                        message
                    }
                }
            }
        }
    }
    """

    json_data = {
        'operationName': 'MobileAppMenuDetails',
        'variables': {
            'sailDate': sail_date,
            'shipCode': ship_code,
            'filter': {
                'ids': venue_ids,
            },
        },
        'query': graphql_query,
    }

    response = _execute_api_request(
        method="POST",
        url="https://api.rccl.com/en/royal/mobile/graphql",
        headers=headers,
        json_data=json_data,
        exit_on_fail=False
    )

    if not response:
        return

    # Standardized nested safe parsing strategy
    resp_json = response.json()
    data_container = resp_json.get("data") if resp_json else None
    venues_container = data_container.get('venues') if data_container else None
    venues = venues_container.get('venues', []) if venues_container else []

    if not venues:
        log("Menus not yet populated; please check again later")
        return

    for venue in venues:
        if not venue:
            continue

        menus = venue.get("menus", [])
        if not menus:
            log("Menus not yet populated; please check again later")
            return

        for menu in menus:
            if not menu:
                continue

            day = menu.get("day")
            time_of_day = menu.get("timeOfDay", "")
            name = menu.get("name", "")

            # Filter out unwanted drink/beverage data blocks
            if "Beverage" in name or "Wine" in name or "Wine" in time_of_day:
                continue

            # Defensive parsing against unexpected integer tracking errors
            try:
                port_title = sanitize_string(ports.get(int(day), ""))
            except (ValueError, TypeError):
                port_title = ""

            # Skip unmatched ports (handles Celebrity edge-cases safely)
            if not port_title:
                continue

            log(f"{GREEN}Day {day} {time_of_day} {name}{RESET} : {port_title}")
            sections = menu.get("sections", [])

            for section in sections:
                if not section:
                    continue

                section_name = sanitize_string(section.get("name", ""))
                ignore_list = ["Juices", "Coffee", "Specialty", "Allergens", "Beverage", "Wine", "Private"]
                if any(x in section_name for x in ignore_list):
                    continue

                log(f"{BLUE}{section_name}{RESET}")
                menu_items = section.get("menuItems", [])

                for menu_item in menu_items:
                    if not menu_item:
                        continue

                    title = sanitize_string(menu_item.get("title", ""))
                    if "Safety" in title or "Recommendations" in title or not title:
                        continue

                    # For the theme of dinner, the API is listing both the sectionName and title as the same
                    # Eliminate duplicate screen clutter when section name and title mirror each other
                    if title == section_name:
                        continue

                    log(title)
                log("")


##################################
# Main execution path
##################################
def setup_hybrid_logging(log_file_path: Optional[str] = None) -> None:
    """
    Initializes the tracking environment, functional logging aliases, and file captures.
    """
    global log, log_warn, log_err, has_terminal_issues

    # 1. Determine terminal safety based on your module-level configuration constant
    has_terminal_issues = any(k in os.environ for k in PROBLEM_ENVS)

    # 2. Safely attempt stream reconfiguration on Windows consoles
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            # Fallback for legacy Python installations lacking reconfigure hooks
            has_terminal_issues = True

    # 3. Construct and clear out the active root logging context
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    # Terminal Stream Handler (Keeps original ANSI terminal colors)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    if platform.system() == "iOS":
        console_handler.addFilter(StripAnsiFilter())

    root_logger.addHandler(console_handler)

    # 4. Plain Text File Handler (Only built if a log file path string is passed)
    if log_file_path:
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"

        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(delimiter)

            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter('%(message)s'))
            file_handler.addFilter(StripAnsiFilter())
            root_logger.addHandler(file_handler)
        except IOError as e:
            sys.stderr.write(f"Warning: Could not open log file '{log_file_path}': {e}\n")

    # 5. Initialize the shortcut execution instances and map to module globals
    easy_log_instance = EasyLogger(root_logger)
    log = easy_log_instance
    log_warn = easy_log_instance.warn
    log_err = easy_log_instance.error

    # 6. Intercept raw standard print statements system-wide
    sys.stdout = PrintRedirector(root_logger.info)


def main() -> None:
    parser = argparse.ArgumentParser(description="Browse Royal Caribbean Price")
    parser.add_argument('-c', '--currency', type=str, default='System', help='currency (default: System Setting)')
    parser.add_argument('-s', '--ship', type=str, help='Ship')
    parser.add_argument('-d', '--saildate', type=str, help='Sail Date (mm/dd/yy format)')
    parser.add_argument('-o', '--sortorder', choices=['asc', 'desc'], default="asc", dest="sort_order", help='Set sorting order')
    parser.add_argument('-k', '--sortkey', choices=['price', 'alpha', 'default'], default="default", dest="sort_key", help='Set value to sort on')
    parser.add_argument('-w', '--watchlistcodes', action='store_true', dest="watchlist_codes", help='Show Codes For Watchlist')
    parser.add_argument('-a', '--activitysort', choices=['date', 'alpha', 'default'], default="default", dest="activity_sort", help='Set activity sorting metric')
    parser.add_argument('-l', '--logfile', type=str, nargs='?', const='output.txt', dest="log_file", help='optional logfile, eg. output.txt')
    args = parser.parse_args()

    currency = args.currency
    if currency == "System":
        currency = get_system_currency()

    setup_hybrid_logging(args.log_file)
    if args.log_file:
        log(f"Logging run to file: {args.log_file}")

    ships = get_ships_web()

    selected_index = None
    if args.ship:
        # Find the matching ship programmatically
        for i, ship in enumerate(ships):
            if ship['name'] == f"{args.ship} of the Seas" or ship['name'] == f"Celebrity {args.ship}":
                selected_index = i
                break
        if selected_index == None:
            log(f"I can't find that ship name {args.ship}; please try again")
            return
    else:
        # User chooses a ship dynamically from the interactive terminal menu
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

        if args.saildate:
            # Find the matching sailing profile match
            for i, sailing in enumerate(sailings):
                if args.saildate == sailing['displayDate'].split(" ")[0]:
                    sailing_index = i
                    log(f"Getting info for {sailing['displayDate']} {sailing['description']}")
                    break
            if sailing_index is None:
                log(f"I can't find that sail date {args.saildate} for {ship_name}; please try again")
                return
        else:
            # User chooses a cruise itinerary target from the prompt menu
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
            get_cruise_price_from_API(currency, ship_code + sailing['voyageCode'], sailing['date'], num_adults, num_children)
            log("")

            log("Gathering list of products.  This may take a few minutes; please be patient.")
            log("These are public prices, sale prices for you could be less")
            log("")
            print_all_products(ship_code, sailing['date'], sailing['duration'], currency, args.sort_key, args.sort_order, args.watchlist_codes, ports)

            log("")
            log("Gathering list of activities.  This may take a few minutes; please be patient.")
            activities = get_all_activities_web(ship_code, sailing['date'])
            print_all_activities(activities, args.activity_sort)

            log("")
            log("Gathering list of Main Dining Room Menus.  This may take a few minutes; please be patient.")
            mdr_names = get_MDR_locations(ship_code, sailing['date'], is_royal)
            print_MDR_menus(ship_code, sailing['date'], mdr_names, ports)
    else:
        log("Invalid ship selection")

    input("Hit ENTER to quit: ")
    log("Have a nice day!")


##################################
# Dead/Obsolete/Unused functions
##################################
appkey_mobile = 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc' # royal
appversion_mobile = '1.73.4'
user_agent_mobile = 'royal/1.73.4 (com.rccl.royalcaribbean; build:2528; android 16) okhttp/4.12.0'

def getShips():
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
        log(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    shipNames = []
    ships = response.json().get("payload").get("ships")
    i = 0
    for ship in ships:
        shipCode = ship.get("shipCode")
        name = ship.get("name")
        shipNames.append({'code': shipCode, 'name': name})
        if shipCode == "HM":
            #Force Hero until it is added to the API
            shipNames.append({'code': 'HE', 'name': 'Hero of the Seas'})

    return shipNames

def getSailings(shipCode):
    headers = {
        'appkey': appkey_mobile,
        'accept': 'application/json',
        'appversion': appversion_mobile,
        'accept-language': 'en',
        'user-agent': user_agent_mobile,
    }

    params = {
        'resultSet': '300',
    }

    try:
        response = requests.get(f'https://api.rccl.com/en/royal/mobile/v3/ships/{shipCode}/voyages', params=params, headers=headers)
    except Exception as e:
        log(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    voyages = response.json().get("payload").get("voyages")
    sailings = []
    for voyage in voyages:
        sailDate = voyage.get("sailDate")
        duration = voyage.get("duration")
        voyageCode = voyage.get("voyageCode")
        voyageDescription = voyage.get("voyageDescription")
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(DATE_DISPLAY_FORMAT)
        sailings.append({'date': sailDate, 'displayDate': sailDateDisplay,'description': voyageDescription,'duration':duration,'voyageCode':voyageCode})

    return sailings


def getSailingDetails(shipCode,sailDate):
    headers = {
    'appkey': appkey_mobile,
    'accept': 'application/json',
    'user-agent': user_agent_mobile,
    'appversion': appversion_mobile,
    }

    try:
        response = requests.get(f'https://api.rccl.com/en/royal/mobile/v3/ships/voyages/{shipCode}{sailDate}/enriched', headers=headers)
    except Exception as e:
        log(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    ports = {}

    itinerary = response.json().get("payload").get("sailingInfo")[0].get("itinerary")
    if itinerary is None:
        return ports

    portInfo = itinerary.get("portInfo")
    for port in portInfo:
        title = sanitize_string(port.get("title"))
        arrivalDateTime = port.get("arrivalDateTime")
        portType = port.get("portType","Unknown")
        day = port.get("day","Unknown")

        # Save port names for later
        ports[int(day)] = title

        # Sometimes fields are not filled, so we skip (like international date line changes)
        if arrivalDateTime is None:
            log(f"Day {day}: {title}")
            continue

        arrivalDateDisplay = datetime.strptime(arrivalDateTime[0:8], "%Y%m%d").strftime(DATE_DISPLAY_FORMAT)
        departureDateTime = port.get("departureDateTime")
        printString = f"Day {day} ({arrivalDateDisplay}): {title}"
        if portType == "EMBARK":
            printString += f" ↑ {departureDateTime[9:13]}"
        elif portType == "DEBARK":
            printString += f" ↓ {arrivalDateTime[9:13]}"
        elif title != "Cruising":
            printString += f" ↓ {arrivalDateTime[9:13]} ↑ {departureDateTime[9:13]}"
        if portType == "TENDERED":
            printString += f" (Tendered)"

        portString = sanitize_string(printString)
        log(portString)

    if has_terminal_issues:
        log("(^ means gangway up; v means gangway down)")
    else:
        log("(↑ means gangway up; ↓ means gangway down)")

    return ports

def getAllActivities(shipCode, sailDate):
    headers = {
        'appkey': appkey_mobile,
        'accept': 'application/json',
        'user-agent': user_agent_mobile,
        'appversion': appversion_mobile,
    }

    params = {
            'sailingID': shipCode + sailDate,
            'limit': '200',
            'offset': '0',
            #'availableForSale': 'FALSE',
        }

    products = []
    for offset in range(0,10000,200):
        params['offset'] = offset

        response = requests.get('https://api.rccl.com/en/royal/mobile/v3/products', params=params, headers=headers)
        if response.json() is None or response.json().get("payload") is None:
            return products

        tempProducts = response.json().get("payload").get("products")
        for product in tempProducts:
            productType = product.get("productType").get("productType")

            if productType != "NON_REVENUE_SCHEDULABLE":
                continue

            productTitle = product.get("productTitle")
            location = product.get("productLocation").get("locationTitle","")

            if location is None:
                location = ""

            offering = product.get("offering")
            for offer in offering:
                if offer is not None:
                    offeringDate = offer.get("offeringDate")
                    offeringTime = offer.get("offeringTime")
                    day = days_between(sailDate,offeringDate)
                    products.append({'productTitle':productTitle,'location':location,'offeringDate':offeringDate,'offeringTime':offeringTime,'day':day})

    return products


# This is not needed, as dress code is in list of activities
def printThemeNights(shipCode,sailDate,duration):
    headers = {
        'appkey': appkey_mobile,
        'accept': 'application/json',
        'user-agent': user_agent_mobile,
        'appversion': appversion_mobile,
    }

    response = requests.get(f'https://api.rccl.com/en/royal/mobile/v1/ships/{shipCode}/sailDate/{sailDate}/needToKnow', headers=headers)

    payload = response.json().get("payload")

    word_list = ["dress", "attire", "theme","casual"]

    foundTheme = 0
    for needToKnowCard in payload.get("needToKnowCards"):
        includedDayCards = needToKnowCard.get("includedDayCards")
        date = needToKnowCard.get("cardIdentifier")[:8]
        for card in includedDayCards:
            title = card.get("cardTitle")
            titleLower = card.get("cardTitle").lower()
            #if date == '20260402': # I use this to find missing themes
            #    log(title)

            if any(word.lower() in titleLower for word in word_list):
                if "address" in titleLower:
                    continue
                foundTheme += 1

                day = days_between(sailDate,date)
                subtitle = re.split(r'[.!]+', card.get("cardSubtitle"))[0].replace("<p>", "").replace("&nbsp;","")
                offeringDate = datetime.strptime(date, "%Y%m%d").strftime("%B %d, %Y")
                log(f"{GREEN}{offeringDate} (Day {day}){RESET} {title}: {subtitle}")

    if foundTheme == 0:
        log("Themes Not Available")
    elif foundTheme < duration:
        log("Themes Not Fully Loaded")
#    flush_print_buffer()

##################################
# End Dead/Obsolete/Unused functions
##################################

if __name__ == "__main__":
    main()
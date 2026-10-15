"""core/browsing.py: Core business logic and API endpoints for ship discovery, pricing, and itinerary browsing."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List

# TODO:  should we be importing the entire module and using it's children as
#        client._execuite_api_request for better modularity?  Reference modules
#        instead of individual names
from royal_caribbean.api.client import _execute_api_request
from royal_caribbean.utils.constants import (
    APPKEY_MOBILE_GRAPH,
    APPKEY_WEB,
    DATE_DISPLAY_FORMAT,
    USER_AGENT_MOBILE_GRAPH,
    USER_AGENT_WEB
)
from royal_caribbean.utils.helpers import (
    _extract_json_array,
    _safe_json,
    days_between,
    get_system_currency,
    print_response,
)
from royal_caribbean.utils.logging import (
    GREEN,
    BLUE,
    YELLOW,
    RESET,
    has_terminal_issues,
    log,
    sanitize_string,
)

__all__ = [
    "days_between",
    "get_system_currency",
    "get_ships_web",
    "get_sailings_web",
    "get_sailing_details_web",
    "get_web_categories",
    "get_products_graph_all_pages",
    "print_and_sort_products",
    "print_all_products",
    "get_all_activities_web",
    "print_all_activities",
    "get_cruise_price_from_API",
    "get_MDR_locations",
    "print_MDR_menus",
]


def get_ships_web() -> List[Dict[str, str]]:
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }
    params = {'sort': 'name'}

    response = _execute_api_request(
        method="GET",
        url="https://aws-prd.api.rccl.com/en/royal/web/v2/ships",
        params=params,
        headers=headers,
        on_failure="retry"
    )

    if not response:
        return []

    ship_names = []
    payload = (_safe_json(response) or {}).get("payload", {})
    ships = payload.get("ships", []) if payload else []

    for ship in ships:
        ship_code = ship.get("shipCode")
        name = ship.get("name")
        ship_names.append({'code': ship_code, 'name': (name or "").title().replace(" Of The ", " of the ")})

    return ship_names


def get_sailings_web(ship_code: str) -> List[Dict[str, Any]]:
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }

    response = _execute_api_request(
        method="GET",
        url=f"https://aws-prd.api.rccl.com/en/royal/web/v3/ships/{ship_code}/voyages",
        headers=headers,
        on_failure="retry"
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
        if not sail_date:
            continue
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
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }

    response = _execute_api_request(
        method="GET",
        url=f"https://aws-prd.api.rccl.com/en/royal/web/v3/ships/{ship_code}/sailDate/{sail_date}",
        headers=headers,
        on_failure="retry"
    )

    ports = {}
    if not response:
        return ports

    try:
        resp_json = response.json()
    except Exception:
        return ports

    payload = resp_json.get("payload") if resp_json else None
    sailing_info = payload.get("sailingInfo") if payload else None

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
        day = port.get("day")
        if day is None:
            continue

        ports[int(day)] = title

        if arrival_date_time is None:
            log.info(f"Day {day}: {title}")
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

        log.info(sanitize_string(print_string))

    if has_terminal_issues:
        log.info("(^ means gangway up; v means gangway down)")
    else:
        log.info("(↑ means gangway up; ↓ means gangway down)")

    return ports


def get_web_categories(ship: str, saildate: str) -> Dict[str, str]:
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }

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
        json_data=json_data,
        on_failure="retry"
    )

    product_map = {}
    if not response:
        return product_map

    data_container = (_safe_json(response) or {}).get("data")
    categories_container = data_container.get("categories") if data_container else None
    if categories_container is None:
        log.info("No Items for Sale")
        return product_map

    categories_list = categories_container.get("categories")
    if categories_list is None:
        log.info("No Items for Sale")
        return product_map

    for category in categories_list:
        product_map[category.get("id")] = category.get("name")

    return product_map


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
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }

    if sortkey == "price":
        api_sort_key = "PRICE"
    elif sortkey == "alpha":
        api_sort_key = "TITLE"
    else:
        api_sort_key = "RANK"

    api_sort_order = "DESCENDING" if sortorder == "desc" else "ASCENDING"

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
            'pageSize': 12,
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
    for page in range(100):
        json_data['variables']['currentPage'] = page

        response = _execute_api_request(
            method="POST",
            url="https://aws-prd.api.rccl.com/en/royal/web/graphql",
            headers=headers,
            json_data=json_data,
            on_failure="skip"
        )

        if not response:
            break

        data_container = (_safe_json(response) or {}).get("data") if response else None
        products_container = data_container.get("products") if data_container else None
        if products_container is None:
            break

        temp_products = products_container.get("commerceProducts")
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
    if products is None:
        return

    if sort_key == 'alpha':
        if sort_order == 'desc':
            sorted_products = sorted(products, key=lambda product: (product.get('title') or '').lstrip(), reverse=True)
        else:
            sorted_products = sorted(products, key=lambda product: (product.get('title') or '').lstrip())
    else:
        sorted_products = products

    for product in sorted_products:
        if not product or not isinstance(product, dict):
            continue

        current_id = product.get("id")
        price_list = product.get("price")

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

        price = re.sub(r'[^0-9\.]', '', str(price))
        price = price.replace(" ", "")
        try:
            if not price or float(price) == 0:
                continue
        except ValueError:
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

        log.info(print_string)

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

            log.info(print_string)


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
    product_map = get_web_categories(ship_code, sail_date)

    for key, category_name in product_map.items():
        log.info(f"{BLUE}{category_name}{RESET}")

        if key == "shorex":
            for day in range(1, int(duration or 7) + 2):
                products = get_products_graph_all_pages(
                    ship_code, sail_date, duration, currency, sort_key, sort_order, key, str(day)
                )
                if products:
                    port_title = ports.get(day, "")
                    log.info(f"\t{BLUE}Day {day}: {port_title}{RESET}")
                    print_and_sort_products(products, sort_key, sort_order, currency, key, show_watchlist_codes)
        else:
            products = get_products_graph_all_pages(
                ship_code, sail_date, duration, currency, sort_key, sort_order, key, "all"
            )
            print_and_sort_products(products, sort_key, sort_order, currency, key, show_watchlist_codes)


def get_all_activities_web(ship_code: str, sail_date: str) -> List[Dict[str, Any]]:
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'application/json',
        'AppKey': APPKEY_WEB,
    }

    products = []
    limit = 200

    for offset in range(0, 10000, limit):
        params = {
            'sailingID': ship_code + sail_date,
            'limit': str(limit),
            'offset': str(offset),
        }

        response = _execute_api_request(
            method="GET",
            url="https://aws-prd.api.rccl.com/en/royal/web/v3/products",
            params=params,
            headers=headers,
            on_failure="skip"
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
                    if not offering_date:
                        continue
                    day = days_between(sail_date, offering_date)

                    products.append({
                        'productTitle': product_title,
                        'location': location,
                        'offeringDate': offering_date,
                        'offeringTime': offering_time,
                        'day': day
                    })

        if len(temp_products) < limit:
            break

    return products


def print_all_activities(activities: List[Dict[str, Any]], sort_order: str) -> None:
    if not activities:
        log.info("No Activities Scheduled")
        return

    if sort_order == 'date':
        sorted_activities = sorted(
            activities,
            key=lambda activity: (activity.get('offeringDate', ''), activity.get('offeringTime', ''))
        )
    elif sort_order == 'alpha':
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

        log.info(f"{product_title}\t {location} {GREEN}{offering_date} (Day {day}) {offering_time}{RESET}")


def get_cruise_price_from_API(
    currency: str,
    package_code: str,
    sail_date: str,
    num_adults: int,
    num_children: int,
    is_royal: bool
) -> None:
    headers = {
        'User-Agent': USER_AGENT_WEB,
        'Accept': 'text/x-component',
        'Accept-Language': 'en-US,en;q=0.9',
        'RSC': '1',
    }

    formatted_sail_date = f"{sail_date[0:4]}-{sail_date[4:6]}-{sail_date[6:8]}"

    params = {
        'packageCode': package_code,
        'sailDate': formatted_sail_date,
        'country': 'USA',
        'selectedCurrencyCode': currency,
        'shipCode': package_code[0:2],
        'cabinClassType': 'INTERIOR',
        'roomIndex': '0',
        'r0a': num_adults,
        'r0c': num_children,
        'r0b': 'n',
        'r0r': 'n',
        'r0s': 'n',
        'r0q': 'n',
        'r0t': 'n',
        'r0d': 'INTERIOR',
        'r0D': 'y',
        'rgVisited': 'true',
        'r0C': 'y',
    }

    base_host = "royalcaribbean.com" if is_royal else "celebritycruises.com"

    response = _execute_api_request(
        method="GET",
        url=f"https://www.{base_host}/room-selection/type-and-subtype",
        params=params,
        headers=headers,
        on_failure="skip",
        use_impersonation=False
    )

    if response is None:
        log.info("         Could not retrieve cabin prices (request failed)")
        return

    rooms = _extract_json_array(response.text, "rooms")
    stateroom_types = []
    if rooms:
        try:
            stateroom_types = (rooms[0].get("options") or {}).get("stateroomTypes") or []
        except AttributeError:
            stateroom_types = []

    if not stateroom_types:
        log.info("         Sailing is sold out")
        return

    num_passengers = int(num_adults) + int(num_children)
    log.info("Cheapest available cabins for this sailing:")
    for stateroom_type in stateroom_types:
        cabin_type = stateroom_type.get("name")

        cheapest_price = None
        for stateroom_subtype in stateroom_type.get("stateroomSubtypes") or []:
            pricing = stateroom_subtype.get("pricing") or {}
            invoice = pricing.get("invoice") or {}
            total = invoice.get("total")
            if total is not None and (cheapest_price is None or total < cheapest_price):
                cheapest_price = total

        if cheapest_price is None:
            log.info(f"\t{cabin_type} sold out")
        else:
            log.info(f"\t{GREEN}{cheapest_price} {currency}{RESET}: Cheapest {cabin_type} Price for {num_passengers}")


def get_MDR_locations(ship_code: str, sail_date: str, is_royal: bool) -> List[str]:
    headers = {
        'AppKey': APPKEY_MOBILE_GRAPH,
        'content-type': 'application/json',
        'user-agent': USER_AGENT_MOBILE_GRAPH,
    }

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
            'guestTypes': ['ADULT'],
            'filter': {
                'includeNonRevenueProducts': True,
                'subCategories': ['dining_main', 'food_main'],
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
        json_data=json_data,
        on_failure="skip"
    )

    venue_ids = []
    if not response:
        return venue_ids

    data_container = (_safe_json(response) or {}).get("data")
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

        if is_royal:
            if 'Dining Room' in title and "My Time" not in title:
                venue_ids.append(venue_id)
                return venue_ids
        else:
            if venue_id:
                venue_ids.append(venue_id)

    return venue_ids


def print_MDR_menus(ship_code: str, sail_date: str, venue_ids: List[str], ports: Dict[int, str]) -> None:
    headers = {
        'AppKey': APPKEY_MOBILE_GRAPH,
        'content-type': 'application/json',
        'user-agent': USER_AGENT_MOBILE_GRAPH,
    }

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
            'filter': {'ids': venue_ids},
        },
        'query': graphql_query,
    }

    response = _execute_api_request(
        method="POST",
        url="https://api.rccl.com/en/royal/mobile/graphql",
        headers=headers,
        json_data=json_data,
        on_failure="skip"
    )

    if not response:
        return

    resp_json = _safe_json(response)
    data_container = resp_json.get("data") if resp_json else None
    venues_container = data_container.get('venues') if data_container else None
    venues = venues_container.get('venues', []) if venues_container else []

    if not venues:
        log.info("Menus not yet populated; please check again later")
        return

    for venue in venues:
        if not venue:
            continue

        menus = venue.get("menus", [])
        if not menus:
            log.info("Menus not yet populated; please check again later")
            continue

        for menu in menus:
            if not menu:
                continue

            day = menu.get("day")
            time_of_day = menu.get("timeOfDay", "")
            name = menu.get("name", "")

            if "Beverage" in name or "Wine" in name or "Wine" in time_of_day:
                continue

            try:
                port_title = sanitize_string(ports.get(int(day), ""))
            except (ValueError, TypeError):
                port_title = ""

            if not port_title:
                continue

            log.info(f"{GREEN}Day {day} {time_of_day} {name}{RESET} : {port_title}")
            sections = menu.get("sections", [])

            for section in sections:
                if not section:
                    continue

                section_name = sanitize_string(section.get("name", ""))
                ignore_list = ["Juices", "Coffee", "Specialty", "Allergens", "Beverage", "Wine", "Private"]
                if any(x in section_name for x in ignore_list):
                    continue

                log.info(f"{BLUE}{section_name}{RESET}")
                menu_items = section.get("menuItems", [])

                for menu_item in menu_items:
                    if not menu_item:
                        continue

                    title = sanitize_string(menu_item.get("title", ""))
                    if "Safety" in title or "Recommendations" in title or not title:
                        continue

                    if title == section_name:
                        continue

                    log.info(title)
                log.info("")


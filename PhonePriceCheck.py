from __future__ import annotations

import base64
import getpass
import json
import requests
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Set
from urllib.parse import quote


###############################################################################
# Edit these values
###############################################################################
username = ""
password = ""

# Set cruise line: "royalcaribbean" or "celebritycruises"
cruiseLineName = "royalcaribbean"
# cruiseLineName = "celebritycruises"

# Optional: Override currency (e.g. "USD", "CAD", "AUD", "GBP", "EUR")
currencyOverride = ""

# Enable or disable colored terminal output
# (Set to False if your terminal displays raw escape codes like '[91m')
USE_COLOR = True

###############################################################################
# Do Not Edit Below Here
###############################################################################
APP_KEY = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
DATE_DISPLAY_FORMAT = "%x"  # Uses the system/locale date format

if USE_COLOR:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
else:
    RED = ""
    GREEN = ""
    YELLOW = ""
    BLUE = ""
    RESET = ""


@dataclass
class AuthSession:
    """Encapsulates authenticated session state and tokens."""
    access_token: str
    account_id: str
    session: requests.Session


@dataclass
class AuditItem:
    """Encapsulates a purchased Cruise Planner item to be checked against catalog prices."""
    reservation_id: str
    passenger_id: str
    passenger_name: str
    room: str
    ship: str
    start_date: str
    prefix: str
    product: str
    paid_price: float
    currency: str
    guest_age_string: str
    order_code: str
    order_date: str
    is_owner: bool


def _execute_api_request(
    session: requests.Session,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    data: Optional[str] = None,
    timeout: int = 15,
) -> Optional[Dict[str, Any]]:
    """Helper to execute HTTP requests safely and return parsed JSON or payload."""
    try:
        resp = session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            data=data,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if isinstance(body, dict):
            return body.get("payload") if "payload" in body else body
    except Exception:
        return None
    return None


def login(user_str: str, pass_str: str, line_name: str) -> AuthSession:
    """Authenticates against the cruise line OAuth2 endpoint and decodes the JWT account ID."""
    session = requests.Session()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC",
        "User-Agent": MOBILE_USER_AGENT,
    }

    url_safe_password = quote(pass_str, safe="")
    url_safe_username = quote(user_str, safe="")
    data = (
        f"grant_type=password&username={url_safe_username}"
        f"&password={url_safe_password}&scope=openid+profile+email+vdsid"
    )

    url = f"https://www.{line_name}.com/auth/oauth2/access_token"
    res_json = _execute_api_request(session, "POST", url, headers=headers, data=data)

    if not res_json or not res_json.get("access_token"):
        print(
            f"{RED}{line_name} website might be down, username/password incorrect, "
            f"or password contains unsupported characters. Quitting.{RESET}"
        )
        sys.exit(1)

    access_token = res_json["access_token"]

    # JWT base64URL decoding for account ID ('sub' field)
    try:
        payload_b64 = access_token.split(".")[1]
        decoded_bytes = base64.urlsafe_b64decode(
            payload_b64.replace("+", "-").replace("/", "_") + "=="
        )
        auth_info = json.loads(decoded_bytes.decode("utf-8"))
        account_id = auth_info["sub"]
    except Exception as e:
        print(f"{RED}Failed to parse JWT auth token: {e}{RESET}")
        sys.exit(1)

    return AuthSession(access_token=access_token, account_id=account_id, session=session)


def get_ship_dictionary(session: requests.Session) -> Dict[str, str]:
    """Retrieves fleet ship codes to names mapping from the public mobile API."""
    headers = {
        "appkey": "cdCNc04srNq4rBvKofw1aC50dsdSaPuc",
        "accept": "application/json",
        "appversion": "1.54.0",
        "accept-language": "en",
        "user-agent": "okhttp/4.10.0",
    }
    payload = _execute_api_request(
        session,
        "GET",
        "https://api.rccl.com/en/all/mobile/v2/ships",
        params={"sort": "name"},
        headers=headers,
        timeout=10,
    )
    if payload and isinstance(payload, dict):
        ships = payload.get("ships") or []
        return {s.get("shipCode"): s.get("name") for s in ships if s.get("shipCode")}

    return {"HE": "Hero of the Seas"}


def check_item_catalog_price(
    auth: AuthSession,
    api_brand: str,
    line_name: str,
    item: AuditItem,
) -> None:
    """Queries current catalog pricing for a purchased item and prints price drop alerts."""
    headers = {
        "Access-Token": auth.access_token,
        "AppKey": APP_KEY,
        "vds-id": auth.account_id,
        "User-Agent": MOBILE_USER_AGENT,
    }
    params = {
        "reservationId": item.reservation_id,
        "startDate": item.start_date,
        "currencyIso": item.currency,
        "passengerId": item.passenger_id,
    }

    url = (
        f"https://aws-prd.api.rccl.com/en/{api_brand}/web/commerce-api/"
        f"catalog/v2/{item.ship}/categories/{item.prefix}/products/{item.product}"
    )
    payload = _execute_api_request(auth.session, "GET", url, headers=headers, params=params)
    if not payload:
        return

    title = payload.get("title", "Item")
    variant = ""
    try:
        variant = (
            payload.get("baseOptions")[0]
            .get("selected")
            .get("variantOptionQualifiers")[0]
            .get("value")
        )
    except (IndexError, KeyError, TypeError):
        pass

    if variant and "Bottles" in variant:
        title = f"{title} ({variant})"

    price_payload = payload.get("startingFromPrice")
    if not price_payload:
        print(
            f"{YELLOW}{item.passenger_name.ljust(10)} ({item.room}) best price for {title}: "
            f"${item.paid_price:.2f} (No Longer for Sale){RESET}"
        )
        return

    current_price = (
        price_payload.get(f"{item.guest_age_string}PromotionalPrice")
        or price_payload.get(f"{item.guest_age_string}ShipboardPrice")
        or 0.0
    )

    if current_price > 0 and current_price < item.paid_price:
        text = (
            f"{item.passenger_name}: Rebook! {title} price dropped to "
            f"${current_price:.2f} (Paid: ${item.paid_price:.2f})"
        )
        promo_desc = payload.get("promoDescription")
        if promo_desc and promo_desc.get("displayName"):
            text += f"\n Promotion: {promo_desc.get('displayName')}"

        cancel_url = (
            f"https://www.{line_name}.com/account/cruise-planner/order-history?"
            f"bookingId={item.reservation_id}&shipCode={item.ship}&sailDate={item.start_date}"
        )
        text += f"\n Cancel Order {item.order_date} {item.order_code} at {cancel_url}"

        if not item.is_owner:
            text += "\n (Booked by another party member - they must cancel/rebook for you)"

        print(f"{RED}{text}{RESET}")
    else:
        status = (
            f"{GREEN}{item.passenger_name.ljust(10)} ({item.room}) best price for {title}: "
            f"${item.paid_price:.2f}{RESET}"
        )
        if current_price > item.paid_price:
            status += f" (now ${current_price:.2f})"
        print(status)


def process_orders(
    auth: AuthSession,
    api_brand: str,
    line_name: str,
    reservation_id: str,
    passenger_id: str,
    ship: str,
    start_date: str,
    number_of_nights: int,
    curr_override: str,
    found_items: Set[str],
) -> None:
    """Fetches order history for a booking and audits active item pricing."""
    currency = curr_override if curr_override else "USD"
    headers = {
        "Access-Token": auth.access_token,
        "AppKey": APP_KEY,
        "Account-Id": auth.account_id,
        "User-Agent": MOBILE_USER_AGENT,
    }
    params = {
        "passengerId": passenger_id,
        "reservationId": reservation_id,
        "sailingId": f"{ship}{start_date}",
        "currencyIso": currency,
        "includeMedia": "false",
    }

    url = f"https://aws-prd.api.rccl.com/en/{api_brand}/web/commerce-api/calendar/v1/{ship}/orderHistory"
    payload = _execute_api_request(auth.session, "GET", url, headers=headers, params=params)
    if not payload:
        return

    orders = (payload.get("myOrders") or []) + (payload.get("ordersOthersHaveBookedForMe") or [])
    for order in orders:
        if (order.get("orderTotals") or {}).get("total", 0) <= 0:
            continue

        order_code = order.get("orderCode")
        try:
            date_obj = datetime.strptime(order.get("orderDate", ""), "%Y-%m-%d")
            order_date = date_obj.strftime(DATE_DISPLAY_FORMAT)
        except (ValueError, TypeError):
            order_date = order.get("orderDate", "")

        is_owner = order.get("owner", True)
        detail_url = (
            f"https://aws-prd.api.rccl.com/en/{api_brand}/web/commerce-api/"
            f"calendar/v1/{ship}/orderHistory/{order_code}"
        )

        detail_payload = _execute_api_request(
            auth.session, "GET", detail_url, headers=headers, params=params
        )
        if not detail_payload:
            continue

        for detail_item in detail_payload.get("orderHistoryDetailItems") or []:
            prod_summary = detail_item.get("productSummary") or {}
            try:
                product = prod_summary.get("baseOptions")[0].get("selected").get("code")
            except (IndexError, KeyError, TypeError):
                product = prod_summary.get("defaultVariantId")

            prefix = (prod_summary.get("productTypeCategory") or {}).get("id", "")
            sales_unit = prod_summary.get("salesUnit", "")

            for guest in detail_item.get("guests") or []:
                if guest.get("orderStatus") == "CANCELLED":
                    continue

                price_details = guest.get("priceDetails") or {}
                paid_price = price_details.get("subtotal", 0.0)
                paid_quantity = price_details.get("quantity", 1)

                if paid_price == 0:
                    continue

                guest_passenger_id = guest.get("id")
                first_name = guest.get("firstName", "").capitalize()
                guest_reservation_id = guest.get("reservationId")
                guest_age_string = (guest.get("guestType") or "adult").lower()

                item_key = f"{guest_passenger_id}{guest_reservation_id}{prefix}{product}"
                if item_key in found_items:
                    continue
                found_items.add(item_key)

                if sales_unit in ["PER_NIGHT", "PER_DAY"] and number_of_nights > 0:
                    paid_price = round(paid_price / number_of_nights, 2)

                if paid_quantity > 0:
                    paid_price = round(paid_price / paid_quantity, 2)

                item_currency = price_details.get("currency", currency)
                room = guest.get("stateroomNumber", "GTY")

                item = AuditItem(
                    reservation_id=guest_reservation_id,
                    passenger_id=guest_passenger_id,
                    passenger_name=first_name,
                    room=room,
                    ship=ship,
                    start_date=start_date,
                    prefix=prefix,
                    product=product,
                    paid_price=paid_price,
                    currency=item_currency,
                    guest_age_string=guest_age_string,
                    order_code=order_code,
                    order_date=order_date,
                    is_owner=is_owner,
                )

                check_item_catalog_price(auth, api_brand, line_name, item)


def run_audit(auth: AuthSession, line_name: str, curr_override: str) -> None:
    """Fetches all voyages and audits purchased Cruise Planner items."""
    api_brand = "royal" if line_name == "royalcaribbean" else "celebrity"
    brand_code = "R" if line_name == "royalcaribbean" else "C"

    ship_dict = get_ship_dictionary(auth.session)
    headers = {
        "Access-Token": auth.access_token,
        "AppKey": APP_KEY,
        "vds-id": auth.account_id,
        "User-Agent": MOBILE_USER_AGENT,
    }
    params = {"brand": brand_code, "includeCheckin": "false"}

    url = f"https://aws-prd.api.rccl.com/v1/profileBookings/enriched/{auth.account_id}"
    payload = _execute_api_request(auth.session, "GET", url, headers=headers, params=params)

    if not payload:
        print(f"{RED}Failed to fetch profile bookings.{RESET}")
        return

    bookings = payload.get("profileBookings") or []
    if not bookings:
        print(f"{YELLOW}No active bookings found for this account.{RESET}")
        return

    found_items: Set[str] = set()

    for booking in bookings:
        reservation_id = str(booking.get("bookingId"))
        passenger_id = str(booking.get("passengerId"))
        sail_date = booking.get("sailDate")
        nights = booking.get("numberOfNights", 1)
        ship_code = booking.get("shipCode", "")
        guests = booking.get("passengers") or []

        passenger_names = ", ".join(
            g.get("firstName", "").capitalize() for g in guests if g.get("firstName")
        )

        try:
            sail_date_display = datetime.strptime(sail_date, "%Y%m%d").strftime(DATE_DISPLAY_FORMAT)
        except (ValueError, TypeError):
            sail_date_display = sail_date

        ship_name = ship_dict.get(ship_code, ship_code)
        stateroom = booking.get("stateroomNumber") or "GTY"

        print(
            f"\n{BLUE}Booking {reservation_id}: {sail_date_display} "
            f"{ship_name} Room {stateroom} ({passenger_names}){RESET}"
        )

        if booking.get("balanceDue"):
            print(f"{YELLOW}  Notice: Remaining Cruise Balance is ${booking.get('balanceDueAmount')}{RESET}")

        process_orders(
            auth,
            api_brand,
            line_name,
            reservation_id,
            passenger_id,
            ship_code,
            sail_date,
            nights,
            curr_override,
            found_items,
        )


def main() -> None:
    user_val = username
    pass_val = password

    # Fallback to terminal input if not edited in file
    if not user_val:
        user_val = input("Enter Cruise Account Username: ").strip()
    if not pass_val:
        pass_val = getpass.getpass("Enter Cruise Account Password: ").strip()

    if not user_val or not pass_val:
        print(f"{RED}Username and password are required.{RESET}")
        sys.exit(1)

    print(f"Logging in to {cruiseLineName} as {user_val}...")
    auth = login(user_val, pass_val, cruiseLineName)
    print(f"{GREEN}Login successful!{RESET}")

    run_audit(auth, cruiseLineName, currencyOverride)


if __name__ == "__main__":
    main()


#import requests
#from datetime import datetime
#from urllib.parse import urlparse, parse_qs, quote
#import re
#import base64
#import json
#import argparse
#import locale
#
####################
## Edit these values
#
#username = ""
#password = ""
#cruiseLineName =  "royalcaribbean"
##cruiseLineName = "celebritycruises"
## aws-prd API brand segment, same mapping the main checker's api_brand uses
#apiBrand = "royal" if cruiseLineName == "royalcaribbean" else "celebrity"
#
##########################
## Do Not Edit Below Here
#
#appKey = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"
#
#currencyOverride = ""
#
#foundItems = []
#
##RED = '\033[91m'
##GREEN = '\033[92m'
#RED = ''
#GREEN = ''
#YELLOW = ''
#RESET = '' # Resets color to default
#
#dateDisplayFormat = "%x"  # Uses the locale date format unless overridden by config
#
#shipDictionary = {}
#
#def main():
#
#        global shipDictionary
#        shipDictionary = getShipDictionary()
#
#        reservationFriendlyNames = {}   # id -> display name (was a list: .get() crashed the moment it was populated)
#        apobj = None
#        print(cruiseLineName + " " + username)
#        session = requests.session()
#        access_token,accountId,session = login(username,password,session,cruiseLineName)
#        getLoyalty(access_token,accountId,session)
#        getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames)
#
#
#
#def login(username,password,session,cruiseLineName):
#    headers = {
#        'Content-Type': 'application/x-www-form-urlencoded',
#        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
#        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
#    }
#
#    urlSafePassword  = quote(password, safe='')
#    # the username must be form-encoded too: a raw '+' (plus-addressed email)
#    # decodes server-side as a space and the login fails as the wrong user
#    data = 'grant_type=password&username=' + quote(username, safe='') +  '&password=' + urlSafePassword + '&scope=openid+profile+email+vdsid'
#
#    response = session.post('https://www.'+cruiseLineName+'.com/auth/oauth2/access_token', headers=headers, data=data)
#
#    if response.status_code != 200:
#        print(cruiseLineName + " Website Might Be Down, username/password incorrect, or have unsupported % symbol in password. Quitting.")
#        quit()
#
#    access_token = response.json().get("access_token")
#
#    list_of_strings = access_token.split(".")
#    string1 = list_of_strings[1]
#    # JWT segments are base64URL: standard b64decode silently DROPS -/_
#    # characters (validate=False), shifting every later byte and crashing the
#    # json parse whenever the payload happens to contain such a byte
#    decoded_bytes = base64.urlsafe_b64decode(string1.replace('+', '-').replace('/', '_') + '==')
#    auth_info = json.loads(decoded_bytes.decode('utf-8'))
#    accountId = auth_info["sub"]
#    return access_token,accountId,session
#
#
#def getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest, passengerId,passengerName,room, orderCode, orderDate, owner):
#
#    headers = {
#    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
#    'Accept': 'application/json',
#    'Accept-Language': 'en-US,en;q=0.5',
#    'X-Requested-With': 'XMLHttpRequest',
#    'Access-Token': access_token,
#    'AppKey': appKey,
#    'vds-id': accountId,
#    'Account-Id': accountId,
#    'channel': 'web',
#    'Req-App-Id': 'Royal.Web.PlanMyCruise',
#    'Req-App-Vers': '1.81.3',
#    'Content-Type': 'application/json',
#    'Origin': 'https://www.' + cruiseLineName + '.com',
#    'DNT': '1',
#    'Sec-GPC': '1',
#    'Connection': 'keep-alive',
#    'Referer': 'https://www.' + cruiseLineName + '.com/',
#    'Sec-Fetch-Dest': 'empty',
#    'Sec-Fetch-Mode': 'cors',
#    'Sec-Fetch-Site': 'cross-site',
#    'Priority': 'u=0',
#    # Requests doesn't support trailers
#    # 'TE': 'trailers',
#    }
#
#    params = {
#        'sailingId': ship + startDate,
#        'currencyIso': currency,
#        'categoryId': prefix,
#    }
#
#
#    json_data = {
#        'productCode': product,
#        'quantity': quantity,
#        'signOnReservationId': reservationId,
#        'signOnPassengerId': passengerId,
#        'guests': [
#            {
#                'id': passengerId,
#                'firstName': guest.get("firstName"),
#                'selected': False,
#                'dob': guest.get("dob"),
#                'reservationId': reservationId,
#                'attachedToReservation': False,
#            },
#        ],
#        'offeringId': product,
#    }
#
#    response = requests.post(
#        'https://aws-prd.api.rccl.com/en/' + apiBrand + '/web/commerce-api/cart/v1/price',
#        params=params,
#        headers=headers,
#        json=json_data,
#    )
#
#    payload = response.json().get("payload")
#    #print('response')
#    if payload is None:
#        print("Payload Not Returned")
#        return
#
#    unitType = payload.get("prices")[0].get("unitType")
#
#    if unitType in [ 'perNight', 'perDay' ]:
#        price = payload.get("prices")[0].get("promoDailyPrice")
#    else:
#        price = payload.get("prices")[0].get("promoPrice")
#
#    print("Paid Price: " + str(paidPrice) + " Cart Price: " + str(price))
#
#def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,guestAgeString,passengerName,room, orderCode, orderDate, owner):
#
#    headers = {
#        'Access-Token': access_token,
#        'AppKey': appKey,
#        'vds-id': accountId,
#    }
#
#    if currencyOverride != "":
#        currency = currencyOverride
#
#    params = {
#        'reservationId': reservationId,
#        'startDate': startDate,
#        'currencyIso': currency,
#        'passengerId': passengerId,
#    }
#
#    response = session.get(
#        'https://aws-prd.api.rccl.com/en/' + apiBrand + '/web/commerce-api/catalog/v2/' + ship + '/categories/' + prefix + '/products/' + str(product),
#        params=params,
#        headers=headers,
#    )
#
#    payload = response.json().get("payload")
#    if payload is None:
#        return
#
#    title = payload.get("title")
#    variant = ""
#    try:
#        variant = payload.get("baseOptions")[0].get("selected").get("variantOptionQualifiers")[0].get("value")
#    except:
#        pass
#
#    if "Bottles" in variant:
#        title = title + " (" + variant + ")"
#
#    newPricePayload = payload.get("startingFromPrice")
#
#    if newPricePayload is None:
#        tempString = YELLOW + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + " (No Longer for Sale)" + RESET
#        print(tempString)
#        return
#
#    # This should pull correct infant, child, or adult price
#    currentPrice = newPricePayload.get(guestAgeString + "PromotionalPrice")
#    if not currentPrice:
#        currentPrice = newPricePayload.get(guestAgeString + "ShipboardPrice")
#    # Infant price is often None, this just sets to 0 to avoid error
#    # Should never happen since should not check prices that are 0 to begin with
#    if not currentPrice:
#        currentPrice = 0
#
#    if currentPrice < paidPrice:
#        text = passengerName + ": Rebook! " + title + " Price is lower: " + str(currentPrice) + " than " + str(paidPrice)
#
#        promoDescription = payload.get("promoDescription")
#        if promoDescription:
#            promotionTitle = promoDescription.get("displayName")
#            text += '\n Promotion:' + promotionTitle
#
#        text += '\n' + 'Cancel Order ' + orderDate + ' ' + orderCode + ' at https://www.' + cruiseLineName + '.com/account/cruise-planner/order-history?bookingId=' + reservationId + '&shipCode=' + ship + "&sailDate=" + startDate
#
#        if not owner:
#            text += " " + "This was booked by another in your party. They will have to cancel/rebook for you!"
#
#        print(RED + text + RESET)
#
#    else:
#        tempString = GREEN + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + RESET
#        if currentPrice > paidPrice:
#            tempString += " (now " + str(currentPrice) + ")"
#        print(tempString)
#
#
#
#def getLoyalty(access_token,accountId,session):
#
#    headers = {
#        'Access-Token': access_token,
#        'AppKey': appKey,
#        'account-id': accountId,
#    }
#    response = session.get('https://aws-prd.api.rccl.com/en/' + apiBrand + '/web/v1/guestAccounts/loyalty/info', headers=headers)
#
#    loyalty = response.json().get("payload").get("loyaltyInformation")
#    cAndANumber = loyalty.get("crownAndAnchorId")
#    cAndALevel = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
#    cAndAPoints = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints")
#    cAndASharedPoints = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints")
#    if cAndANumber is not None and cAndASharedPoints is not None and cAndASharedPoints > 0:
#        print("C&A: " + str(cAndANumber) + " " + cAndALevel + " - " + str(cAndASharedPoints) + " Shared Points (" + str(cAndAPoints) + " Individual Points)")
#
#    clubRoyaleLoyaltyIndividualPoints = loyalty.get("clubRoyaleLoyaltyIndividualPoints")
#    if clubRoyaleLoyaltyIndividualPoints is not None and clubRoyaleLoyaltyIndividualPoints > 0:
#        clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier")
#        print("Casino: " + clubRoyaleLoyaltyTier + " - " + str(clubRoyaleLoyaltyIndividualPoints) + " Points")
#
#
#def getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames):
#
#    headers = {
#        'Access-Token': access_token,
#        'AppKey': appKey,
#        'vds-id': accountId,
#    }
#
#    if cruiseLineName == "royalcaribbean":
#        brandCode = "R"
#    else:
#        brandCode = "C"
#
#    params = {
#        'brand': brandCode,
#        'includeCheckin': 'false',
#    }
#
#    response = requests.get(
#        'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/' + accountId,
#        params=params,
#        headers=headers,
#    )
#
#    for booking in response.json().get("payload").get("profileBookings"):
#        reservationId = booking.get("bookingId")
#        passengerId = booking.get("passengerId")
#        sailDate = booking.get("sailDate")
#        numberOfNights = booking.get("numberOfNights")
#        shipCode = booking.get("shipCode")
#        guests = booking.get("passengers")
#
#        passengerNames = ""
#        for guest in guests:
#            firstName = guest.get("firstName").capitalize()
#            passengerNames += firstName + ", "
#
#        passengerNames = passengerNames.rstrip()
#        passengerNames = passengerNames[:-1]
#
#        reservationDisplay = str(reservationId)
#        # Use friendly name if available
#        if str(reservationId) in reservationFriendlyNames:
#            reservationDisplay += " (" + reservationFriendlyNames.get(str(reservationId)) + ")"
#        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
#        # GTY bookings have stateroomNumber null; a brand-new ship may not be
#        # in the fleet dictionary yet - neither should crash the listing
#        print(reservationDisplay + ": " + sailDateDisplay + " " + shipDictionary.get(shipCode, shipCode) + " Room " + str(booking.get("stateroomNumber") or "GTY") + " (" + passengerNames + ")")
#        if booking.get("balanceDue") is True:
#            print(YELLOW + reservationDisplay + ": " + "Remaining Cruise Payment Balance is " + str(booking.get("balanceDueAmount")) + RESET)
#
#        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj)
#        print(" ")
#
#
#
#def getOrders(access_token,accountId,session,reservationId,passengerId,ship,startDate,numberOfNights,apobj):
#
#    headers = {
#        'Access-Token': access_token,
#        'AppKey': appKey,
#        'Account-Id': accountId,
#    }
#
#    if currencyOverride != "":
#        currency = currencyOverride
#    else:
#        currency = "USD"
#
#    params = {
#        'passengerId': passengerId,
#        'reservationId': reservationId,
#        'sailingId': ship + startDate,
#        'currencyIso': currency,
#        'includeMedia': 'false',
#    }
#
#    response = requests.get(
#        'https://aws-prd.api.rccl.com/en/' + apiBrand + '/web/commerce-api/calendar/v1/' + ship + '/orderHistory',
#        params=params,
#        headers=headers,
#    )
#
#    # Check for my orders and orders others booked for me
#    # either order array can be missing/null, and an error body has no payload
#    # at all - none of those should crash the whole run
#    payload = (response.json() or {}).get("payload") or {}
#    for order in (payload.get("myOrders") or []) + (payload.get("ordersOthersHaveBookedForMe") or []):
#        orderCode = order.get("orderCode")
#
#        # Match Order Date with Website (assuming Website follows locale)
#        date_obj = datetime.strptime(order.get("orderDate"), "%Y-%m-%d")
#        orderDate = date_obj.strftime(dateDisplayFormat)
#        owner = order.get("owner")
#
#        # Only get Valid Orders That Cost Money
#        if order.get("orderTotals").get("total") > 0:
#
#            # Get Order Details
#            response = requests.get(
#                'https://aws-prd.api.rccl.com/en/' + apiBrand + '/web/commerce-api/calendar/v1/' + ship + '/orderHistory/' + orderCode,
#                params=params,
#                headers=headers,
#            )
#            if response.json() is None or response.json().get("payload") is None:
#                continue
#
#            for orderDetail in response.json().get("payload").get("orderHistoryDetailItems"):
#                # check for canceled status at item-level
#
#                quantity = orderDetail.get("priceDetails").get("quantity")
#                order_title = orderDetail.get("productSummary").get("title")
#
#                # API Change on 6 Feb 2026 - Properly handle variants
#                # I do the except just as a precaution
#                try:
#                    product = orderDetail.get("productSummary").get("baseOptions")[0].get("selected").get("code")
#                except:
#                    product = orderDetail.get("productSummary").get("defaultVariantId")
#
#                prefix = orderDetail.get("productSummary").get("productTypeCategory").get("id")
#
#                salesUnit = orderDetail.get("productSummary").get("salesUnit")
#                guests = orderDetail.get("guests")
#
#                for guest in guests:
#
#                    if guest.get("orderStatus") == "CANCELLED":
#                        continue
#
#                    paidPrice = guest.get("priceDetails").get("subtotal")
#                    paidQuantity = guest.get("priceDetails").get("quantity")
#
#                    if paidPrice == 0:
#                        continue
#
#                    passengerId = guest.get("id")
#                    firstName = guest.get("firstName").capitalize()
#                    reservationId = guest.get("reservationId")
#                    guestAgeString = guest.get("guestType").lower()
#
#                    # Skip if item checked already
#                    newKey = passengerId + reservationId + prefix + product
#                    if newKey in foundItems:
#                        continue
#                    foundItems.append(newKey)
#
#                    # New Per Day Logic From cyntil8 fork
#                    if salesUnit in [ 'PER_NIGHT', 'PER_DAY' ]:
#                        paidPrice = round(paidPrice / numberOfNights,2)
#
#                    if paidQuantity > 0:
#                        paidPrice = round(paidPrice / paidQuantity,2)
#
#                    currency = guest.get("priceDetails").get("currency")
#                    room = guest.get("stateroomNumber")
#                    #getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest,passengerId,firstName,room,orderCode,orderDate,owner)
#
#                    getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,guestAgeString,firstName,room,orderCode,orderDate,owner)
#
## Unused Functions
## For Future Capability
#
## Get List of Ships From API
#def getShips():
#
#    headers = {
#        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
#        'accept': 'application/json',
#        'appversion': '1.54.0',
#        'accept-language': 'en',
#        'user-agent': 'okhttp/4.10.0',
#    }
#
#    params = {
#        'sort': 'name',
#    }
#
#    response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)
#
#    shipCodes = []
#    ships = response.json().get("payload").get("ships")
#    for ship in ships:
#        shipCode = ship.get("shipCode")
#        shipCodes.append(shipCode)
#        name = ship.get("name")
#        classificationCode = ship.get("classificationCode")
#        brand = ship.get("brand")
#        print(shipCode + " " + name)
#    return shipCodes
#
#def getShipDictionary():
#
#    headers = {
#        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
#        'accept': 'application/json',
#        'appversion': '1.54.0',
#        'accept-language': 'en',
#        'user-agent': 'okhttp/4.10.0',
#    }
#
#    params = {
#        'sort': 'name',
#    }
#
#    response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)
#    ships = response.json().get("payload").get("ships")
#
#    shipCodes = {}
#
#    for ship in ships:
#        shipCode = ship.get("shipCode")
#        name = ship.get("name")
#        shipCodes[shipCode] = name
#
#    shipCodes["HE"] = "Hero of the Seas"
#    return shipCodes
#
## Get SailDates From a Ship Code
#def getSailDates(shipCode):
#    headers = {
#        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
#        'accept': 'application/json',
#        'appversion': '1.54.0',
#        'accept-language': 'en',
#        'user-agent': 'okhttp/4.10.0',
#    }
#
#    params = {
#        'resultSet': '100',
#    }
#
#
#    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/ships/' + shipCode + '/voyages', params=params, headers=headers)
#    voyages = response.json().get("payload").get("voyages")
#
#    sailDates = []
#    for voyage in voyages:
#        sailDate = voyage.get("sailDate")
#        sailDates.append(sailDate)
#        voyageDescription = voyage.get("voyageDescription")
#        voyageId = voyage.get("voyageId")
#        voyageCode = voyage.get("voyageCode")
#        print(sailDate + " " + voyageDescription)
#
#    return sailDates
#
## Get Available Products from shipcode and saildate
#def getProducts(shipCode, sailDate):
#
#    headers = {
#        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
#        'accept': 'application/json',
#        'appversion': '1.54.0',
#        'accept-language': 'en',
#        'user-agent': 'okhttp/4.10.0',
#    }
#
#    params = {
#        'sailingID': shipCode + sailDate,
#        'offset': '0',
#        'availableForSale': 'all',
#    }
#
#    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/products', params=params, headers=headers)
#
#    products = response.json().get("payload").get("products")
#    for product in products:
#        productTitle = product.get("productTitle")
#        startingFromPrice = product.get("startingFromPrice")
#
#        availableForSale = product.get("availableForSale")
#        if not startingFromPrice or not availableForSale:
#            continue
#
#        adultPrice = startingFromPrice.get("adultPrice")
#        print(productTitle + " " + str(adultPrice))
#
#def getRoyalUp(access_token,accountId,cruiseLineName,session,apobj):
#    # Unused, need javascript parsing to see offer
#    # Could notify when Royal Up is available, but not too useful.
#    headers = {
#        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
#        'Accept': 'application/json',
#        'Accept-Language': 'en-US,en;q=0.5',
#        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
#        'X-Requested-With': 'XMLHttpRequest',
#        'AppKey': 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm',
#        'Access-Token': access_token,
#        'vds-id': accountId,
#        'Account-Id': accountId,
#        'X-Request-Id': '67e0a0c8e15b1c327581b154',
#        'Req-App-Id': 'Royal.Web.PlanMyCruise',
#        'Req-App-Vers': '1.73.0',
#        'Content-Type': 'application/json',
#        'Origin': 'https://www.'+cruiseLineName+'.com',
#        'DNT': '1',
#        'Sec-GPC': '1',
#        'Connection': 'keep-alive',
#        'Referer': 'https://www.'+cruiseLineName+'.com/',
#        'Sec-Fetch-Dest': 'empty',
#        'Sec-Fetch-Mode': 'cors',
#        'Sec-Fetch-Site': 'cross-site',
#        'Priority': 'u=0',
#        # Requests doesn't support trailers
#        # 'TE': 'trailers',
#    }
#
#    response = requests.get('https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/upgrades', headers=headers)
#    for booking in response.json().get("payload"):
#        print( booking.get("bookingId") + " " + booking.get("offerUrl") )
#
#
#if __name__ == "__main__":
#    main()
#

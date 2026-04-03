import requests
import yaml
from apprise import Apprise
from datetime import datetime,date, timedelta, timezone
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, quote
import re
import base64
import json
import argparse
import locale
import sys
import traceback
import time

appKey = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"

currencyOverride = ""
minimumSavingAlert = None

foundItems = []

#RED = '\033[91m'
#GREEN = '\033[92m'
RED = '\033[1;31;40m'
GREEN = '\033[1;32m'
YELLOW = '\033[33m'
RESET = '\033[0m' # Resets color to default

dateDisplayFormat = "%x"  # Uses the locale date format unless overridden by config

shipDictionary = {}

class Logger(object):
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"
        self.log.write(delimiter)
        self.log.flush()

    def write(self, message):
        self.terminal.write(message)
        # Remove ANSI color codes so the text file is readable, not filled with \033
        clean_message = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', message)
        self.log.write(clean_message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def get_config_path():
    parser = argparse.ArgumentParser(description="Check Royal Caribbean Price")
    parser.add_argument('-c', '--config', type=str, default='config.yaml', help='Path to configuration YAML file (default: config.yaml)')
    args = parser.parse_args()
    return args.config

def build_apprise_from_config(config_path):
    apobj = Apprise()
    notify_on_error = False
    try:
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file) or {}
            notify_on_error = bool(data.get('notifyOnError', False))
            if 'apprise' in data:
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
    
    apobj = Apprise()
    try:    
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file)
            if 'dateDisplayFormat' in data:
                global dateDisplayFormat
                dateDisplayFormat = data['dateDisplayFormat']

            if 'logFile' in data:
                logFile = data['logFile']
                print(f"Logging run to file: {logFile}")
                sys.stdout = Logger(logFile)
                sys.stderr = sys.stdout
                
            print("Report generated " + timestamp.strftime(dateDisplayFormat + " %X"))
            
            if 'apprise' in data:
                for apprise in data['apprise']:
                    url = apprise['url']
                    apobj.add(url)

            if 'apprise_test' in data and data['apprise_test']:
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

            global shipDictionary
            shipDictionary = getShipDictionary()
            
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

                    print(f"\nChecking {friendlyCruiseLine} for user {username}")
                    session = requests.session()
                    global foundItems # Clear found items between accounts
                    foundItems = [] # Clear found items between accounts
                    access_token,accountId,session = login(username,password,session,cruiseLineName)
                    stateFromProfile, loyaltyNumber = getProfile(access_token,accountId,session)
                    if state is None:
                        state = stateFromProfile
                        
                    discountFlags = [loyaltyNumber, state, senior, military, police]
                    getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid,showPromos,discountFlags)
                
                    if numAccounts > 1:
                        session.close()
                        print("Sleeping for 30 seconds to allow API to cool down between accounts")
                        time.sleep(30)
                
            if 'cruises' in data:
                for cruises in data['cruises']:
                        cruiseURL = cruises['cruiseURL'] 
                        paidPrice = cruises['paidPrice']
                        session = requests.session()
                        get_cruise_price(cruiseURL, session, paidPrice, apobj, False, None,None)
                        
    except FileNotFoundError:
        print("No Configuration File Found")
        print("Would You like me to make a barebones file for you?")
        user_input = input("Enter y if want me to make the file: ")
        if user_input == "y":
            url = 'https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/SAMPLE-SIMPLE-config.yaml'
            response = requests.get(url)
            response.raise_for_status() 
            with open("config.yaml", "wb") as f:
                f.write(response.content)
            print("File in current directory. Edit Username/password then run tool again")

def string_to_float(s: str) -> float:
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

def aboveAgeOnSailDate(birthDate, sailDate,ageThreshold):
    dt1 = datetime.strptime(birthDate, "%Y%m%d")
    dt2 = datetime.strptime(sailDate, "%Y%m%d")
    
    age = dt2.year - dt1.year
    # Adjust if birthday hasn’t happened yet this year
    if (dt2.month, dt2.day) < (dt1.month, dt1.day):
        age -= 1
    
    return age >= ageThreshold

def days_between(d1, d2):
    dt1 = datetime.strptime(d1, "%Y%m%d")
    dt2 = datetime.strptime(d2, "%Y%m%d")
    return (dt2 - dt1).days

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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
    }
    
    urlSafePassword  = quote(password, safe='')
    data = f'grant_type=password&username={username}&password={urlSafePassword}&scope=openid+profile+email+vdsid'
    
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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    'X-Requested-With': 'XMLHttpRequest',
    'Access-Token': access_token,
    'AppKey': appKey,
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
    
def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,guestAgeString,passengerName,room, orderCode, orderDate, owner, forWatch, cruiseLineName, salesUnit=None, numberOfNights=None):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
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
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    payload = response.json().get("payload")
    if payload is None:
        print(f"{prefix} not available for passenger")
        return

    title = payload.get("title")    
    variant = ""
    try:
        variant = payload.get("baseOptions")[0].get("selected").get("variantOptionQualifiers")[0].get("value")
    except:
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
            tempString = YELLOW + f"\t{passengerName.ljust(10)} not available or already booked" + RESET
            
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
        'AppKey': appKey,
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
        print(f"\tCasino Tier: {clubRoyaleLoyaltyTier} - {clubRoyaleLoyaltyIndividualPoints} Points")

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
        print(f"\tBlue Chip Tier: {clubRoyaleLoyaltyTier} - {celebrityBlueChipLoyaltyIndividualPoints} Points")

    return loyaltyNumber

def getNumberOfNights(access_token,accountId,session,loyaltyNumber):

    # Set to -1 as this API call sometimes fails
    # But not worth quiting over
    totalNights = -1
    totalTrips = -1
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'account-id': accountId,
    }

    params = {
        'loyaltyNumber': loyaltyNumber,
    }

    response = requests.get(
        'https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/loyalty/history/summary',
        params=params,
        headers=headers,
    )
    
    payload = response.json().get("payload")
    if payload is not None:
        totalNights = payload.get("totalNights","-1")
        totalTrips = payload.get("totalTrips","-1")
    
    return totalNights, totalTrips
    
def getProfile(access_token,accountId,session):

    loyaltyNumber = None
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
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
            print(f"\tTotal Trips: {totalTrips} - Total Nights: {totalNights}")
    
    clubRoyaleLoyaltyIndividualPoints = loyalty.get("clubRoyaleLoyaltyIndividualPoints")
    if clubRoyaleLoyaltyIndividualPoints is not None and clubRoyaleLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier")
        print(f"\tCasino Tier: {clubRoyaleLoyaltyTier} - {clubRoyaleLoyaltyIndividualPoints} Points")

    captainsClubId = loyalty.get("captainsClubId")
    if captainsClubId is not None:
        captainsClubLoyaltyTier = loyalty.get("captainsClubLoyaltyTier")
        captainsClubLoyaltyIndividualPoints = loyalty.get("captainsClubLoyaltyIndividualPoints")
        captainsClubLoyaltyRelationshipPoints = loyalty.get("captainsClubLoyaltyRelationshipPoints")
        print(f"\tCaptain's Club Number: {captainsClubId} {captainsClubLoyaltyTier} TIER ({captainsClubLoyaltyRelationshipPoints} Shared Points, {captainsClubLoyaltyIndividualPoints} Individual Points)")
        loyaltyNumber = captainsClubId
        totalNights, totalTrips = getNumberOfNights(access_token,accountId,session,loyaltyNumber)
        if totalNights > 0:
            print(f"\tTotal Trips: {totalTrips} - Total Nights: {totalNights}")
        print("Using Captains Club Id To Check Cruise Prices")

    celebrityBlueChipLoyaltyIndividualPoints = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints")
    if celebrityBlueChipLoyaltyIndividualPoints is not None and celebrityBlueChipLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        print(f"\tBlue Chip Tier: {clubRoyaleLoyaltyTier} - {celebrityBlueChipLoyaltyIndividualPoints} Points")

    return state, loyaltyNumber

def getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid,showPromos,discountFlags):

    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'vds-id': accountId,
    }
    
    if cruiseLineName == "royalcaribbean":
        brandCode = "R"
    else:
        brandCode = "C"
        
    params = {
        'brand': brandCode,
        'includeCheckin': 'false',
    }

    try:
        response = requests.get(
            f'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/{accountId}',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    for booking in response.json().get("payload").get("profileBookings"):
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
        stateroomTypeName = "NONE"
        
        if stateroomType == "I":
            stateroomTypeName = "INTERIOR"
        if stateroomType == "O":
            stateroomTypeName = "OUTSIDE"
        if stateroomType == "B":
            stateroomTypeName = "BALCONY"
        if stateroomType == "D":
            stateroomTypeName = "DELUXE"
        if stateroomType == "C":
            stateroomTypeName = "CONCIERGE"    
            
        passengerNames = ""
        numberOfPassengers = 0
        numberOfChildren = 0
        numberOfAdults = 0
        haveASenior = False
        
        checkinString = ""
        
        for guest in guests:
            stateroomCategoryCode = guest.get("stateroomCategoryCode")
            stateroomSubtype = booking.get("stateroomSubtype")
            
            # Work around for Celebrity Concierge GTY which does not return this info
            if stateroomCategoryCode is None and stateroomSubtype is None:
                stateroomCategoryCode = "XC"
                stateroomSubtype = "XC"
                if displayCruisePrices:
                    print(YELLOW + "Data is missing from API. Cruise Price Check May Not Work - Use Manual Method" + RESET)
                
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
                boarding_hour = guest.get("arrivalTime")[9:11]
                boarding_min = guest.get("arrivalTime")[11:13]
                if checkinString == "":
                    checkinString = f"{firstName} Boarding Time {boarding_hour}:{boarding_min}"
                else:
                    checkinString += f", {firstName} Boarding Time {boarding_hour}:{boarding_min}"
                
        passengerNames = passengerNames.rstrip()
        passengerNames = passengerNames[:-1]

        reservationDisplay = f"Reservation #{reservationId}"
        # Use friendly name if available
        if str(reservationId) in reservationFriendlyNames:
            reservationDisplay += f" ({reservationFriendlyNames.get(str(reservationId))})"
        print(f"\n{reservationDisplay}")
        
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        print(f"{sailDateDisplay} {shipDictionary[shipCode]} Room {stateroomNumber} (In this cabin: {passengerNames})")
        
        # Print Boarding Info or provide check in information
        if checkinString != "":
            print(checkinString)
        else:
            GetCheckinInfo(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,apobj)
        
        
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
    
            [loyaltyNumber, state, senior, military, police] = discountFlags
            # Override if booking says will have a senior
            if senior == "n" and haveASenior:
                senior = "y"
                
            urlSailDate = f"{sailDate[0:4]}-{sailDate[4:6]}-{sailDate[6:8]}"
            
            if stateroomNumber == "GTY": #GTY Room needs a different URL
                cruisePriceURL = f"https://www.{cruiseLineName}.com/checkout/add-ons?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0b=n&r0r={police}&r0s=n&r0q={military}&r0t={senior}&r0D=y&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0g=BESTRATE&r0h=n&r0C=y"
            else:
                cruisePriceURL = f"https://www.{cruiseLineName}.com/room-selection/room-location?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0b=n&r0r={police}&r0s=n&r0q={military}&r0t={senior}&r0D=y"
                
                
            paidPrice = None
            #print(cruisePriceURL)
            if str(reservationId) in reservationPricePaid:
                paidPrice = reservationPricePaid.get(str(reservationId))
            
            if stateroomType != "NONE":
                get_cruise_price(cruisePriceURL, session, paidPrice, apobj, True, finalPaymentDate, loyaltyNumber, state)
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
        'AppKey': appKey,
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
        response = requests.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/{ship}/orderHistory',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)
 
    if response.status_code != 200:
        print(f"Error getting voyage information (returned error code {response.status_code}). Try again later.\nQuitting.")
        sys.exit(1)

    # Check for my orders and orders others booked for me
    for order in response.json().get("payload").get("myOrders") + response.json().get("payload").get("ordersOthersHaveBookedForMe"):
        orderCode = order.get("orderCode")

        # Match Order Date with Website (assuming Website follows locale)
        date_obj = datetime.strptime(order.get("orderDate"), "%Y-%m-%d")
        orderDate = date_obj.strftime(dateDisplayFormat)
        owner = order.get("owner")
            
        # Only get Valid Orders That Cost Money
        if order.get("orderTotals").get("total") > 0: 
            
            # Get Order Details
            try:
                response = requests.get(
                    f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/{ship}/orderHistory/{orderCode}',
                    params=params,
                    headers=headers,
                )
            except Exception as e:
                print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
                sys.exit(1)
            
            response = requests.get(
                f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/{ship}/orderHistory/{orderCode}',
                params=params,
                headers=headers,
            )
            
            if response.json() is None or response.json().get("payload") is None:
                continue
                
            for orderDetail in response.json().get("payload").get("orderHistoryDetailItems"):
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
                except:
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
                    foundItems.append(newKey)
                    
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
    
    refundable = params.get("r0u",["XXX"])[0] != "XXX"
    travelInsurance = params.get("r0n",["n"])[0] != "n"
    prepaidGrats = params.get("r0m",["n"])[0] != "n"
    
    senior = params.get("r0t",["n"])[0] == "y"
    military = params.get("r0q",["n"])[0] == "y"
    police = params.get("r0r",["n"])[0] == "y"
    fire = params.get("r0s",["n"])[0] 
    
    return isRoyal,sailDate,currencyCode,bookingOfficeCountryCode,shipCode,cabinClassString,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,packageCode,numberOfAdults,numberOfChildren,loyaltyNumber,username,state,refundable,travelInsurance,prepaidGrats,senior,military,police,fire
    

def get_cruise_price(url, session, paidPrice, apobj, automaticURL,finalPaymentDate,loyaltyNumber=None,state=None):
    
    isRoyal,sailDate,currencyCode,bookingOfficeCountryCode,shipCode,cabinClassString,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,packageCode,numberOfAdults,numberOfChildren,loyaltyNumber,username,state,refundable,travelInsurance,prepaidGrats,senior,military,police,fire = parseProvidedURL(url)
    roomNumber = None

    results = getRoomPriceViaAPI(isRoyal,bookingOfficeCountryCode,packageCode,sailDate,currencyCode,stateroomTypeName,stateroomSubtype,stateroomCategoryCode,roomNumber,loyaltyNumber,state,fire,military,police,senior,numberOfAdults,numberOfChildren)
    
    roomIsFound = results != {}
    numberOfNights = results.get("sailingNights")
    shipName = shipDictionary.get(shipCode)

    sailDateDisplay = datetime.strptime(sailDate, "%Y-%m-%d").strftime(dateDisplayFormat) 
    
    preString = f"{sailDateDisplay} {shipName} {cabinClassString} {stateroomCategoryCode}"
    
    paidPriceLetters = ""
    if paidPrice is not None and isinstance(paidPrice, str):
        # Use G,I,R as keys in the price
        paidPriceLetters = "".join([char for char in paidPrice if char.isalpha()])
        # Remove letters from price
        paidPrice = re.sub(r'[a-zA-Z]', '', paidPrice)
        # make a float as it now only has numbers
        paidPrice = float(paidPrice)
    
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
    if usedDiscounts != "":
       preString = preString + " (" + usedDiscounts[:-2] + " Discount)"
    
    # Add discount addon information into string to print
    addons = ""
    
    refundNotFound = False
    
    if roomIsFound:
        fareStruct = results.get("baseFare")
        price = fareStruct.get("fare")
        grats = fareStruct.get("gratuities")
        ins = fareStruct.get("insurance")
        obc = fareStruct.get("obc")
        
        # Save this for later
        basePrice = price
        baseGrats = grats
        baseIns = ins
        
        desireRefundPrice = False
        if refundable or "R" in paidPriceLetters:
           desireRefundPrice = True
           addons += "Refundable Deposit, "
           fareStruct = results.get("refundFare")
           if fareStruct is not None:
               price = fareStruct.get("fare")
               grats = fareStruct.get("gratuities")
               ins = fareStruct.get("insurance")
               obc = fareStruct.get("obc")
           else:
               refundNotFound = True
           
        if travelInsurance or "I" in paidPriceLetters:
           addons += "Travel Protection, "
           price += ins
           basePrice += baseIns
        if prepaidGrats or "G" in paidPriceLetters:
           addons += "Prepaid grats, "
           price += grats
           basePrice += baseGrats
       
        # Strip last ,
        if addons != "":
            preString = preString + " (" + addons[:-2] + ")"  
    
    if finalPaymentDate is None:
        finalPaymentDate = getFinalPaymentDate(numberOfNights,sailDate.replace('-', ''))
        
    finalPaymentDateDisplay = finalPaymentDate.strftime(dateDisplayFormat)
    pastFinalPaymentDate = date.today() > finalPaymentDate
    
    if not roomIsFound:
        textString = f"{preString} Not For Sale"
        if automaticURL and pastFinalPaymentDate:
            textString += ". Past Final Payment Date of " + finalPaymentDateDisplay
            
        print(YELLOW + textString + RESET)
        
        # If you specified the URL, provide a notification to update the URL
        if not automaticURL:
            apobj.notify(body=textString, title='Cruise Room Not Available')
        
        # If cruise room not available, print other room prices
        # Only do this for watchlist rooms
        if packageCode and not automaticURL:
            GetCruisePriceFromAPI(currencyCode, packageCode, sailDate, cabinClassString, numberOfAdults, numberOfChildren)
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
                textString += f" not including {obcString} OBC"
            textString += f" is lower than {paidPrice}"
            
            if minimumSavingAlert is not None and saving < minimumSavingAlert:
                textString += f" (Saving {saving} < minimumSavingAlert {minimumSavingAlert}; no notification sent)"
                print(YELLOW + textString + RESET)
            else:
                print(RED + textString + RESET)
                apobj.notify(body=textString, title='Cruise Price Alert')

        # Don't notify if rebooking not possible
        if  automaticURL and pastFinalPaymentDate:
            textString = f"Past Final Payment Date of {finalPaymentDateDisplay} : {preString} New price of {price} {currencyCode}"
            if obcValue > 0:
                textString += f" not including {obcString} OBC"
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
                apobj.notify(body=textString, title='Cruise Price Alert')
    else:
        tempString = GREEN + f"{preString}: You have best price of {paidPrice} {currencyCode}" + RESET
        if price > paidPrice:
            tempString += f" (now {price} {currencyCode}"
        if obcValue > 0:
            tempString += f" not including {obcString} OBC"
        tempString += ")"   
        
        if desireRefundPrice and paidPrice > basePrice:
            tempString += f"{YELLOW} Non-Refunable price {basePrice} {currencyCode} is lower than you paid{RESET}"
        if desireRefundPrice:
            tempString += f" Non-refundable price is {basePrice} {currencyCode}"
            
        print(tempString)
        

# Unused Functions
# For Future Capability

# Get List of Ships From API
def getShips():

    headers = {
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'appversion': '1.54.0',
        'accept-language': 'en',
        'user-agent': 'okhttp/4.10.0',
    }

    params = {
        'sort': 'name',
    }

    try:
        response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    shipCodes = []
    ships = response.json().get("payload").get("ships")
    for ship in ships:
        shipCode = ship.get("shipCode")
        shipCodes.append(shipCode)
        name = ship.get("name")
        classificationCode = ship.get("classificationCode")
        brand = ship.get("brand")
        print(f"{shipCode} {name}")
    return shipCodes

def getShipDictionary():

    headers = {
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'appversion': '1.54.0',
        'accept-language': 'en',
        'user-agent': 'okhttp/4.10.0',
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
def getAllPromotions(access_token, accountId, session, ship, startDate, currency):
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'vds-id': accountId,
    }

    base_url = 'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog/v2/promotions/list'
    sailingId = ship + startDate

    def fetchPromos(page):
        resp = session.get(base_url, params={'sailingId': sailingId, 'page': page, 'currencyIso': currency}, headers=headers)
        return resp.json().get("payload") or [] if resp.status_code == 200 else []

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

def GetCruisePriceFromAPI(currency, packageCode, sailDate, bookingType, numAdults, numChildren):

    cookies = {
        'currency': currency,
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
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
                cabinCostPerPerson = float(price["price"]["value"]) * numPassengers
                print(f"\t\t{cabinCostPerPerson} {currency}: Cheapest {cabinType} Price for {numPassengers}" + postString)

def GetOBC(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj,cruiseLineName,currency):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'Account-Id': accountId,
    }
        
    params = {
    'passengerId': passengerId,
    'sailingId': shipCode + sailDate,
    'currencyIso': currency,
    }

    try:
        response = requests.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/cart/v1/obc/reservations/{reservationId}',
            params=params,
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    payload = response.json().get("payload")
    if not payload:
        return
    
    amount = payload.get("amount")
    cur = payload.get("currencyIso")
    
    if amount and amount > 0:
        print(f"\tOnboard Credit of {amount} {cur}")

def GetCheckinInfo(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,apobj):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'Account-Id': accountId,
    }
        
    try:
        response = requests.get(
            f'https://aws-prd.api.rccl.com/en/royal/web/v3/ships/voyages/{shipCode}{sailDate}/enriched',
            headers=headers,
        )
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        sys.exit(1)

    payload = response.json().get("payload")
    if not payload:
        return

    isCheckinAvailable = payload.get("sailingInfo")[0].get("isCheckinAvailable")
    checkWindowOpenStartDateTime = payload.get("sailingInfo")[0].get("checkWindowOpenStartDateTime")
    
    if isCheckinAvailable:
        print(f"{RED}Check In Available and Not Completed{RESET}")
    else:
        dt = datetime.fromisoformat(checkWindowOpenStartDateTime)
        local_dt = dt.astimezone().strftime(dateDisplayFormat + " %X %Z")
        print(f"Check In opens on: {local_dt}")


def checkIfRoomIsAvailable(isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomSubtypeCode,categoryCode,adultCount,childCount):
    
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
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
    
    response = requests.get(
        apiURL,
        params=params,
        headers=headers,
    )

    # Extract json from html
    if isRoyal:
        start_var = "$L34\\\",null,"
    else:
        start_var = "$L33\\\",null,"
        
    end_var = "]}]}]]"
    pattern = rf"{re.escape(start_var)}(.*?){re.escape(end_var)}"
    match = re.search(pattern, response.text)
    if match:
        result = match.group(1)
        # Format text so will load as json. Needs to be done twice
        unescaped = json.loads(f'"{result}"')
        json_data = json.loads(unescaped)
        data = json_data.get("data")
        # Loop through data to see if desired room is available
        stateroomTypes = (data.get("rooms")[0]).get("options").get("stateroomTypes")
        for stateroomType in stateroomTypes:
            stateroomSubtypes = stateroomType.get("stateroomSubtypes")
            for stateroomSubtype in stateroomSubtypes:
                cur_subTypeCode = stateroomSubtype.get("code")
                cur_categoryCode = stateroomSubtype.get("categoryCode")
                #print("Desired: " + stateroomSubtypeCode + " " + categoryCode)
                #print("Cur:     " + cur_subTypeCode + " " + categoryCode)
                if cur_subTypeCode == stateroomSubtypeCode and cur_categoryCode == categoryCode:
                    return True

    return False

def getRoomPriceViaAPI(isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomTypeCode,stateroomSubtypeCode,categoryCode,roomNumber,loyaltyNumber,stateCode,fireFighter,military,police,senior,adultCount,childCount):
    
    roomAvailable = checkIfRoomIsAvailable(isRoyal,countryCode,packageId,sailDate,currencyCode,stateroomSubtypeCode,categoryCode,adultCount,childCount)
    
    results = {}
    
    if not roomAvailable:
        #print("Room not available")
        return results
    
    headers = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0',
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    }

    params = ''
    
    
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
                'qualifiers': {
                    #'loyaltyNumber': loyaltyNumber,
                    #'stateCode': stateCode,
                    'fireFighter': fireFighter=="y",
                    'military': military=="y",
                    'police': police=="y",
                    'senior': senior=="y",
                },
                'occupancy': {
                    'adultCount': adultCount,
                    'childCount': childCount,
                },
            },
        ],
    }
    
    if roomNumber is not None:
        json_data['rooms'][0]['roomNumber'] = roomNumber
    if stateCode is not None:
        json_data['rooms'][0]['qualifiers']['qualifiers'] = qualifiers
    if loyaltyNumber is not None:
        json_data['rooms'][0]['qualifiers']['loyaltyNumber'] = loyaltyNumber
    
    if isRoyal:
        apiURL = 'https://www.royalcaribbean.com/checkout/api/v1/rooms/checkout'
    else:
        apiURL = 'https://www.celebritycruises.com/checkout/api/v1/rooms/checkout'    
        
    response = requests.post(apiURL,
        params=params,
        headers=headers,
        json=json_data,
    )
    
    room = response.json().get("rooms")[0]
    baseFare = room.get("baseFare",None)
    sailingNights = response.json().get("sailing").get("itinerary").get("sailingNights")
    results['sailingNights'] = sailingNights
    
    if baseFare is not None:
        base_fare = baseFare.get("pricing").get("amount")
        base_gratuities = baseFare.get("gratuities")
        base_insurance = baseFare.get("insurance")
        obc = baseFare.get("pricing").get("invoice").get("onboardCredits",0)
        results['baseFare'] = {'fare': base_fare,'gratuities': base_gratuities,'insurance': base_insurance,'obc':obc}
        
    baseRefundableFare = room.get("baseRefundableFare",None)
    if baseRefundableFare is not None:
        refund_fare = baseRefundableFare.get("pricing").get("amount")
        refund_gratuities = baseRefundableFare.get("gratuities")
        refund_insurance = baseRefundableFare.get("insurance")
        obc = baseRefundableFare.get("pricing").get("invoice").get("onboardCredits",0)
        results['refundFare'] = {'fare': refund_fare,'gratuities': refund_gratuities,'insurance': refund_insurance,'obc':obc}
   
    return results
    
if __name__ == "__main__":
    config_path = get_config_path()
    apobj, notify_on_error = build_apprise_from_config(config_path)
    try:
        main(config_path)
    except Exception as exc:
        timestamp = datetime.now().strftime(dateDisplayFormat + " %X")
        error_summary = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {error_summary}", file=sys.stderr)
        traceback.print_exc()
        if notify_on_error and len(apobj) > 0:
            body = f"Script failed at {timestamp}\n{error_summary}"
            apobj.notify(body=body, title='Cruise Price Script Error')
        sys.exit(1)
 

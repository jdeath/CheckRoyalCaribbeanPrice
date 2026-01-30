import requests
import yaml
from apprise import Apprise
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import base64
import json
import argparse
import locale

appKey = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"

currencyOverride = ""

foundItems = []

#RED = '\033[91m'
#GREEN = '\033[92m'
RED = '\033[1;31;40m'
GREEN = '\033[1;32m'
YELLOW = '\033[33m'
RESET = '\033[0m' # Resets color to default

dateDisplayFormat = "%x"  # Uses the locale date format unless overridden by config

shipDictionary = {}



def main():
    parser = argparse.ArgumentParser(description="Check Royal Caribbean Price")
    parser.add_argument('-c', '--config', type=str, default='config.yaml', help='Path to configuration YAML file (default: config.yaml)')
    args = parser.parse_args()
    config_path = args.config

    # Set Time with AM/PM or 24h based on locale    
    locale.setlocale(locale.LC_TIME,'')
    timestamp = datetime.now()
    print(" ")
    
    apobj = Apprise()
        
    with open(config_path, 'r') as file:
        data = yaml.safe_load(file)
        if 'dateDisplayFormat' in data:
            global dateDisplayFormat
            dateDisplayFormat = data['dateDisplayFormat']
        
        print(timestamp.strftime(dateDisplayFormat + " %X"))
        
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
            print(YELLOW + "Overriding Current Price Currency to " + currencyOverride + RESET)

        global shipDictionary
        shipDictionary = getShipDictionary()
        
        # Load watch list configuration
        watchListItems = []
        if 'watchList' in data:
            watchListItems = data['watchList']
        
        displayCruisePrices = False
        if 'displayCruisePrices' in data:
            displayCruisePrices = data['displayCruisePrices']
        
        reservationPricePaid = {}
        if 'reservationPricePaid' in data:
            reservationPricePaid=data.get('reservationPricePaid', {})
        
        
        if 'accountInfo' in data:
            for accountInfo in data['accountInfo']:
                username = accountInfo['username']
                password = accountInfo['password']
                if 'cruiseLine' in accountInfo:
                   if accountInfo['cruiseLine'].lower().startswith("c"):
                    cruiseLineName = "celebritycruises"
                   else:
                    cruiseLineName =  "royalcaribbean"
                else:
                   cruiseLineName =  "royalcaribbean"
                    
                print(cruiseLineName + " " + username)
                session = requests.session()
                access_token,accountId,session = login(username,password,session,cruiseLineName)
                getLoyalty(access_token,accountId,session)
                getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid)
    
        if 'cruises' in data:
            for cruises in data['cruises']:
                    cruiseURL = cruises['cruiseURL'] 
                    paidPrice = float(cruises['paidPrice'])
                    get_cruise_price(cruiseURL, paidPrice, apobj, False)
            

def aboveTwelveOnSailDate(birthDate, sailDate):
    dt1 = datetime.strptime(birthDate, "%Y%m%d")
    dt2 = datetime.strptime(sailDate, "%Y%m%d")
    
    age = dt2.year - dt1.year
    # Adjust if birthday hasn’t happened yet this year
    if (dt2.month, dt2.day) < (dt1.month, dt1.day):
        age -= 1
    
    return age >= 12

def days_between(d1, d2):
    dt1 = datetime.strptime(d1, "%Y%m%d")
    dt2 = datetime.strptime(d2, "%Y%m%d")
    return (dt2 - dt1).days
    
def login(username,password,session,cruiseLineName):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
    }
    
    
    data = 'grant_type=password&username=' + username +  '&password=' + password + '&scope=openid+profile+email+vdsid'
    
    response = session.post('https://www.'+cruiseLineName+'.com/auth/oauth2/access_token', headers=headers, data=data)
    
    if response.status_code != 200:
        print(cruiseLineName + " Website Might Be Down, username/password incorrect, or have unsupported % symbol in password. Quitting.")
        quit()
          
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

    response = requests.post(
        'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/cart/v1/price',
        params=params,
        headers=headers,
        json=json_data,
    )
    
    payload = response.json().get("payload")
    #print('response')
    if payload is None:
        print("Payload Not Returned")
        return
        
    unitType = payload.get("prices")[0].get("unitType")
    
    if unitType in [ 'perNight', 'perDay' ]:
        price = payload.get("prices")[0].get("promoDailyPrice")
    else:
        price = payload.get("prices")[0].get("promoPrice")
        
    print("Paid Price: " + str(paidPrice) + " Cart Price: " + str(price))
    
def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,passengerName,room, orderCode, orderDate, owner, forWatch, cruiseLineName):
    
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
    
    response = session.get(
        'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog/v2/' + ship + '/categories/' + prefix + '/products/' + str(product),
        params=params,
        headers=headers,
    )
    
    payload = response.json().get("payload")
    if payload is None:
        return
    
    title = payload.get("title")    
    variant = ""
    try:
        variant = payload.get("baseOptions")[0].get("selected").get("variantOptionQualifiers")[0].get("value")
    except:
        pass
    
    if "Bottles" in variant:
        title = title + " (" + variant + ")"
    
    newPricePayload = payload.get("startingFromPrice")
    
    if newPricePayload is None:
        if not forWatch:
            tempString = YELLOW + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + " (No Longer for Sale)" + RESET
            print(tempString)
        return
        
    currentPrice = newPricePayload.get("adultPromotionalPrice")
    
    if not currentPrice:
        currentPrice = newPricePayload.get("adultShipboardPrice")
    
    if currentPrice < paidPrice:
        if forWatch:
            text = passengerName + ": Book! " + title + " Price is lower: " + str(currentPrice) + " than " + str(paidPrice)
        else:
            text = passengerName + ": Rebook! " + title + " Price is lower: " + str(currentPrice) + " than " + str(paidPrice)
        
        promoDescription = payload.get("promoDescription")
        if promoDescription:
            promotionTitle = promoDescription.get("displayName")
            text += '\n Promotion:' + promotionTitle

        if forWatch:
            text += '\n' + 'Book at https://www.' + cruiseLineName + '.com/account/cruise-planner/category/' + prefix + '/product/' + str(product) + '?bookingId=' + reservationId + '&shipCode=' + ship + '&sailDate=' + startDate
        else:
            text += '\n' + 'Cancel Order ' + orderDate + ' ' + orderCode + ' at https://www.' + cruiseLineName + '.com/account/cruise-planner/order-history?bookingId=' + reservationId + '&shipCode=' + ship + "&sailDate=" + startDate
        
        if not owner:
            text += " " + "This was booked by another in your party. They will have to cancel/rebook for you!"
            
        print(RED + text + RESET)
        apobj.notify(body=text, title='Cruise Addon Price Alert')
    else:
        if forWatch:
            tempString = GREEN + passengerName.ljust(10) + " (" + title +  ") price is higher than watch price: " + str(paidPrice) + RESET
        else:
            tempString = GREEN + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + RESET
        if currentPrice > paidPrice:
            tempString += " (now " + str(currentPrice) + ")"
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
        
        # Skip disabled watchlist items
        if not enabled:
            continue
        
        if not product or not prefix or watchPrice <= 0:
            print(f"    {YELLOW}Skipping {name} - missing required fields{RESET}")
            continue
            
        # Format: [WATCH] Item Name - Passenger (Room): Message
        watchDisplayName = f"[WATCH] {name} - {passengerName} ({room})"
        
        # Set placeholder values for order-specific fields since these aren't actual orders
        getNewBeveragePrice(
            access_token, accountId, session, reservationId, ship, startDate,
            prefix, watchPrice, "USD", product, apobj, passengerId,
            watchDisplayName, room, "WATCH-LIST", "Watch List", True, True, cruiseLineName
        )

def getLoyalty(access_token,accountId,session):

    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'account-id': accountId,
    }
    response = session.get('https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/loyalty/info', headers=headers)

    loyalty = response.json().get("payload").get("loyaltyInformation")
    cAndANumber = loyalty.get("crownAndAnchorId")
    cAndALevel = loyalty.get("crownAndAnchorSocietyLoyaltyTier")
    cAndAPoints = loyalty.get("crownAndAnchorSocietyLoyaltyIndividualPoints")
    cAndASharedPoints = loyalty.get("crownAndAnchorSocietyLoyaltyRelationshipPoints")
    print("C&A: " + str(cAndANumber) + " " + cAndALevel + " - " + str(cAndASharedPoints) + " Shared Points (" + str(cAndAPoints) + " Individual Points)")  
    
    clubRoyaleLoyaltyIndividualPoints = loyalty.get("clubRoyaleLoyaltyIndividualPoints")
    if clubRoyaleLoyaltyIndividualPoints is not None and clubRoyaleLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("clubRoyaleLoyaltyTier")
        print("Casino Royale: " + clubRoyaleLoyaltyTier + " - " + str(clubRoyaleLoyaltyIndividualPoints) + " Points")

    captainsClubId = loyalty.get("captainsClubId")
    if captainsClubId is not None:
        captainsClubLoyaltyTier = loyalty.get("captainsClubLoyaltyTier")
        captainsClubLoyaltyIndividualPoints = loyalty.get("captainsClubLoyaltyIndividualPoints")
        captainsClubLoyaltyRelationshipPoints = loyalty.get("captainsClubLoyaltyRelationshipPoints")
        print("CC: " + str(captainsClubId) + " " + captainsClubLoyaltyTier + " - " + str(captainsClubLoyaltyRelationshipPoints) + " Shared Points (" + str(captainsClubLoyaltyIndividualPoints) + " Individual Points)")
    
    celebrityBlueChipLoyaltyIndividualPoints = loyalty.get("celebrityBlueChipLoyaltyIndividualPoints")
    if celebrityBlueChipLoyaltyIndividualPoints is not None and celebrityBlueChipLoyaltyIndividualPoints > 0:
        clubRoyaleLoyaltyTier = loyalty.get("celebrityBlueChipLoyaltyTier","Unknown")
        print("Blue Chip: " + clubRoyaleLoyaltyTier + " - " + str(celebrityBlueChipLoyaltyIndividualPoints) + " Points")
    
def getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames,watchListItems,displayCruisePrices,reservationPricePaid):

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

    response = requests.get(
        'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/' + accountId,
        params=params,
        headers=headers,
    )

    for booking in response.json().get("payload").get("profileBookings"):
        print(booking)
        reservationId = booking.get("bookingId")
        passengerId = booking.get("passengerId")
        sailDate = booking.get("sailDate")
        numberOfNights = booking.get("numberOfNights")
        shipCode = booking.get("shipCode")
        guests = booking.get("passengers")
        packageCode = booking.get("packageCode")
        bookingCurrency = booking.get("bookingCurrency")
        bookingOfficeCountryCode = booking.get("bookingOfficeCountryCode")
        stateroomType = booking.get("stateroomType")
        stateroomNumber = booking.get("stateroomNumber")
        
        stateroomTypeName = "INTERIOR"
        
        if stateroomType == "I":
            stateroomTypeName = "INTERIOR"
        if stateroomType == "O":
            stateroomTypeName = "OUTSIDE"
        if stateroomType == "B":
            stateroomTypeName = "BALCONY"
        if stateroomType == "D":
            stateroomTypeName = "DELUXE"
            
        passengerNames = ""
        numberOfPassengers = 0
        numberOfChildren = 0
        numberOfAdults = 0
        for guest in guests:
            stateroomCategoryCode = guest.get("stateroomCategoryCode")
            stateroomSubtype = booking.get("stateroomSubtype")
            
            numberOfPassengers = numberOfPassengers + 1
            firstName = guest.get("firstName").capitalize()
            birthDate = guest.get("birthdate")
            
            isAdult = aboveTwelveOnSailDate(birthDate, sailDate)
            
            if isAdult:
                numberOfAdults = numberOfAdults + 1
            else:
                numberOfChildren = numberOfChildren + 1
                
            passengerNames += firstName + ", "
        
        passengerNames = passengerNames.rstrip()
        passengerNames = passengerNames[:-1]

        reservationDisplay = str(reservationId)
        # Use friendly name if available
        if str(reservationId) in reservationFriendlyNames:
            reservationDisplay += " (" + reservationFriendlyNames.get(str(reservationId)) + ")"
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        print(reservationDisplay + ": " + sailDateDisplay + " " + shipDictionary[shipCode] + " Room " + stateroomNumber + " (" + passengerNames + ")")
        
        
        # Print Current Prices
        if displayCruisePrices:
            #GetCruisePriceFromAPI(bookingCurrency, packageCode, sailDate,stateroomType, numberOfAdults, numberOfChildren)
        
            urlSailDate = f"{sailDate[0:4]}-{sailDate[4:6]}-{sailDate[6:8]}"
       
            if stateroomNumber == "GTY": #GTY Room needs a different URL
                cruisePriceURL = f"https://www.{cruiseLineName}.com/checkout/add-ons?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0D=y&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0g=BESTRATE&r0h=n&r0C=y"
            else:
                cruisePriceURL = f"https://www.{cruiseLineName}.com/room-selection/room-location?packageCode={packageCode}&sailDate={urlSailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={bookingCurrency}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0D=y"
                
            paidPrice = None
            #print(cruisePriceURL)
            if str(reservationId) in reservationPricePaid:
                paidPrice = float(reservationPricePaid.get(str(reservationId)))
                
            get_cruise_price(cruisePriceURL, paidPrice, apobj, True, 0)
        
        if booking.get("balanceDue") is True:
            print(YELLOW + reservationDisplay + ": " + "Remaining Cruise Payment Balance is " + str(booking.get("balanceDueAmount")) + RESET)
        
        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj,cruiseLineName)
        print(" ")
        
        if watchListItems:
            # Process watchlist for each individual passenger instead of per booking
            for guest in guests:
                firstName = guest.get("firstName").capitalize()
                guestPassengerId = guest.get("id")
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
    
    response = requests.get(
        'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory',
        params=params,
        headers=headers,
    )
 
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
            response = requests.get(
                'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory/' + orderCode,
                params=params,
                headers=headers,
            )
            
            for orderDetail in response.json().get("payload").get("orderHistoryDetailItems"):
                # check for canceled status at item-level
                
                quantity = orderDetail.get("priceDetails").get("quantity")
                order_title = orderDetail.get("productSummary").get("title")
                
                #product = orderDetail.get("productSummary").get("id")
                product = orderDetail.get("productSummary").get("baseId")
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
                        
                    passengerId = guest.get("id")
                    firstName = guest.get("firstName").capitalize()
                    reservationId = guest.get("reservationId")
                    
                    # Skip if item checked already
                    newKey = passengerId + reservationId + prefix + product
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
                    #getInCartPricePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,quantity,paidPrice,currency,product,apobj, guest,passengerId,firstName,room,orderCode,orderDate,owner)
                    
                    getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,firstName,room,orderCode,orderDate,owner,False,cruiseLineName)

def get_cruise_price(url, paidPrice, apobj, automaticURL,iteration = 0):
    
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'none',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
    }
      
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    
    sailDate = params.get("sailDate")[0]
    currencyCodeList = params.get("selectedCurrencyCode")
    if currencyCodeList is None:
        currencyCode = "USD"
    else:
        currencyCode = currencyCodeList[0]
    
    bookingOfficeCountryCode = params.get("country")[0] 
    sailDateDisplay = datetime.strptime(sailDate, "%Y-%m-%d").strftime(dateDisplayFormat)
    shipCode = params.get("shipCode")[0]
    shipName = shipDictionary[shipCode]    
    
    cabinClassString = ""
    if params.get("cabinClassType") is not None:
        cabinClassString = params.get("cabinClassType")[0]
    elif params.get("r0d") is not None:
        cabinClassString = params.get("r0d")[0]
    
    stateroomTypeName = params.get("r0d")[0]
    stateroomSubtype = params.get("r0e")[0]
    stateroomCategoryCode = params.get("r0f")[0]
    
    # Change URL
    if stateroomCategoryCode is None:
        stateroomCategoryCode = stateroomSubtype
    
    preString = "         " + sailDateDisplay + " " + shipName + " " + cabinClassString + " " + stateroomCategoryCode
    
    packageCode = params.get("packageCode")[0]
    numberOfAdults = params.get("r0a")[0]
    numberOfChildren = params.get("r0c")[0]
    
    
    m = re.search('www.(.*).com', url)
    cruiseLineName = m.group(1)
    
    # Remake the URL in a format that works to check the class of room. Should avoid issues
    if not automaticURL:
        if params.get("r0j") is None: # This is for a GTY Room, as r0j is the room number normally
            url = f"https://www.{cruiseLineName}.com/checkout/add-ons?packageCode={packageCode}&sailDate={sailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={currencyCode}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0D=y&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0g=BESTRATE&r0h=n&r0C=y"
        else: # This is for a non GTY Room
            url = f"https://www.{cruiseLineName}.com/room-selection/room-location?packageCode={packageCode}&sailDate={sailDate}&country={bookingOfficeCountryCode}&selectedCurrencyCode={currencyCode}&shipCode={shipCode}&roomIndex=0&r0a={numberOfAdults}&r0c={numberOfChildren}&r0d={stateroomTypeName}&r0e={stateroomSubtype}&r0f={stateroomCategoryCode}&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0D=y"
    
    response = requests.get(url,headers=headers)
        
    soup = BeautifulSoup(response.text, "html.parser")
    soupFind = soup.find("span",attrs={"data-testid":"pricing-total"})
    
    # Check if Get to: Guest Info, Room Selection, Or Addons Panel
    # These are the three types of webpages that occur if your room is available
    roomIsFound = re.search("GuestInfoPanel_heading|RoomLocationPanel_title|AddOnsPanel_heading", response.text) 

    # Extract Number of Nights from URL
    if params.get("groupId") is not None:
        groupID = params.get("groupId")[0]
        part = groupID[2:4]
        
    if params.get("packageCode") is not None:
        packageCode = params.get("packageCode")[0]
        part = packageCode[2:4]
    
    numbers_only = "".join(c for c in part if c.isdigit())
    numberOfNights = int(numbers_only)
        
    daysBeforeCruise = days_between(datetime.today().isoformat().replace('-', '')[0:8],sailDate.replace('-', ''))
    finalPaymentDeadline = 0
    
    if numberOfNights < 5:
        finalPaymentDeadline = 75
    elif numberOfNights < 15:
        finalPaymentDeadline = 90
    else:
        finalPaymentDeadline = 120
    
    if not roomIsFound:
        textString = preString + " No Longer Available To Book"
        if automaticURL and (daysBeforeCruise < finalPaymentDeadline):
            textString = textString + " and Past Final Payment Date"
            
        print(YELLOW + textString + RESET)
        
        # If you specified the URL, provide a notification to update the URL
        if not automaticURL:
            apobj.notify(body=textString, title='Cruise Room Not Available')
        return

    if soupFind is None:
        textString = preString + " No Longer Available To Book"
        print(YELLOW + textString + RESET)
        apobj.notify(body=textString, title='Cruise Room Not Available')
        return
    
    priceString = soupFind.text
    if currencyCode == "DKK":
        priceString = priceString.replace(".", "")
        priceString = priceString.replace(",", ".")
        m = re.search("(.*)" + "kr", priceString)
    elif currencyCode == "GBP": 
        priceString = priceString.replace(",", "")
        m = re.search("\\£(.*)" + currencyCode, priceString)
    else:
        priceString = priceString.replace(",", "")
        m = re.search("\\$(.*)" + currencyCode, priceString)
    
    priceOnlyString = m.group(1)
    price = float(priceOnlyString)
    
    if paidPrice is None:
        tempString = GREEN + preString + ": Current Price " + str(price) + RESET
        print(tempString)
        return
    
    if price < paidPrice: 
        # Notify if should rebook
        if automaticURL and (daysBeforeCruise >= finalPaymentDeadline):
            textString = "Rebook! " + preString + " New price of "  + str(price) + " is lower than " + str(paidPrice)
            print(RED + textString + RESET)
            apobj.notify(body=textString, title='Cruise Price Alert')
        # Don't notify if rebooking not possible
        if  automaticURL and (daysBeforeCruise < finalPaymentDeadline):
            textString = "Past Final Payment Date " + preString + " New price of "  + str(price) + " is lower than " + str(paidPrice)
            print(YELLOW + textString + RESET)
            # Do not notify as no need!
            #apobj.notify(body=textString, title='Cruise Price Alert')
        # Always notify if URL is manually provided, assuming you have not booked it yet
        if not automaticURL:
            textString = "Consider Booking! " + preString + " New price of "  + str(price) + " is lower than watchlist price of " + str(paidPrice)
            print(RED + textString + RESET)
            apobj.notify(body=textString, title='Cruise Price Alert')
    else:
        tempString = GREEN + preString + ": You have best price of " + str(paidPrice) + RESET
        if price > paidPrice:
            tempString += " (now " + str(price) + ")"
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

    response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)

    shipCodes = []
    ships = response.json().get("payload").get("ships")
    for ship in ships:
        shipCode = ship.get("shipCode")
        shipCodes.append(shipCode)
        name = ship.get("name")
        classificationCode = ship.get("classificationCode")
        brand = ship.get("brand")
        print(shipCode + " " + name)
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

    response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)
    ships = response.json().get("payload").get("ships")
    
    shipCodes = {}
    
    for ship in ships:
        shipCode = ship.get("shipCode")
        name = ship.get("name")
        shipCodes[shipCode] = name
    return shipCodes

# Get SailDates From a Ship Code
def getSailDates(shipCode):
    headers = {
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'appversion': '1.54.0',
        'accept-language': 'en',
        'user-agent': 'okhttp/4.10.0',
    }

    params = {
        'resultSet': '100',
    }


    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/ships/' + shipCode + '/voyages', params=params, headers=headers)
    voyages = response.json().get("payload").get("voyages")
    
    sailDates = []
    for voyage in voyages:
        sailDate = voyage.get("sailDate")
        sailDates.append(sailDate)
        voyageDescription = voyage.get("voyageDescription")
        voyageId = voyage.get("voyageId")
        voyageCode = voyage.get("voyageCode")
        print(sailDate + " " + voyageDescription)

    return sailDates

# Get Available Products from shipcode and saildate
def getProducts(shipCode, sailDate):
    
    headers = {
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'appversion': '1.54.0',
        'accept-language': 'en',
        'user-agent': 'okhttp/4.10.0',
    }

    params = {
        'sailingID': shipCode + sailDate,
        'offset': '0',
        'availableForSale': 'all',
    }

    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/products', params=params, headers=headers)

    products = response.json().get("payload").get("products")
    for product in products:
        productTitle = product.get("productTitle")
        startingFromPrice = product.get("startingFromPrice")
        
        availableForSale = product.get("availableForSale")
        if not startingFromPrice or not availableForSale:
            continue
            
        adultPrice = startingFromPrice.get("adultPrice")
        print(productTitle + " " + str(adultPrice))

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
    
    response = requests.get('https://aws-prd.api.rccl.com/en/royal/web/v1/guestAccounts/upgrades', headers=headers)
    for booking in response.json().get("payload"):
        print( booking.get("bookingId") + " " + booking.get("offerUrl") )


def GetCruisePriceFromAPI(currency, packageCode, sailDate, bookingType, numAdults, numChildren):

    cookies = {
        '_abck': '297E795D527DA0176A3DC95A05D590C2~0~YAAQn5QZuCS0guqbAQAApcHz9Q/EeVXBhFP8GnaUZxrk099BVlGgBx/JfgS02ckMvEs4MwxQYFmvqwCv7OhfkzynaIls9q8fZEIFNEBwm3tVF2/cDgFc1CMISsW+qA3CE2HHT/mgJ3JUyiO8/kjGQTTReJGdgbyenLtdByLrkp9ui8tcNu5GCG6FE6S5DAy7afeoDF4D5HSGYGkQWqhfMHwCvSaBjA3P/S/UCjuldrtIQTgo+Xz7jT/fjp+dyND2fjjtuNHnvVu7wPJbSNK+xOZs3Ui8u45qhsaUyIGfWp4RUGsnO7i3v4zZ8kI6vfL1ShM7JKZBmeSa6uI+/OzKOHJq+boLwfvFBz0SsnCfxtDdj4mFX59FgTv/EoPF9qwQqeSaltYSaJVWz1n5hLSdr4+o7wRPwXJDtPuNHg686n+6W4dF3BGeUkjV1SfQkhALcddHpoksXzLAhlUxYwYHB/RwtbUJ1EWKlssjbygsxch4wv6how/w/f50EHn23Qwd9f3zZn7J69aT3eVeuGR0pr5jBKra3n6om7H/9JVHqfUYLpPICYC148lZs3FftUlW5p6jIHIpZlHX9uZvKmuMs4WI6DiWHMjfpIwonMVkJw024HIDZPqm/dC3WmaSRBt0d+R4E3rI5M/V9RmCIrtj99I6MLFK4NUBh5Q5UUPadyXkfJ4vomaFiQ6fgAzugLsOzwEFapSFUUhj9uRsh6zWTjN1qR4Mrrd+uAoXpE1gQAGRXdTI7h4HPRlsU0iQQrQesw73ziiLE8a3nlD9IHolQ1XMnlNTp9kHHwwB7/I0KrP4uHemyZWNmRSnOdzatqJy3h1ywy1/wHHDd9pVy7yLtMp1JH75YktTDQQ0eHc=~-1~-1~1769361460~AAQAAAAF%2f%2f%2f%2f%2f2vU5DuWauaTWH%2f58nvgf4OtMwpHGeMC7+T6wQ2cda8g%2fGgf9EJaEHMB51KuibNiz1WCjdHGr4scnnQ8mDzSvJmY7NmXZBmVnGQ368rMK7kgo879Hoq+dRAw1tcHZskMrEWYexo%3d~-1',
        'AMCV_981337045329610C0A490D44%40AdobeOrg': '1585540135%7CMCMID%7C65518191893208271519119443450072481442%7CMCAID%7CNONE%7CMCOPTOUT-1769365115s%7CNONE%7CvVersion%7C4.4.0%7CMCIDTS%7C20478',
        's_nr': '1769357915730-Repeat',
        '__td_signed': 'true',
        'consumerID': '200073083',
        '_mibhv': 'anon-1742945302576-6531793573_9236',
        'fe_sso': 'CtCG2D0Yhh5wbIxz-Zx7lIv9Lf0.*AAJTSQACMDEAAlNLABxSeW5VOVRWVjhqWDhMRUo1Y0ZRWWJPdzhyd1E9AAR0eXBlAANDVFMAAlMxAAA.*',
        'OptanonConsent': 'isGpcEnabled=0&datestamp=Sun+Jan+25+2026+11%3A18%3A32+GMT-0500+(Eastern+Standard+Time)&version=202411.2.0&browserGpcFlag=1&isIABGlobal=false&hosts=&consentId=c7583ebe-4ed6-4e84-82e9-ac204537dbdf&interactionCount=3&isAnonUser=1&landingPath=NotLandingPage&groups=C0001%3A1%2CC0003%3A1%2CC0002%3A1%2CC0004%3A1%2CBG105%3A1&intType=1&geolocation=US%3BMA&AwaitingReconsent=false',
        'mbox': 'PC#3612e3d0cc8244788deba6764ca74a48.34_0#1832602716|session#bca6333b847f4ae9b42b3143eb2ff460#1769359777',
        'LPVID': 'JkZGM1NzJjZTE5ZTRlNGQ3',
        'rwd_id': '5a99d17d-4b22-4fe0-81a9-2f40c2d224ad',
        'departureDate': '20260216',
        's_evar66cvp': '%5B%5B%27Direct%27%2C%271765722688585%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271765935647510%27%5D%2C%5B%27Direct%27%2C%271766017975418%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271766020200267%27%5D%2C%5B%27Direct%27%2C%271766351693796%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271766351858209%27%5D%2C%5B%27Direct%27%2C%271766790531749%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271766874132381%27%5D%2C%5B%27Direct%27%2C%271767839807232%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271767919145227%27%5D%2C%5B%27Direct%27%2C%271768154512055%27%5D%2C%5B%27cruisespotlight.com%27%2C%271768218912552%27%5D%2C%5B%27Direct%27%2C%271768260147314%27%5D%2C%5B%27cruisespotlight.com%27%2C%271768260250685%27%5D%2C%5B%27Direct%27%2C%271768353148518%27%5D%2C%5B%27Google-Organic-keyword%2520unavailable%27%2C%271768358317788%27%5D%2C%5B%27Direct%27%2C%271769357862467%27%5D%5D',
        's_evar68cvp': '%5B%5B%27Direct%27%2C%271765722688586%27%5D%2C%5B%27Organic%27%2C%271765935647511%27%5D%2C%5B%27Direct%27%2C%271766017975419%27%5D%2C%5B%27Organic%27%2C%271766020200268%27%5D%2C%5B%27Direct%27%2C%271766351693796%27%5D%2C%5B%27Organic%27%2C%271766351858210%27%5D%2C%5B%27Direct%27%2C%271766790531750%27%5D%2C%5B%27Organic%27%2C%271766874132383%27%5D%2C%5B%27Direct%27%2C%271767839807232%27%5D%2C%5B%27Organic%27%2C%271767919145228%27%5D%2C%5B%27Direct%27%2C%271768154512055%27%5D%2C%5B%27Social%2520Media%2520-%2520Organic%27%2C%271768218912557%27%5D%2C%5B%27Direct%27%2C%271768260147314%27%5D%2C%5B%27Social%2520Media%2520-%2520Organic%27%2C%271768260250686%27%5D%2C%5B%27Direct%27%2C%271768353148519%27%5D%2C%5B%27Organic%27%2C%271768358317789%27%5D%2C%5B%27Direct%27%2C%271769357862470%27%5D%5D',
        'tiktok_throttle': 'tiktok_ON',
        'visitorType': 'New to Cruise',
        'OptanonAlertBoxClosed': '2025-12-06T00:45:50.827Z',
        'MCMID': '65518191893208271519119443450072481442',
        's_fid': '212E3ECAA85E5D3B-342E120B9AEE1D74',
        's_v21': '%5B%5B%27retargeting%2520active%27%2C%271768748708592%27%5D%5D',
        '_fbp': 'fb.1.1765493910373.661147650104698682.Bg',
        'rcclGuestCookie': '%7B%22packageCode%22%3A%22OY7BH145%22%2C%22shipCode%22%3A%22OY%22%2C%22sailDate%22%3A%222026-12-20%22%2C%22startDate%22%3A%222026-12-20%22%2C%22itineraryName%22%3A%227%20Night%20Perfect%20Day%20Bahamas%20Holiday%22%2C%22stateroomPricing%22%3A%22BALCONY%22%2C%22itineraryUrl%22%3A%22%2Froom-selection%2Frooms-and-guests%3FgroupId%3DOY07BYE-3974839358%26packageCode%3DOY7BH145%26sailDate%3D2026-12-20%26country%3DUSA%26selectedCurrencyCode%3DUSD%26shipCode%3DOY%26cabinClassType%3DBALCONY%26icid%3Dhprtrg_conten_crb_hm_hero_2058%22%2C%22numberOfAdults%22%3A2%2C%22numberOfChildren%22%3A0%2C%22numberOfRooms%22%3A1%2C%22groupId%22%3A%22OY07BYE-3974839358%22%2C%22searchQuery%22%3A%22%26groupId%3DOY07BYE-3974839358%22%2C%22dismissedRetaget%22%3Atrue%7D',
        'VDS_ID': 'e0e406a2-b8a0-41c4-9a60-9fa9f62aace5',
        'newUniversalNav': 'experienceB',
        'ak_bmsc': '805F3596406A18068E68488799EA7C0F~000000000000000000000000000000~YAAQn5QZuAq0guqbAQAAor/z9R61sBRJS0jiQFiWig75Hhll0vzrxP7FlZNpMyn8PXa3idNWikyhV9qWBW3KUnCSeSrpyqUCb4yQXo/PjdjMKhPRM7h+hyHSa0d0I0qZB50K8pWNJb9iPUybSzBG41iw48rwh36iNERTNCBVKaYU+BbPB3ck/uVZZbDIEZbfVz/E9LCYepb1yYLgAUJV20lZQNk/qTmxyVdQUWCdrTYqnWewUM3qe/eGFPJx88w34UfI/wWRS99mMTeaeUf75KwKg/bBVnMO2XxkMIPHsITgWEI2YZLK2FHr8VFvecCIYTDxmdMspYRpd00UR6yBzftxxmKz1DpTR7NVTWJEHvbXMv/9j56IxbWPa1CdKXdM2ZEATJrgNS8Ffgx6n3Z3W9F7rfENtqKLohfF9DES8A7tixriaVDx',
        'bm_sz': '60B095BB23F2F1B2FAED9B4B3557FFD1~YAAQn5QZuAu0guqbAQAAor/z9R6KY7C2cax04VXHtL5tgXelr23B/Yo20RpPYa/69m+3phTw9SfBd8gn9ScjvNHhUvjui/+4cAB3vtpSTu0M8CoQjLJvLPYeYjZ51q45OZ/fckCK75OxyV/EwJfeA9ctlqVvg/9VrSvokledM25x6s/erq9GbZn0TGNnYSBOHzHo5I49kFr7Jj6j1+728DNwVyZjk7/kl//KZOSaZeZb+utCN7z3d4CqYSTsvObxFl9cQAZ2/HyvgAxSNJ/7IlU+8SzFBOC5Kmkg1qsUquZ9/77BaBZUC1DpWZiCurH/HDBgYsOv4qJ83+EfHYCyfCUCEPbwTCL5/xWdZB6cKl3bxSYR6cDctppXYJMWc/FpFExXM2FXtPsQmo2ei05qoBe5Jjjwehr3cngMWrIxAKFNzyCEdEuGhzSdDx+WoNm0pBXQifgELg==~4272450~3162681',
        'bm_sv': '0B62EE0F4717A50A6C451C8ABC5C053A~YAAQn5QZuLSpguqbAQAAEzXz9R4MkUhCu4jE3j4PC8GpWgEqUGF0+aBI7xgAeCT/2R+xF8+TcoYvIoMXjYTTPLTdG0B+jo9hC5EpejYbjxeow9WTBeaB6V/gjOsLhLOAt+IEtmx7appiX6mCLdwxDhkheI+ECxJcB7LPsie547mqYryVdSCpLnSJ9isnJMlpmjvFaEpoD4siykhG0POiZMg2LCK76nduwcMQ/ZSXBxxY7/OvqC1tHAhIBVIoolsJVG4/K9wAxs41~1',
        '_cs_mk_aa': '0.4631365231879573_1769357861448',
        'pdFlags': 'enable-rooms-section%3Dfalse',
        's_dl': '1',
        's_cm': 'Typed%2FBookmarkedTyped%2FBookmarkedundefined',
        'cmCampaign': 'Direct',
        'cmChannel': 'Direct',
        'gpv_pn': 'findacruise',
        'AKA_A2': 'A',
        'bm_mi': 'C7617DFF47795E752FC57AA82F4FDF77~YAAQn5QZuDmlguqbAQAA6f7y9R6jie6pvjV+MXih0F+3ev+rRrjILM+48ZiFudmFCyfcAvUz44FTmC7WGAl4GB+5LwbovPED+K/zTzKjZ+ljuMgBLImwCwuEoAMRM78sHrY4X9Fr5amiLn9B5+mxshHu7YPAW+jaCMrKkD0v6eJKO6xAkf05r2pQ9x+UhAf3rmHTd03Kc9ivBMT/JLlqqgJ7XFBNAmskc9t28o2QBIsDtbVMSqLU0HUty7W3Rb8uuGY6f8Rr5hvT7oTfkyXaKsRxTDKDxBaPVvRyYDylLSBKrOPkTWuqq8CTh2NbS+V0lxPTBQ==~1',
        'bgv': 'c2',
        'gp': '0',
        'pageNamePersistant': 'findacruise',
        'at_check': 'true',
        'AMCVS_981337045329610C0A490D44%40AdobeOrg': '1',
        'country': 'USA',
        'wuc': 'USA',
        'language': 'en',
        'wul': 'en',
        'akacd_PRC_RCI_CRUISE_SEARCH_PRD_V2': '3946810702~rv=14~id=1dbb7396865ba7cae47bc83497415b43',
        'akacd_PRC-ROYAL-GRAPH-PRD-V2': '3946810703~rv=42~id=5da16e27fbca0a5ebc80078eb6d3a43e',
        's_cc': 'true',
        'JSESSIONID': 'node05zh5ui1actnf1x2ka04hmal0b33661.node0',
        'affinity': '"b329b0421a94a9e7"',
        's_sq': '%5B%5BB%5D%5D',
        'PIM-SESSION-ID': 'yANhcsLi4STtNgPA',
        'utag_main': '_sn:83$vapi_domain:royalcaribbean.com$v_id:019aa96738f50019256ab4943cd005050003200d00919$dc_visit:74$_se:6%3Bexp-session$_ss:0%3Bexp-session$_st:1769359751354%3Bexp-session$ses_id:1769357860035%3Bexp-session$_pn:4%3Bexp-session$dc_event:5%3Bexp-session$count_dc:162%3Bexp-1832429914874$:85%3Bexp-1826850689333',
        'cruiseSearchColor': 'green',
        'rcgCohorts': 'cs:green|',
        'currency': currency,
        '_splunk_rum_sid': '%7B%22expiresAt%22%3A1769358851582%2C%22id%22%3A%22d03854aa9eacc0bd64fb2728f3f24b4b%22%2C%22startTime%22%3A1769357913181%7D',
        'FavoritesExperience': 'redesign',
        'legacyFavoritesUser': 'true',
        's_plt': '4.25%2Cfindacruise',
        '_td': 'cba14fcf-8e57-4766-99fb-bee41b5c76f5',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': 'https://www.royalcaribbean.com/cruises?promo=january-beat-the-clock-1000-is-0123-jan-2026&country=USA&icid=jnfs61_tctclp_jnf_hm_hero_4548',
        'traceparent': '00-81a5fe03b3275b37a01c25bb6ec51c89-479036682032bbeb-00',
        'content-type': 'application/json',
        'brand': 'R',
        'country': 'USA',
        'language': 'en',
        'currency': currency,
        'office': 'MIA',
        'countryalpha2code': 'US',
        'apollographql-client-name': 'rci-NextGen-Cruise-Search',
        'skip_authentication': 'true',
        'request-timeout': '20',
        'x-session-id': '5a99d17d-4b22-4fe0-81a9-2f40c2d224ad',
        'apollographql-query-name': 'cruiseSearch_Cruises',
        'Origin': 'https://www.royalcaribbean.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
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


    sailings = resp.json()["data"]["cruiseSearch"]["results"]["cruises"][0]["sailings"]

   
    for sailing in sailings:
       
        if sailing["sailDate"].replace("-", "") != sailDate:
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
                print("         " + cabinType + " Not for sale")
            else:    
                cabinCostPerPerson = float(price["price"]["value"]) * (numAdults + numChildren)
                print("         " + str(cabinCostPerPerson) + " : Current Cheapest " + cabinType + " Price " + "for " + str(numAdults + numChildren) + postString)
            
   

if __name__ == "__main__":
    main()
 

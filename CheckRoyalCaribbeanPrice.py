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
                getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames)
    
        if 'cruises' in data:
            for cruises in data['cruises']:
                    cruiseURL = cruises['cruiseURL'] 
                    paidPrice = float(cruises['paidPrice'])
                    get_cruise_price(cruiseURL, paidPrice, apobj)
            
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
    
def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,passengerName,room, orderCode, orderDate, owner):
    
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
        tempString = YELLOW + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + " (No Longer for Sale)" + RESET
        print(tempString)
        return
        
    currentPrice = newPricePayload.get("adultPromotionalPrice")
    
    if not currentPrice:
        currentPrice = newPricePayload.get("adultShipboardPrice")
    
    if currentPrice < paidPrice:
        text = passengerName + ": Rebook! " + title + " Price is lower: " + str(currentPrice) + " than " + str(paidPrice)
        
        promoDescription = payload.get("promoDescription")
        if promoDescription:
            promotionTitle = promoDescription.get("displayName")
            text += '\n Promotion:' + promotionTitle
            
        text += '\n' + 'Cancel Order ' + orderDate + ' ' + orderCode + ' at https://www.royalcaribbean.com/account/cruise-planner/order-history?bookingId=' + reservationId + '&shipCode=' + ship + "&sailDate=" + startDate
        
        if not owner:
            text += " " + "This was booked by another in your party. They will have to cancel/rebook for you!"
            
        print(RED + text + RESET)
        apobj.notify(body=text, title='Cruise Addon Price Alert')
    else:
        tempString = GREEN + passengerName.ljust(10) + " (" + room + ") has best price for " + title +  " of: " + str(paidPrice) + RESET
        if currentPrice > paidPrice:
            tempString += " (now " + str(currentPrice) + ")"
        print(tempString)
        
    

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
        print("Casino: " + clubRoyaleLoyaltyTier + " - " + str(clubRoyaleLoyaltyIndividualPoints) + " Points")

    
def getVoyages(access_token,accountId,session,apobj,cruiseLineName,reservationFriendlyNames):

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
        reservationId = booking.get("bookingId")
        passengerId = booking.get("passengerId")
        sailDate = booking.get("sailDate")
        numberOfNights = booking.get("numberOfNights")
        shipCode = booking.get("shipCode")
        guests = booking.get("passengers")
                
        passengerNames = ""
        for guest in guests:
            firstName = guest.get("firstName").capitalize()
            passengerNames += firstName + ", "
        
        passengerNames = passengerNames.rstrip()
        passengerNames = passengerNames[:-1]

        reservationDisplay = str(reservationId)
        # Use friendly name if available
        if str(reservationId) in reservationFriendlyNames:
            reservationDisplay += " (" + reservationFriendlyNames.get(str(reservationId)) + ")"
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        print(reservationDisplay + ": " + sailDateDisplay + " " + shipDictionary[shipCode] + " Room " + booking.get("stateroomNumber") + " (" + passengerNames + ")")
        if booking.get("balanceDue") is True:
            print(YELLOW + reservationDisplay + ": " + "Remaining Cruise Payment Balance is " + str(booking.get("balanceDueAmount")) + RESET)

        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj)
        print(" ")
    

    
def getOrders(access_token,accountId,session,reservationId,passengerId,ship,startDate,numberOfNights,apobj):
    
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
                    
                    getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,currency,product,apobj, passengerId,firstName,room,orderCode,orderDate,owner)

def get_cruise_price(url, paidPrice, apobj, iteration = 0):
        
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

    # clean url of r0y and r0x tags
    findindex1=url.find("r0y")
    findindex2=url.find("&",findindex1+1)
    if findindex2==-1:
        url=url[0:findindex1-1]
    else:
        url=url[0:findindex1-1]+url[findindex2:len(url)]
    
    findindex1=url.find("r0x")
    findindex2=url.find("&",findindex1+1)
    if findindex2==-1:
        url=url[0:findindex1-1]
    else:
        url=url[0:findindex1-1]+url[findindex2:len(url)]
        
    
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    
    sailDate = params.get("sailDate")[0]
    currencyCodeList = params.get("selectedCurrencyCode")
    if currencyCodeList is None:
        currencyCode = "USD"
    else:
        currencyCode = currencyCodeList[0]
    
    sailDateDisplay = datetime.strptime(sailDate, "%Y-%m-%d").strftime(dateDisplayFormat)
    shipName = shipDictionary[params.get("shipCode")[0]]    
    preString = sailDateDisplay + " " + shipName + " " + params.get("cabinClassType")[0] + " " + params.get("r0f")[0]
    
    roomNumberList = params.get("r0j")
    if roomNumberList:
        roomNumber = roomNumberList[0]
        preString = preString + " Cabin " + roomNumber
    
    if iteration > 8:
        print("Check Cruise URL - No room available for " + preString)
        return
    
    m = re.search('www.(.*).com', url)
    cruiseLineName = m.group(1)
    
    response = requests.get('https://www.'+cruiseLineName+'.com/checkout/guest-info', params=params,headers=headers)
    
    soup = BeautifulSoup(response.text, "html.parser")
    soupFind = soup.find("span",attrs={"class":"SummaryPrice_title__1nizh9x5","data-testid":"pricing-total"})
    if soupFind is None:
        m = re.search("\"B:0\",\"NEXT_REDIRECT;replace;(.*);307;", response.text)
        if m is not None:
            redirectString = m.group(1)
            textString = preString + ": URL Not Working - Redirecting to suggested room"
            # Uncomment these print statements, if get into a loop
            #print(textString)
            newURL = "https://www." + cruiseLineName + ".com" + redirectString
            iteration = iteration + 1
            get_cruise_price(newURL, paidPrice, apobj,iteration)
            #print("Update url to: " + newURL)
            return
        else:
            textString = preString + " No Longer Available To Book"
            print(YELLOW + textString + RESET)
            apobj.notify(body=textString, title='Cruise Room Not Available')
            return
    
    priceString = soupFind.text

    if currencyCode == "DKK":
        priceString = priceString.replace(".", "")
        priceString = priceString.replace(",", ".")
        m = re.search("(.*)" + "kr", priceString)
    else:
        priceString = priceString.replace(",", "")
        m = re.search("\\$(.*)" + currencyCode, priceString)
    priceOnlyString = m.group(1)
    price = float(priceOnlyString)
    
    if price < paidPrice: 
        textString = "Rebook! " + preString + " New price of "  + str(price) + " is lower than " + str(paidPrice)
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


if __name__ == "__main__":
    main()
 

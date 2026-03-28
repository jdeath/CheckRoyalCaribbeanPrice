import argparse
import locale
import re
import requests
import sys
import platform

from datetime import datetime, date
from unicodedata import combining, normalize

dateDisplayFormat = "%x"
GREEN = '\033[1;32m'
RESET = '\033[0m' # Resets color to default

appkey_mobile = 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc'
appversion_mobile = '1.54.0'
user_agent_mobile = 'okhttp/4.10.0'

user_agent_web = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0'
appkey_web = 'trL6t38bpvA5p65XlCrhFKzug8NNkqCD'

# Too much output too quickly can overwhelm Python's output buffer, so use this to periodically flush out the buffer
# Alternatively, we could have called python with -u ("python -u BrowseRoyalCarribbeanPrice.py...")
flush_print_buffer = sys.stdout.flush


def main():
    parser = argparse.ArgumentParser(description="Browse Royal Caribbean Price")
    parser.add_argument('-c', '--currency', type=str, default='System', help='currency (default: System Setting)')
    parser.add_argument('-s', '--ship', type=str, help='Ship')
    parser.add_argument('-d', '--saildate', type=str, help='Sail Date (mm/dd/yy format)')
    parser.add_argument('-o', '--sortorder', choices=['asc', 'desc'], default="asc", help='Set sorting order')
    parser.add_argument('-k', '--sortkey', choices=['price', 'alpha', 'default'], default="default", help='Set value to sort on')
    parser.add_argument('-w', '--watchlistcodes', action='store_true', help='Show Codes For Watchlist')
    parser.add_argument('-a', '--activitysort', choices=['date', 'alpha', 'default'], default="default", help='Show Codes For Watchlist')
    args = parser.parse_args()
    
    currency = args.currency
    if currency == "System":
        currency = getSystemCurrency()
    
    if platform.system() == "iOS":
        global GREEN
        global RESET
        GREEN = ""
        RESET = ""
        
    ships = getShips()

    if args.ship:
        # Find the matching ship
        i = 0
        for ship in ships:
            if ship['name'] == f"{args.ship} of the Seas" or ship['name'] == f"Celebrity {args.ship}":
                user_input = str(i)
                break
            i = i + 1
        if i == len(ships):
            print(f"I can't find that ship name {args.ship}; please try again")
            return
    else:
        # User chooses from menu
        i = 0
        print("Select Ship:")
        for ship in ships:
            print(f"{i}) {ship['name']}")
            i = i + 1
        print("q - Quit")
        user_input = input("Enter Ship Number: ")
        if user_input == 'q' or user_input == 'Q':
            print("Have a nice day!")
            return

    numShips = len(ships)
    user_input = int(user_input)
    shipname = "Unknown ship"
    shipcode = "xx"
    
    if user_input < numShips and user_input >= 0:
        shipname = ships[user_input]['name']
        shipcode = ships[user_input]['code']
        print(f"Getting sailings for {shipname}")
        sailings = getSailings(shipcode)
        
        numSailings = len(sailings)
        if args.saildate:
            # Find the matching sailing
            i = 0
            for sailing in sailings:
                if args.saildate == sailing['displayDate'].split(" ")[0]:
                    user_input = str(i)
                    print(f"Getting info for {sailing['displayDate']} {sailing['description']}")
                    break
                i = i + 1
            if i == len(sailings):
                print(f"I can't find that sail date {args.saildate} for {shipname}; please try again")
                return
        else:
            # User chooses from menu
            i = 0
            print("")
            print("Select sailing:")
            for sailing in sailings:
                print(f"{i}) {sailing['displayDate']} {sailing['description']}")
                i = i + 1
            print("q - Quit")
            user_input = input("Enter Sailing Number: ")
            if user_input == 'q' or user_input == 'Q':
                print("Have a nice day!")
                return

        user_input = int(user_input)    
        if user_input < numSailings and user_input >= 0:
            sailing = sailings[user_input]
            print("")
            print(f"Browsing for {shipname} sailing on {sailing['displayDate']} ({sailing['description']})")
            print("")
            
            isRoyal = "of the Seas" in shipname
            
            if isRoyal:
                print("Direct Link To Royal Caribbean Cruise Planner Website: ")
                linkRoot = "https://www.royalcaribbean.com/account/cruise-planner/category/beverage"
            else:
                print("Direct Link To Celebrity Cruise Planner Website: ")
                linkRoot = "https://www.celebritycruises.com/account/cruise-planner/category/drinks"
                
            print(f"{linkRoot}?bookingId=123456&shipCode={shipcode}&sailDate={sailing['date']}")
            print("")
            
            numAdults = 2
            numChildren = 0
            GetCruisePriceFromAPI(currency, shipcode+sailing['voyageCode'], sailing['date'],numAdults, numChildren)
            print("")
            
            print("Gathering list of products.  This may take a few minutes; please be patient.")
            print("These are public prices, sale prices for you could be less")
            print("")
            
            printAllProducts(shipcode,sailing['date'],sailing['duration'],currency,args.sortkey,args.sortorder,args.watchlistcodes)
            
            print("")
            print("Gathering list of activities.  This may take a few minutes; please be patient.")
            flush_print_buffer()
            activities = getAllActivities(shipcode,sailing['date'])
            printAllActivities(activities, args.activitysort)
    else:
        print("Invalid ship selection")

    user_input = input("Hit any key to quit: ")
    print("Have a nice day!")
    

def daysBetween(sailDate,activityDate):
    d0 = date(int(sailDate[0:4]), int(sailDate[4:6]), int(sailDate[6:8]))
    d1 = date(int(activityDate[0:4]), int(activityDate[4:6]), int(activityDate[6:8]))
    delta = d1 - d0 
    return(str(delta.days + 1))

    
def getSystemCurrency():
    # Set the locale to the system's default
    # An empty string "" makes setlocale search the appropriate environment variables.
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error as e:
        print(f"Warning: Could not set locale. Using default 'C' locale. Error: {e}")
        # Fallback to 'C' locale or handle as needed
        locale.setlocale(locale.LC_ALL, 'C')

    # Get a dictionary of the local formatting conventions
    conventions = locale.localeconv()
    
    # Extract the international currency symbol (e.g., "USD ")
    international_symbol = conventions.get('int_curr_symbol', '').strip()
    return international_symbol
    
    
def sanitizeString(string_to_clean):
    # Some unicode characters don't properly print to ASCII terminals
    # Convert unicode non-printable punctuation characters
    tmp_string = string_to_clean.lstrip()
    tmp_string = tmp_string.replace('\u2013', '-')  # replace en dash with -
    tmp_string = tmp_string.replace('\u2014', '-')  # replace en dash with -
    tmp_string = tmp_string.replace('\u2018', '`')  # replace left single quotation with `
    tmp_string = tmp_string.replace('\u2019', '\'')  # replace right single quotation with '
    tmp_string = tmp_string.replace('\u201C', '"')  # replace left double quotation with "
    tmp_string = tmp_string.replace('\u201D', '"')  # replace right double quotation with "
    tmp_string = tmp_string.replace('\u2120', '(SM)')  # replace right double quotation with "

    # Convert unicode non-printable accented characters
    tmp_string = normalize('NFKD', tmp_string)
    return ''.join([c for c in tmp_string if not combining(c)])


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
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    shipNames = []
    ships = response.json().get("payload").get("ships")
    i = 0
    for ship in ships:
        shipCode = ship.get("shipCode")
        name = ship.get("name")
        shipNames.append({'code': shipCode, 'name': name})
    
    return shipNames


###################
# Get Sailings
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
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    voyages = response.json().get("payload").get("voyages")    
    sailings = []
    for voyage in voyages:
        sailDate = voyage.get("sailDate")
        duration = voyage.get("duration")
        voyageCode = voyage.get("voyageCode")
        voyageDescription = voyage.get("voyageDescription")
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        sailings.append({'date': sailDate, 'displayDate': sailDateDisplay,'description': voyageDescription,'duration':duration,'voyageCode':voyageCode})
        
    return sailings


def getWebCatagories(ship,saildate):
    headers = {
        'User-Agent': user_agent_web,
        'Accept': 'application/json',
        'appkey': appkey_web,
    }

    json_data = {
        'operationName': 'WebCategories',
        'variables': {
            'sailDate': saildate,
            'shipCode': ship,
        },
        'query': 'query WebCategories($shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $regionCode: String) { categories(shipCode: $shipCode, sailDate: $sailDate, regionCode: $regionCode, filter: {limitCategoriesWithProducts: true}) { ... on CategoryResultSuccess { categories { id name } } } }',
    }

    response = requests.post('https://aws-prd.api.rccl.com/en/royal/web/graphql', headers=headers, json=json_data)
    catagories = response.json().get("data").get("categories").get("categories")

    productMap = {}
    if catagories is None:
        print("No Items for Sale")
        return productMap
        
    for catagory in catagories:
        productMap[catagory.get("id")] = catagory.get("name")
    
    return productMap


def getProductsGraphAllPages(shipCode,sailDate,duration,currency,sortkey,sortorder,key,dayNumber="all"):    
    headers = {
        'User-Agent': user_agent_web,
        'Accept': 'application/json',
        'appkey': appkey_web,
    }
    
    if sortkey == "price":
        apiSortKey = "PRICE"
    elif sortkey == "alpha":
        apiSortKey = "TITLE"
    else:
        apiSortKey = "RANK"

    if sortorder == "desc":
        apiSortOrder = "DESCENDING"
    else:
        apiSortOrder = "ASCENDING"
        
    json_data = {
        'operationName': 'WebProductsByCategory',
        'variables': {
            'category': key,
            'passengerId': '',
            'shipCode': shipCode,
            'sailDate': sailDate,
            'reservationId': '000000',
            'pageSize': 12, # Changing to more than 20 will not return anything
            #'currentPage': page, # This will get set later in the code
            'sorting': {
                'sortKey': apiSortKey,
                'sortKeyOrder': apiSortOrder,
            },
            'filter': {
                'includeVariantProducts': False, # Changing to true does not do anything
            },
            'currencyCode': currency,
            'includeFilterInfo': False, # Changing to true does not do anything
            'includeIfABexperience': False,
        },
        'query': 'query WebProductsByCategory($category:String!,$passengerId:String,$shipCode:ShipCodeScalar!,$sailDate:LocalDateScalar!,$reservationId:String,$pageSize:Long,$currentPage:Long,$sorting:Sorting,$filter:FilterInput,$currencyCode:String!){products(category:$category,guestTypes:[ADULT],passengerId:$passengerId,shipCode:$shipCode,sailDate:$sailDate,reservationId:$reservationId,pageSize:$pageSize,currentPage:$currentPage,sorting:$sorting,filter:$filter,currencyIso:$currencyCode){... on CommerceProductResultSuccess{commerceProducts{id title variantOptions{code name} price{currency promotionalPrice shipboardPrice formattedPromotionalPrice formattedBasePrice formattedDailyPrice formattedPromoDailyPrice salesUnit{code name label}}}}}}',
        }
        
        
    if dayNumber != "all":
        json_data['variables']['filter']['dayNumber']= [ dayNumber, ]
            
    products = []
    for page in range(100):        
        json_data['variables']['currentPage'] = page
            
        try:
            response = requests.post('https://aws-prd.api.rccl.com/en/royal/web/graphql', headers=headers, json=json_data)
        except Exception as e:
            print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
            return products
            
        tempProducts = response.json().get("data").get("products").get("commerceProducts")
        if tempProducts is None:
            break
        else:
            products = products + tempProducts 
        
    return products


def printAndSortProducts(products,sortkey,sortorder,currency,key,showWatchlistCodes):
    if products is None:
        return
     
    # Alpha Sort via API does not always work, so do a force sort with @AESternberg code
    # Price Sorting works fine from the API 
    # Ultimate dinning package always starts with a " ", so this removes any leading spaces
    if sortkey == 'alpha':
        if sortorder == 'desc':
            sorted_products = sorted(products, key=lambda product: product['title'].lstrip(), reverse=True)
        else:
            sorted_products = sorted(products, key=lambda product: product['title'].lstrip())
    else:
        sorted_products = products

    for product in sorted_products:        
        currentId = product.get("id")
        if product.get("price") == []:
            continue
        priceStruct = product.get("price")[0]

        title = sanitizeString(product.get("title"))
        price = priceStruct.get("formattedPromotionalPrice")
        if price is None:
            price = priceStruct.get("formattedBasePrice")
        
        if price is None:
            price = priceStruct.get("shipboardPrice")
            
        if price is None:
            continue
        
        # Remove any currency codes/$/Pound Sign and spaces
        price = re.sub(r'[^0-9\.]', '', price)
        price = price.replace(" ", "")
        if price == 0:
            continue

        unit = priceStruct.get("salesUnit").get("name")
        
        printString = f"\t{title} {GREEN}{price} {currency}{RESET}"
        
        if unit == "Per Night":
            printString =  printString + " per night" 
        
        if unit == "Per Day":
            printString =  printString + " per day"
        
        if showWatchlistCodes == True:
            printString += f" (prefix: {key}, product: {currentId})"
        
        print(printString)
            
        # Skip first variant, as it is the default
        for variantOption in product.get("variantOptions")[1:]:
           variantCode = variantOption.get("code")
           variantName = variantOption.get("name")
           if "Bottles" in variantName:
               variantName = variantName + " (larger option)"
           
           printString = f"\t{variantName} Price Not Available"
           if showWatchlistCodes == True:
               printString += f" (prefix: {key}, product: {variantCode})" 
           
           print(printString)

           
def printAllProducts(shipCode,sailDate,duration,currency,sortkey,sortorder,showWatchlistCodes):
    productMap = getWebCatagories(shipCode,sailDate)
        
    for key in productMap:
        print(productMap[key])
        
        # Display Shore Excursions by day
        if key == "shorex":
            for day in range(1,duration+2):
                products = getProductsGraphAllPages(shipCode,sailDate,duration,currency,sortkey,sortorder,key,day)
                if products != []:
                    print(f"   Day {day}")
                    printAndSortProducts(products,sortkey,sortorder,currency,key,showWatchlistCodes)
        else:
            products = getProductsGraphAllPages(shipCode,sailDate,duration,currency,sortkey,sortorder,key,"all")
            printAndSortProducts(products,sortkey,sortorder,currency,key,showWatchlistCodes)

        flush_print_buffer()

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
            print("No Activities Scheduled")
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
                    day = daysBetween(sailDate,offeringDate)
                    products.append({'productTitle':productTitle,'location':location,'offeringDate':offeringDate,'offeringTime':offeringTime,'day':day})
                    
            #if len(tempProducts) < 200:
            #    return products
    
    return products

def printAllActivities(activities, sortorder):
    if sortorder == 'date':
        sorted_activities = sorted(activities, key=lambda activity: (activity['offeringDate'],activity['offeringTime']))
    elif sortorder == 'alpha':
        # This is likely unnecessary, but here just in case RCCL default is no longer alphabetical order
        sorted_activities = sorted(activities, key=lambda activity: activity['productTitle'])
    else:
        sorted_activities = activities

    print("\nHere are the scheduled activities:")
    flush_print_buffer() # one last flush before printing the activities
    
    for activity in sorted_activities:
        productTitle = sanitizeString(activity.get("productTitle"))
        location = sanitizeString(activity.get("location"))
        offeringDate = datetime.strptime(activity.get("offeringDate"), "%Y%m%d").strftime("%B %d, %Y")
        offeringTime = activity.get("offeringTime")
        day = activity.get("day")
        print(f"{productTitle}\t {location} {GREEN}{offeringDate} (Day {day}) {offeringTime}{RESET}")

    
def GetCruisePriceFromAPI(currency, packageCode, sailDate, numAdults, numChildren):
    cookies = {
        'currency': currency,
    }

    headers = {
        'User-Agent': user_agent_web,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'currency': currency,
    }
        
    formattedSailDate = sailDate[0:4] + "-" + sailDate[4:6] + "-" + sailDate[6:8]
    
    filterString = f"id:{packageCode}|adults:{numAdults}|children:{numChildren}|startDate:{formattedSailDate}~{formattedSailDate}"
    
    json_data = {
        'operationName': 'cruiseSearch_Cruises',
        'variables': {
            'filters': filterString,
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
            
        print("Cheapest available cabins for this sailing:")
        prices = sailing["stateroomClassPricing"]
        for price in prices:
            cabinCode = price["stateroomClass"]["content"]["code"]                
            cabinType = price["stateroomClass"]["name"]
            
            if price["price"] is None:
                print(f"\t{cabinType} sold out")
            else:    
                numPassengers = int(numAdults) + int(numChildren)
                cabinCostPerPerson = float(price["price"]["value"]) * numPassengers
                print(f"\t{GREEN}{cabinCostPerPerson} {currency}{RESET}: Cheapest {cabinType} Price for {numPassengers}")

# This is not needed, as dress code in list of activities
def printThemeNights(shipCode,sailDate,durration):
    
    headers = {
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'user-agent': 'royal/1.70.1 (com.rccl.royalcaribbean; build:2479; android 16) okhttp/4.10.0',
        'appversion': '1.70.1',
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
            #    print(title)
                
            if any(word.lower() in titleLower for word in word_list):
                if "address" in titleLower:
                    continue
                foundTheme += 1
                
                day = daysBetween(sailDate,date)
                subtitle = re.split(r'[.!]+', card.get("cardSubtitle"))[0].replace("<p>", "").replace("&nbsp;","")
                offeringDate = datetime.strptime(date, "%Y%m%d").strftime("%B %d, %Y")
                print(f"{GREEN}{offeringDate} (Day {day}){RESET} {title}: {subtitle}")
    
    if foundTheme == 0:
        print("Themes Not Available")
    elif foundTheme < durration:
        print("Themes Not Fully Loaded")    
    flush_print_buffer()
    
    
if __name__ == "__main__":
    main()
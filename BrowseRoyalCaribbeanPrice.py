import requests
from datetime import datetime
import argparse
import re

dateDisplayFormat = "%x"

def main():
    parser = argparse.ArgumentParser(description="Browse Royal Caribbean Price")
    parser.add_argument('-c', '--currency', type=str, default='USD', help='currency (default: USD)')
    parser.add_argument('-s', '--ship', type=str, help='Ship')
    parser.add_argument('-d', '--saildate', type=str, help='Sail Date (mm/dd/yy format)')
    parser.add_argument('-o', '--sortorder', choices=['price', 'alpha', 'default'], default="default", help='Set sort order')
    args = parser.parse_args()
    
    currency = args.currency
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
            print("Direct Link To Royal Caribbean Website: ")
            
            isRoyal = "of the Seas" in shipname
            if isRoyal:
                linkRoot = "https://www.royalcaribbean.com/account/cruise-planner/category/beverage"
            else:
                linkRoot = "https://www.celebritycruises.com/account/cruise-planner/category/drinks"
                
            #This link is no longer working for a bogus bookingID
            print(f"{linkRoot}?bookingId=000000&shipCode={shipcode}&sailDate={sailing['date']}")
            print("")
            print("These are public prices, sale prices for you could be less")
            print("")
            #This API appears depreciated
            #getAllProducts(shipcode,sailing['date'],currency, args.sortorder)
            getAllProductsGraph(shipcode,sailing['date'],currency, args.sortorder)
    else:
        print("Invalid ship selection")

    user_input = input("Hit any key to quit: ")
    print("Have a nice day!")
    

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
        'appkey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'accept': 'application/json',
        'appversion': '1.54.0',
        'accept-language': 'en',
        'user-agent': 'okhttp/4.10.0',
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
        voyageDescription = voyage.get("voyageDescription")
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        sailings.append({'date': sailDate, 'displayDate': sailDateDisplay,'description': voyageDescription})
        
    return sailings


###################
# Get Products
# This Function Does Not Get All Products

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
        'availableForSale': 'true',
    }

    try:
        response = requests.get('https://api.rccl.com/en/royal/mobile/v3/prices', params=params, headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    print(products = response.json().get("payload"))
        
    try:
        response = requests.get('https://api.rccl.com/en/royal/mobile/v3/products', params=params, headers=headers)
    except Exception as e:
        print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
        exit(1)

    products = response.json().get("payload").get("products")
   
    for product in products:
        
        productTitle = product.get("productTitle")
        startingFromPrice = product.get("startingFromPrice")
        
        availableForSale = product.get("availableForSale")
        
        if not availableForSale:
            continue
            
        if not startingFromPrice or not availableForSale:
            #print(f"{productTitle}: No Price Available")
            continue
            
        adultPrice = startingFromPrice.get("adultPrice")
        print(f"{productTitle}  {adultPriceString}")

#############################
def getAllProducts(shipCode,sailDate,currency, sortorder):
    productMap = {}
    productMap["beverage"] = "Beverage Packages"
    productMap["shorex"] = "Shore Excursions"
    productMap["dining"] = "Dining Packages"
    productMap["internet"] = "Internet Packages"
    productMap["key"] = "VIP Packages"
    productMap["spa"] = "Spa and Wellness"
    productMap["onboardactivities"] = "Onboard Activities"
    productMap["photoPackage"] = "Photo"
    productMap["arcade"] = "Arcade"
    productMap["gifts"] = "Gifts and Gear"
    productMap["fitness"] = "Fitness"
    
    # Graph Call needed to get all Pre/Post items
    #productMap["preandpost"] = "Pre and Post Cruise"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.5',
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'X-Requested-With': 'XMLHttpRequest',
        'AppKey': 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm',
        'Content-Type': 'application/json',
        'X-Request-Id': '68c9cf789b5eaf0d9bf17e9d',
        'Req-App-Id': 'Royal.Web.PlanMyCruise',
        'Req-App-Vers': '1.79.1',
        'Account-Id': 'deadface-babe-4000-feed-facade123456',
        'Origin': 'https://www.royalcaribbean.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Referer': 'https://www.royalcaribbean.com/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        # Requests doesn't support trailers
        # 'TE': 'trailers',
    }

    params = {
        'reservationId': '000000',
        'startDate': sailDate,
        'currentPage': '0',
        'pageSize': '1000',
        'currencyIso': currency,
        'regionCode': 'ISLAN',
    }
    json_data = {
        'textSearch': None,
        'sortKey': 'rRank-asc',
        'filterFacets': None,
    }
    
    for key in productMap:
        print(productMap[key])

        try:
            response = requests.post(
                f'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog-unauth/v2/{shipCode}/categories/{key}/products',
                params=params,
                headers=headers,
                json=json_data,
            )
        except Exception as e:
            print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
            quit()

        if response.status_code != 200:
            print(f"Error getting voyage information (API https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog-unauth/v2/{shipCode}/categories/{key}/products returned error code {response.status_code}). Quitting.")
            quit()

        payload = response.json().get("payload")
        if payload is None:
            continue
            
        products = payload.get("products")
        if products is None:
            continue
     
        # Sort products based on sort order argument
        if sortorder == 'alpha':
            sorted_products = sorted(products, key=lambda product: product['title'])
        elif sortorder == 'price':
            sorted_products = sorted(products, key=lambda product: product['lowestAdultPrice'])
        else:
            sorted_products = products

        for product in sorted_products:
            title = product.get("title")
            price = product.get("lowestAdultPrice")
            if price == 0:
                continue
                           
            printString = f"\t{title}:  {price:.2f} {currency}"
             
            if product.get("salesUnit") in [ 'PER_DAY' ]:
                printString =  printString + " per day" 
            
            if product.get("salesUnit") in [ 'PER_NIGHT' ]:
                printString =  printString + " per night"
             
            # Promo % off is basically a scam, do not print it 
            #promoDescription = product.get("promoDescription")    
            #if promoDescription is not None:
            #    promoName = promoDescription.get("displayName")
            #    printString = printString + f" - {promoName}"
            
            print(printString)

def getWebCatagories(ship,saildate):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'gqlRouter': 'products',
        'appkey': 'trL6t38bpvA5p65XlCrhFKzug8NNkqCD',
        'Operating-System': 'Firefox 148.0',
        'Operating-System-Version': '148.0',
        'X-APOLLO-OPERATION-NAME': 'WebCategories',
        'X-APOLLO-OPERATION-TYPE': 'query',
        'channel': 'web',
        'X-Request-Id': '69ba051a4aeed730105836a7',
        'Req-App-Id': 'Royal.Web.PlanMyCruise',
        'Req-App-Vers': '1.84.1',
        'Account-Id': 'deadface-babe-4000-feed-facade123456',
        'Content-Type': 'application/json',
        'Origin': 'https://www.royalcaribbean.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Referer': 'https://www.royalcaribbean.com/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        # Requests doesn't support trailers
        # 'TE': 'trailers',
    }

    json_data = {
        'operationName': 'WebCategories',
        'variables': {
            'sailDate': saildate,
            'shipCode': ship,
        },
        'query': 'query WebCategories($shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $regionCode: String) {\n  categories(\n    shipCode: $shipCode\n    sailDate: $sailDate\n    regionCode: $regionCode\n    filter: {limitCategoriesWithProducts: true, includeNonRevenueCategories: false, limitCategoriesWithVenues: false}\n  ) {\n    ... on CategoryResultSuccess {\n      __typename\n      categories {\n        description\n        id\n        media {\n          source {\n            altText\n            path\n            __typename\n          }\n          primary\n          type\n          __typename\n        }\n        name\n        order\n        __typename\n      }\n    }\n    ... on CategoryExceptions {\n      exceptions {\n        ... on CategoryNotFound {\n          exceptionType\n          __typename\n        }\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}',
    }

    response = requests.post('https://aws-prd.api.rccl.com/en/royal/web/graphql', headers=headers, json=json_data)
    catagories = response.json().get("data").get("categories").get("categories")

    productMap = {}
    for catagory in catagories:
        productMap[catagory.get("id")] = catagory.get("name")
    
    return productMap
    
def getAllProductsGraph(shipCode,sailDate,currency, sortorder):
    
    productMap = getWebCatagories(shipCode,sailDate)
    
    headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    # 'Accept-Encoding': 'gzip, deflate, br, zstd',
    'gqlRouter': 'products',
    'appkey': 'trL6t38bpvA5p65XlCrhFKzug8NNkqCD',
    'Operating-System': 'Firefox 146.0',
    'Operating-System-Version': '146.0',
    'X-APOLLO-OPERATION-NAME': 'WebProductsByCategory',
    'X-APOLLO-OPERATION-TYPE': 'query',
    'channel': 'web',
    'X-Request-Id': '696582d9e496660a4aaf61ac',
    'Req-App-Id': 'Royal.Web.PlanMyCruise',
    'Req-App-Vers': '1.83.0',
    'Account-Id': 'deadface-babe-4000-feed-facade123456',
    'Content-Type': 'application/json',
    'Origin': 'https://www.royalcaribbean.com',
    'DNT': '1',
    'Sec-GPC': '1',
    'Connection': 'keep-alive',
    'Referer': 'https://www.royalcaribbean.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    # Requests doesn't support trailers
    # 'TE': 'trailers',
    }

    if sortorder == "price":
        sortKey = "PRICE"
    elif sortorder == "alpha":
        sortKey = "TITLE"
    else:
        sortKey = "RANK"
        
    for key in productMap:
        print(productMap[key])

        json_data = {
            'operationName': 'WebProductsByCategory',
            'variables': {
                'category': key,
                'passengerId': '',
                'shipCode': shipCode,
                'sailDate': sailDate,
                'reservationId': '000000',
                'pageSize': 12,
                'currentPage': 0,
                'sorting': {
                    'sortKey': sortKey,
                    'sortKeyOrder': 'ASCENDING',
                },
                'filter': {
                    'includeVariantProducts': False,
                },
                'currencyCode': currency,
                'includeFilterInfo': False,
                'includeIfABexperience': False,
            },
            'query': "query WebProductsByCategory($category: String!, $shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $currencyCode: String!) { products(category: $category, guestTypes: [ADULT], shipCode: $shipCode, sailDate: $sailDate, currencyIso: $currencyCode) { ... on CommerceProductResultSuccess { commerceProducts { id title price { currency promotionalPrice shipboardPrice formattedPromotionalPrice formattedBasePrice salesUnit{ name }} } } } }",
        }
      
        try:
            response = requests.post('https://aws-prd.api.rccl.com/en/royal/web/graphql', headers=headers, json=json_data)
        except Exception as e:
            print(f"Can't contact cruise line servers; please try again later\n(program exception '{e}')")
            quit()

        
        payload = response.json().get("data")
        if payload is None:
            continue
        
        products = payload.get("products").get("commerceProducts")
        
        if products is None:
            continue
     
        # Not needed as API supports sorting!
        # Sort products based on sort order argument
        # if sortorder == 'alpha':
            # sorted_products = sorted(products, key=lambda product: product['title'])
        # elif sortorder == 'price':
            # # The lambda performs the following ('p' is standing in for 'products' in the function)
            # # 1. Set p['price'][0] to 'x' for simplicity using a walrus operator
            # # 2. Pick 'formattedPromotionalPrice' if it exists, else 'formattedBasePrice' (0 if neither exist)
            # # 3. Use re.sub to keep only numbers and decimals
            # # 4. Convert to float
            # sorted_products = sorted(products, key=lambda p: float(re.sub(r'[^\d.]', '',
                # (x := p['price'][0])['formattedPromotionalPrice'] or x['formattedBasePrice'] or "0"
            # )))
        # else:
            # sorted_products = products

        for product in products:
            title = product.get("title")
            
            if product.get("price") == []:
                continue
                
            priceStruct = product.get("price")[0]
            
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
            
            printString = f"\t{title} {price} {currency}"
            
            if unit == "Per Night":
                printString =  printString + " per night" 
            
            if unit == "Per Day":
                printString =  printString + " per day"
             
            # Promo % off is basically a scam, do not print it 
            #promoDescription = product.get("promoDescription")    
            #if promoDescription is not None:
            #    promoName = promoDescription.get("displayName")
            #    printString = printString + f" - {promoName}"
            print(printString)
        
if __name__ == "__main__":
    main()
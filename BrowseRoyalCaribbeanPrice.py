import requests
from datetime import datetime
import argparse

dateDisplayFormat = "%x"

##########
# Get Ships

def main():
    parser = argparse.ArgumentParser(description="Browse Royal Caribbean Price")
    parser.add_argument('-c', '--currency', type=str, default='USD', help='currency (default: USD)')
    args = parser.parse_args()
    
    currency = args.currency
    print("Select Ship")
    ships = getShips()
    print("q - Quit")

    user_input = input("Enter Ship Number: ")
    if user_input == 'q' or user_input == 'Q':
        print("Have a nice day!")
        return

    numShips = len(ships)
    user_input = int(user_input)    
    if user_input < numShips and user_input >= 0:
        ship = ships[user_input]
        sailings = getSailings(ship)
        print("q - Quit")
        
        numSailings = len(sailings)
        user_input = input("Enter Sailing Number: ")
        if user_input == 'q' or user_input == 'Q':
            print("Have a nice day!")
            return
        
        user_input = int(user_input)    
        if user_input < numSailings and user_input >= 0:
            sailing = sailings[user_input]
            print("")
            print("Direct Link To Royal Caribbean Website: ")
            print("https://www.royalcaribbean.com/account/cruise-planner/category/beverage?bookingId=000000&shipCode=" + ship + "&sailDate=" + sailing)
            print("")
            print("These are public prices, sale prices for you could be less")
            print("")
            getAllProducts(ship,sailing,currency)
            getAllProductsGraph(ship,sailing,currency)

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

    response = requests.get('https://api.rccl.com/en/all/mobile/v2/ships', params=params, headers=headers)

    shipCodes = []
    ships = response.json().get("payload").get("ships")
    i = 0
    for ship in ships:
        shipCode = ship.get("shipCode")
        shipCodes.append(shipCode)
        name = ship.get("name")
            
        print(str(i) + ") " + name)
        i = i + 1
    return shipCodes


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


    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/ships/' + shipCode + '/voyages', params=params, headers=headers)
    voyages = response.json().get("payload").get("voyages")
    
    sailDates = []
    i = 0
    for voyage in voyages:
        sailDate = voyage.get("sailDate")
        sailDates.append(sailDate)
        voyageDescription = voyage.get("voyageDescription")
        sailDateDisplay = datetime.strptime(sailDate, "%Y%m%d").strftime(dateDisplayFormat)
        
        print(str(i) + ") " + sailDateDisplay + " " + voyageDescription)
        i = i + 1
        
    return sailDates


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

    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/prices', params=params, headers=headers)
    print(products = response.json().get("payload"))
        
    response = requests.get('https://api.rccl.com/en/royal/mobile/v3/products', params=params, headers=headers)
  
    products = response.json().get("payload").get("products")
   
    for product in products:
        
        productTitle = product.get("productTitle")
        startingFromPrice = product.get("startingFromPrice")
        
        availableForSale = product.get("availableForSale")
        
        if not availableForSale:
            continue
            
        if not startingFromPrice or not availableForSale:
            #print(productTitle + ": No Price Available")
            continue
            
        adultPrice = startingFromPrice.get("adultPrice")
        print(productTitle + " " + str(adultPrice))
       
#############################
def getAllProducts(shipCode,sailDate,currency):
    productMap = {}
    productMap["beverage"] = "Beverage Packages"
    productMap["shorex"] = "Shore Excursions"
    productMap["dining"] = "Dinning Packages"
    productMap["internet"] = "Internet Packages"
    productMap["key"] = "VIP Packages"
    productMap["spa"] = "Spa and Wellness"
    productMap["onboardactivities"] = "Onboard Activities"
    productMap["photoPackage"] = "Photo"
    productMap["arcade"] = "Arcade"
    productMap["gifts"] = "Gifts and Gear"
    productMap["fitness"] = "Fitness"
    #Pre/Post best to do with Graph Call
    #productMap["preandpost"] = "Pre and Post"
    
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

        response = requests.post(
            'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog-unauth/v2/' + shipCode + '/categories/' + key + '/products',
            params=params,
            headers=headers,
            json=json_data,
        )
        payload = response.json().get("payload")
        if payload is None:
            continue
            
        products = payload.get("products")
        if products is None:
            continue
     
        for product in products:
            title = product.get("title")
            price = product.get("lowestAdultPrice")
            if price == 0:
                continue
                           
            printString = title + " " + str(price) + " " + currency
            
            if product.get("salesUnit") in [ 'PER_DAY' ]:
                printString =  printString + " per day" 
            
            if product.get("salesUnit") in [ 'PER_NIGHT' ]:
                printString =  printString + " per night"
             
            # Promo % off is basically a scam, do not print it 
            #promoDescription = product.get("promoDescription")    
            #if promoDescription is not None:
            #    printString = printString + " - " + promoDescription.get("displayName")
            
            print(printString)

def getAllProductsGraph(shipCode,sailDate,currency):
    productMap = {}
    #productMap["beverage"] = "Beverage Packages"
    #productMap["shorex"] = "Shore Excursions"
    #productMap["dining"] = "Dinning Packages"
    #productMap["internet"] = "Internet Packages"
    #productMap["key"] = "VIP Packages"
    #productMap["spa"] = "Spa and Wellness"
    #productMap["onboardactivities"] = "Onboard Activities"
    #productMap["photoPackage"] = "Photo"
    #productMap["arcade"] = "Arcade"
    #productMap["gifts"] = "Gifts and Gear"
    #productMap["fitness"] = "Fitness"
    
    # Graph Call need to get all Pre/Post items
    # Use other API call for all others
    productMap["preandpost"] = "Pre and Post"
    
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
            'sortKey': 'RANK',
            'sortKeyOrder': 'ASCENDING',
        },
        'filter': {
            'includeVariantProducts': False,
        },
        'currencyCode': currency,
        'includeFilterInfo': False,
        'includeIfABexperience': False,
    },
    'query': 'query WebProductsByCategory($category: String!, $passengerId: String, $shipCode: ShipCodeScalar!, $sailDate: LocalDateScalar!, $reservationId: String, $pageSize: Long, $currentPage: Long, $sorting: Sorting, $filter: FilterInput, $currencyCode: String!, $includeFilterInfo: Boolean!, $includeIfABexperience: Boolean!) {\n  products(\n    category: $category\n    guestTypes: [ADULT]\n    passengerId: $passengerId\n    shipCode: $shipCode\n    sailDate: $sailDate\n    reservationId: $reservationId\n    pageSize: $pageSize\n    currentPage: $currentPage\n    sorting: $sorting\n    filter: $filter\n    currencyIso: $currencyCode\n  ) {\n    ... on CommerceProductResultSuccess {\n      __typename\n      commerceProducts {\n        activityLevel {\n          code\n          description\n          name\n          __typename\n        }\n        ageRanges {\n          guestType\n          maxValue\n          minValue\n          __typename\n        }\n        approvalStatus {\n          code\n          name\n          __typename\n        }\n        bookingEligibility {\n          allowed\n          reason\n          __typename\n        }\n        categories {\n          code\n          name\n          description\n          subCategories {\n            code\n            name\n            description\n            order\n            __typename\n          }\n          __typename\n        }\n        classifications {\n          code\n          name\n          __typename\n        }\n        dayPorts {\n          day\n          name\n          portCode\n          port\n          __typename\n        }\n        media {\n          source {\n            altText\n            path\n            __typename\n          }\n          type\n          primary\n          __typename\n        }\n        price {\n          currency\n          promotionalPrice\n          shipboardPrice\n          formattedPromotionalPrice\n          formattedBasePrice\n          formattedDailyPrice\n          formattedPromoDailyPrice\n          salesUnit {\n            code\n            name\n            label\n            __typename\n          }\n          __typename\n        }\n        isAtYourLeisure\n        restrictions {\n          maxValue\n          minValue\n          operator\n          restrictionDisplayText\n          unit\n          __typename\n        }\n        references {\n          source\n          target\n          type\n          __typename\n        }\n        stock {\n          stockLevel\n          stockLevelStatus\n          __typename\n        }\n        type {\n          id\n          name\n          __typename\n        }\n        variantOptions {\n          code\n          offer\n          options {\n            code\n            name\n            label\n            sequence\n            __typename\n          }\n          __typename\n        }\n        accessible\n        id\n        baseId\n        defaultVariantId\n        variantType\n        categoryIds\n        title\n        shortDescription\n        trending @include(if: $includeIfABexperience) {\n          code\n          name\n          __typename\n        }\n        popular @include(if: $includeIfABexperience) {\n          code\n          name\n          __typename\n        }\n        new @include(if: $includeIfABexperience) {\n          code\n          name\n          __typename\n        }\n        recommended\n        mostPopular\n        productInterests {\n          code\n          name\n          __typename\n        }\n        complimentary\n        promotion {\n          description\n          value\n          type\n          title\n          displayName\n          code\n          __typename\n        }\n        isALaCarte\n        diningType\n        productStatus\n        description\n        highlights\n        __typename\n      }\n      filterInfo @include(if: $includeFilterInfo) {\n        locations {\n          code\n          name\n          count\n          city\n          region\n          day\n          __typename\n        }\n        activityLevels {\n          code\n          name\n          __typename\n        }\n        durations {\n          code\n          name\n          __typename\n        }\n        interests {\n          code\n          name\n          __typename\n        }\n        subcategories {\n          code\n          name\n          order\n          count\n          __typename\n        }\n        __typename\n      }\n      pageInfo {\n        totalResults\n        totalPages\n        __typename\n      }\n    }\n    ... on CommerceProductExceptions {\n      exceptions {\n        ... on InvalidCategoryException {\n          exceptionType\n          message\n          code\n          title\n          __typename\n        }\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}',
    }

        response = requests.post('https://aws-prd.api.rccl.com/en/royal/web/graphql', headers=headers, json=json_data)
    
        payload = response.json().get("data")
        if payload is None:
            continue
        
        products = payload.get("products").get("commerceProducts")
        if products is None:
            continue
     
        for product in products:
            title = product.get("title")
            price = product.get("price")[0].get("formattedBasePrice")
            if price == 0:
                continue
                           
            printString = title + " " + str(price) + " " + currency
            
            if product.get("salesUnit") in [ 'PER_DAY' ]:
                printString =  printString + " per day" 
            
            if product.get("salesUnit") in [ 'PER_NIGHT' ]:
                printString =  printString + " per night"
             
            # Promo % off is basically a scam, do not print it 
            #promoDescription = product.get("promoDescription")    
            #if promoDescription is not None:
            #    printString = printString + " - " + promoDescription.get("displayName")
            
            print(printString)
            
if __name__ == "__main__":
    main()
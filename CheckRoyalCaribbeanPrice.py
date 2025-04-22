import requests
import yaml
from apprise import Apprise
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import base64
import json

cruiseLineName = "royalcaribbean"
cruiseLineCode = "R"
appKey = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"

def main():
    global cruiseLineName
    global cruiseLineCode

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(timestamp)
    
    apobj = Apprise()
        
    with open('config.yaml', 'r') as file:
        data = yaml.safe_load(file)
        
        if 'apprise' in data:
            for apprise in data['apprise']:
                url = apprise['url']
                apobj.add(url)

        if 'apprise_test' in data and data['apprise_test']:
            apobj.notify(body="This is only a test. Apprise is set up correctly", title='Cruise Price Notification Test')
            print("Apprise Notification Sent...quitting")
            quit()

        if 'cruiseLineName' in data and data['cruiseLineName']:
            cruiseLineName = data['cruiseLineName']
        if 'cruiseLineCode' in data and data['cruiseLineCode']:
            cruiseLineCode = data['cruiseLineCode']              

        if 'accountInfo' in data:
            for accountInfo in data['accountInfo']:
                username = accountInfo['username']
                password = accountInfo['password']
                print(username)
                session = requests.session()
                access_token,accountId,session = login(username,password,session)
                getVoyages(access_token,accountId,session,apobj)
    
        if 'cruises' in data:
            for cruises in data['cruises']:
                if cruiseLineName in cruises['cruiseURL']: # Only get X cruises if Celebrity, RCCL if Royal Caribbean
                    cruiseURL = cruises['cruiseURL'] 
                    paidPrice = float(cruises['paidPrice'])
                    get_cruise_price(cruiseURL, paidPrice, apobj)
            
def login(username,password,session):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
    }
    
    data = 'grant_type=password&username=' + username +  '&password=' + password + '&scope=openid+profile+email+vdsid'
    
    response = session.post('https://www.'+cruiseLineName+'.com/auth/oauth2/access_token', headers=headers, data=data)
    
    if response.status_code != 200:
        print(cruiseLineName + " Website Might Be Down. Quitting")
        quit()
          
    access_token = response.json().get("access_token")
    
    list_of_strings = access_token.split(".")
    string1 = list_of_strings[1]
    decoded_bytes = base64.b64decode(string1 + '==')
    auth_info = json.loads(decoded_bytes.decode('utf-8'))
    accountId = auth_info["sub"]
    return access_token,accountId,session

def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,product,apobj):    
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'vds-id': accountId,
    }

    params = {
        'reservationId': reservationId,
        'startDate': startDate,
        'currencyIso': 'USD',
    }

    response = session.get(
        'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/catalog/v2/' + ship + '/categories/' + prefix + '/products/' + str(product),
        params=params,
        headers=headers,
    )
    
    title = response.json().get("payload").get("title")
    currentPrice = response.json().get("payload").get("startingFromPrice").get("adultPromotionalPrice")
    if not currentPrice:
        currentPrice = response.json().get("payload").get("startingFromPrice").get("adultShipboardPrice")
    
    if currentPrice < paidPrice:
        text = reservationId + ": Rebook! " + title + " Price is lower: " + str(currentPrice) + " than " + str(paidPrice)
        print(text)
        apobj.notify(body=text, title='Cruise Addon Price Alert')
    else:
        print(reservationId + ": " + "You have the best price for " + title +  " of: " + str(paidPrice))
        
    if currentPrice > paidPrice:
        print(reservationId + ": \t " + "Price of " + title + " is now higher: " + str(currentPrice))

def getVoyages(access_token,accountId,session,apobj):

    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'vds-id': accountId,
    }
        
    params = {
        'brand': cruiseLineCode,
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
        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj)
    
def getRoyalUp(access_token,accountId,session,apobj):
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
    
def getOrders(access_token,accountId,session,reservationId,passengerId,ship,startDate,numberOfNights,apobj):
    
    headers = {
        'Access-Token': access_token,
        'AppKey': appKey,
        'Account-Id': accountId,
    }
    
    params = {
        'passengerId': passengerId,
        'reservationId': reservationId,
        'sailingId': ship + startDate,
        'currencyIso': 'USD',
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
        
        # Only get Valid Orders That Cost Money
        if order.get("orderTotals").get("total") > 0: 
            
            # Get Order Details
            response = requests.get(
                'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory/' + orderCode,
                params=params,
                headers=headers,
            )
                    
            for orderDetail in response.json().get("payload").get("orderHistoryDetailItems"):
                # check for cancelled status at item-level
                if orderDetail.get("guests")[0].get("orderStatus") == "CANCELLED":
                    continue
                order_title = orderDetail.get("productSummary").get("title")
                product = orderDetail.get("productSummary").get("id")
                prefix = orderDetail.get("productSummary").get("productTypeCategory").get("id")
                paidPrice = orderDetail.get("guests")[0].get("priceDetails").get("subtotal")
                # These packages report total price, must divide by number of days
                if prefix == "pt_beverage" or prefix == "pt_internet":
                   paidPrice = round(paidPrice / numberOfNights,2)
                   
                getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,product,apobj)

def get_cruise_price(url, paidPrice, apobj):

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
    
    response = requests.get('https://www.'+cruiseLineName+'.com/checkout/guest-info', params=params,headers=headers)
    
    preString = params.get("sailDate")[0] + " " + params.get("shipCode")[0]+ " " + params.get("cabinClassType")[0] + " " + params.get("r0f")[0]
    
    roomNumberList = params.get("r0j")
    if roomNumberList:
        roomNumber = roomNumberList[0]
        preString = preString + " Cabin " + roomNumber
    
    soup = BeautifulSoup(response.text, "html.parser")
    soupFind = soup.find("span",attrs={"class":"SummaryPrice_title__1nizh9x5","data-testid":"pricing-total"})
    if soupFind is None:
        m = re.search("\"B:0\",\"NEXT_REDIRECT;replace;(.*);307;", response.text)
        if m is not None:
            redirectString = m.group(1)
            textString = preString + ": URL Not Working - Redirecting to suggested room"
            print(textString)
            newURL = "https://www." + cruiseLineName + ".com" + redirectString
            get_cruise_price(newURL, paidPrice, apobj)
            print("Update url to: " + newURL)
            return
        else:
            textString = preString + " No Longer Available To Book"
            print(textString)
            apobj.notify(body=textString, title='Cruise Room Not Available')
            return
    
    priceString = soupFind.text
    priceString = priceString.replace(",", "")
    m = re.search("\\$(.*)USD", priceString)
    priceOnlyString = m.group(1)
    price = float(priceOnlyString)
    
    if price < paidPrice: 
        textString = "Rebook! " + preString + " New Price of "  + str(price) + " is lower than " + str(paidPrice)
        print(textString)
        apobj.notify(body=textString, title='Cruise Price Alert')
    else:
        print(preString + ": You have best Price of " + str(paidPrice) )
        if price > paidPrice:
            print("\t Current Price is higher: " + str(price) )

if __name__ == "__main__":
    main()
 

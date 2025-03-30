import requests
import yaml
from apprise import Apprise
from datetime import datetime

def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(timestamp)
    
    apobj = Apprise()
        
    with open('config.yaml', 'r') as file:
        data = yaml.safe_load(file)
        username = data['username']
        password = data['password']
        accountId = data['accountId']
        if 'apprise' in data:
            for apprise in data['apprise']:
                url = apprise['url']
                apobj.add(url)
                
    session = requests.session()
    access_token,session = login(username,password,session)
    getVoyages(access_token,accountId,session,apobj)
    
            
def login(username,password,session):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': 'https://www.royalcaribbean.com/account/signin',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ZzlTMDIzdDc0NDczWlVrOTA5Rk42OEYwYjRONjdQU09oOTJvMDR2TDBCUjY1MzdwSTJ5Mmg5NE02QmJVN0Q2SjpXNjY4NDZrUFF2MTc1MDk3NW9vZEg1TTh6QzZUYTdtMzBrSDJRNzhsMldtVTUwRkNncXBQMTN3NzczNzdrN0lC',
        'Origin': 'https://www.royalcaribbean.com',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Priority': 'u=0',
    }
    


    data = 'grant_type=password&username=' + username +  '&password=' + password + '&scope=openid+profile+email+vdsid'

    response = session.post('https://www.royalcaribbean.com/auth/oauth2/access_token', headers=headers, data=data)
    access_token = response.json().get("access_token")
    return access_token,session

def getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,product,apobj):
    
    
    
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
        text = reservationId + ": " + title + " Price is lower: " + str(currentPrice) + " Rebook"
        print(text)
        apobj.notify(body=text, title='Price Alert')
    else:
        print(reservationId + ": " + "You have the best price for " + title +  " of: " + str(paidPrice))
        
    if currentPrice > paidPrice:
        print(reservationId + ": \t" + "Price of " + title + " is now higher: " + str(currentPrice))

def getVoyages(access_token,accountId,session,apobj):

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
    'brand': 'R',
    'includeCheckin': 'true',
}
    
    response = requests.get(
        'https://aws-prd.api.rccl.com/v1/profileBookings/enriched/' + accountId,
        params=params,
        headers=headers,
    )
    
    
    for booking in response.json().get("payload").get("profileBookings"):
        reservationId = booking.get("bookingId")
        passengerId = booking.get("masterPassengerId")
        sailDate = booking.get("sailDate")
        numberOfNights = booking.get("numberOfNights")
        shipCode = booking.get("shipCode")
        getOrders(access_token,accountId,session,reservationId,passengerId,shipCode,sailDate,numberOfNights,apobj)
    
    
def getOrders(access_token,accountId,session,reservationId,passengerId,ship,startDate,numberOfNights,apobj):
    
    
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
        if order.get("status") == 'BOOKED' and order.get("orderTotals").get("total") > 0: 
            
            # Get Order Details
            response = requests.get(
                'https://aws-prd.api.rccl.com/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory/' + orderCode,
                params=params,
                headers=headers,
            )
                    
            orderDetail = response.json().get("payload").get("orderHistoryDetailItems")[0]
            order_title = orderDetail.get("productSummary").get("title")
            product = orderDetail.get("productSummary").get("id")
            prefix = orderDetail.get("productSummary").get("productTypeCategory").get("id")
            paidPrice = orderDetail.get("guests")[0].get("priceDetails").get("subtotal")
            # These packages report total price, must divide by number of days
            if prefix == "pt_beverage" or prefix == "pt_internet":
               paidPrice = round(paidPrice / numberOfNights,2)
               
            getNewBeveragePrice(access_token,accountId,session,reservationId,ship,startDate,prefix,paidPrice,product,apobj)
    
if __name__ == "__main__":
    main()
 

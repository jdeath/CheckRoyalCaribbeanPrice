import requests
import yaml
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import base64
import json
import argparse
import locale
import os

appKey = "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"

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
    print("This only works if you are onboard on the ship WiFi")
    print("Internet Package is NOT required")
   
    with open(config_path, 'r') as file:
        data = yaml.safe_load(file)
        if 'dateDisplayFormat' in data:
            global dateDisplayFormat
            dateDisplayFormat = data['dateDisplayFormat']

        print(timestamp.strftime(dateDisplayFormat + " %X"))

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
                getPhotos(access_token,accountId,session)
            
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
    print("C&A: " + str(cAndANumber) + " " + cAndALevel + " " + str(cAndAPoints) + " Points")  
    
def getPhotos(access_token,accountId,session):
    
    headers = {
        'appVersion': '1.70.1',
        'Access-Token': access_token,
        'AppKey': 'cdCNc04srNq4rBvKofw1aC50dsdSaPuc',
        'vds-id': accountId,
        'Account-id': accountId,
        'X-Request-Id': '23af89e9-9881-4a25-948b-4e3dd527f90f',
        'Accept':           'application/json',
        'Platform':         'android',
        'User-Agent':       'royal/1.70.1 (com.rccl.royalcaribbean; build:2479; android 16) okhttp/4.10.0',
        'Accept-Language':  'en',
        'Host':             'api.rccl.com',
        'Connection':       'Keep-Alive',
        'Accept-Encoding':  'gzip'
    }
    
    response = requests.get('https://api.rccl.com/en/royal/mobile/v1/digital-photo/media', headers=headers)
    
    payload = response.json().get("payload")
    for item in payload:
        cabinImageId = item.get("cabinImageId")
        dateTaken = item.get("dateTaken")
        photoURL = item.get("uris").get("high")
        local_filename = str(cabinImageId) + "_" + dateTaken + ".jpg" # Choose a name for the saved file 
        local_filename = local_filename.replace(':', '-')
        if os.path.isfile(local_filename):
            print(f"Photo '{local_filename}' already downloaded!")
            continue
        response = requests.get(photoURL)
        if response.status_code == 200:        
            with open(local_filename, "wb") as f:
                f.write(response.content)
                print(f"Photo '{local_filename}' downloaded successfully!")
                
if __name__ == "__main__":
    main()
 
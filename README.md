# CheckRoyalCaribbeanPrice
Checks if you have the cheapest price for your **Royal Caribbean** and **Celebrity Cruises** purchases (beverage packages, excursions, internet, etc.).  
- ✅ Automatically checks your purchased packages (no need to enter them manually)  
- ✅ Alerts you if a lower price is available
- ✅ Finds deals specific to each passenger (loyalty or casino status, age-based or room specials) where other trackers only find publicly available prices
- ✅ Shows currently assigned cabin in Royal's backend system (*likely* the room you will get if purchased a GTY "We choose your room")
- ✅ Shows the payment balance Royal's backend system thinks they are owed (does not include TA's take!)
- ✅ Supports multiple Royal and Celebrity accounts or linked cruises
- ✅ Handles all currencies (checks each item based on the currency used to purchase it)
- ✅ Can also check **cabin prices** with just a booking URL (no login required)  
- ✅ Runs on Windows, macOS, Linux, Docker, and Home Assistant.
- ✅ Completely open source, free to use or modify.
- ✅ Separate `BrowseRoyalCaribbeanPrice.py` script lets you look up any cruise's addon prices, no setup required
   

> ⚠️ This is **not a hack**. All API calls and data are publicly available. The script simply automates what you can do on the Royal Caribbean website.

[![Stargazers repo roster for @jdeath/CheckRoyalCaribbeanPrice](https://reporoster.com/stars/jdeath/CheckRoyalCaribbeanPrice)](https://github.com/jdeath/CheckRoyalCaribbeanPrice/stargazers)

If the code saved you money or correctly predicted your cabin number, star the repo and/or post your success on [r/RoyalCaribbean](https://www.reddit.com/r/royalcaribbean/) !

## Install (Recommended, any Operating System, and you can edit code to your liking)
1. Install python3 (3.12 works fine) `https://www.python.org/downloads/`
1. Download the [CheckRoyalCaribbeanPrice.py](https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/CheckRoyalCaribbeanPrice.py) from this repo or `git clone https://github.com/jdeath/CheckRoyalCaribbeanPrice.git`
1. `pip install requests Apprise bs4`

## Install (Windows 10/11 Only)
1. Download `CheckRoyalCaribbeanPrice.exe` from release assets `https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases`
    -   Note: A windows .exe is auto created upon every release, but the Python code in repo may be newer. 
    -   Only if you want to build a binary yourself, you can run `pyinstaller -F --collect-all apprise --collect-all bs4 CheckRoyalCaribbeanPrice.py`
2. Make the config.yaml file as described below in the Edit Config File section
   -   Note: if you make a text file in windows with New->Text file , it may look like it is named `config.yaml`, but it is actually named `config.yaml.txt` . In the windows file browser, go to View->Show and make sure "File Name Extensions" is checked. Then remove the .txt from the end of the file so it is actually named `config.yaml`.
## Install (Docker Option - thanks @JDare)

### Single Execution (One-time price check)
For a single price check without scheduling:
```bash
docker run --rm \
  -v ./config.yaml:/app/config.yaml:ro \
  ghcr.io/jdeath/checkroyalcaribbeanprice:latest \
  check
```

### Scheduled Execution
#### Option 1: Using Pre-built Image
1. Create a `docker-compose.yml` file:
```yaml
services:
  cruise-price-checker:
    image: ghcr.io/jdeath/checkroyalcaribbeanprice:latest
    container_name: cruise-price-checker
    restart: unless-stopped
    environment:
      # Timezone for cron execution (default: UTC)
      # Examples: America/New_York, America/Chicago, America/Los_Angeles, Europe/London
      # Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
      - TZ=America/New_York
      # Cron schedule: 7 AM and 7 PM daily in the specified timezone
      - CRON_SCHEDULE=0 7,19 * * *
    volumes:
      # Mount your config file
      - ./config.yaml:/app/config.yaml:ro
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```
2. Create your `config.yaml` file (see "Edit Config File" section below)
3. Run: `docker compose up -d`

#### Option 2: Build from Source
1. Clone this repository: `git clone https://github.com/jdeath/CheckRoyalCaribbeanPrice.git`
2. `cd CheckRoyalCaribbeanPrice`
3. Create your `config.yaml` file (see "Edit Config File" section below)
4. Run: `docker compose up -d`

The Docker container will run the price checker on the schedule you have defined.
## Edit Config File
Create your `config.yaml` file with the below information. Feel free to copy the file `SAMPLE-config.yaml` to `config.yaml`. Edit `config.yaml` and place it in same directory as `CheckRoyalCaribbeanPrice.py` or `CheckRoyalCaribbeanPrice.exe` or when running `CheckRoyalCaribbeanPrice.py` provide the optional argument `-c path/to/config.yaml`.
```
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
  - username: "user@gmail.com" # Your Celebrity User Name
    password: "pa$$word" # Your Celebrity Password (ensure no % in password)
    cruiseLine: "celebrity" # Must indicate if celebrity
cruises:
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74"
  - cruiseURL: "https://www.celebritycruises.com/checkout/guest-info?groupId=RF04FLL-1098868345&packageCode=RF4BH246&sailDate=2025-08-11&country=USA&selectedCurrencyCode=USD&shipCode=RF&cabinClassType=INTERIOR&category=I&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=Y&r0f=Y&r0g=BESTRATE&r0h=n&r0A=1127.6" # Can have as many URLS and price paid as you want. Supports Celebrity too
    paidPrice: "1127.6"   
apprise_test: false # Optional
apprise:  # Optional, see https://github.com/caronc/apprise, can have as many lines as you want.
  - url: "mailto://user:password@gmail.com"
  - url: "whatsapp://AccessToken@FromPhoneID/ToPhoneNo"
```

If you only want to check cruise addons (drink packages, excursions, etc) and do not want emails or check cruise prices, the config file is simpler. Start with this to see if works. You can have any number of Royal and/or Celebrity accounts:
```
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal
```


If you only want to check cruise price and do not want emails, the account information is not needed by the tool. Config file can look like this:

```
cruises:
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74" 
```


If you would like to assign names to cruise reservation numbers to more easily correlate which cruise is being displayed populate the following section:
```
reservationFriendlyNames:
  '1234567': "Summer Cruise"
  '8912345': "Winter Cruise
```

To override the system's default date format, set the dateDisplayFormat config value to your desired format:
```
dateDisplayFormat: "%m/%d/%Y"
```

To override the currency from what the API returns (what you bought the item in), set the currencyOverride config value to your desired currencyOverride. This should not be needed and should now only be needed for testing.
```
currencyOverride: 'DKK'
```

## Get Cruise URL (Optional)
1. Be sure you are logged out of the Royal Caribbean / Celebrity Website. If you are logged in, the URL you get in Step 5 will not work.
1. Go to Royal Caribbean or Celebrity and do a mock booking of the room you have, with the same number of adults and kids
1. Select a cruise and Select your room type/room and complete until they ask for your personal information.
1. At this point, you should see a blue bar at the bottom right of webpage with a price
1. Copy the entire URL from the top of your browser into the cruiseURL field. The url should start with `https://www.royalcaribbean.com/checkout/guest-info?...` or `https://www.celebritycruises.com/checkout/guest-info?...` where `...` is a bunch of stuff. Copy the entire URL
1. Put the price you paid in the paidPrice field. Remove the `$` and any `,`
1. Run the tool and see if it works
1. You can add multiple cruiseURL/paidPrice to track multiple cruises or rooms on a cruise
1. If the code says the price is cheaper, do a mock booking to see if cabin is still available. You need to do this from a new search on the Royal Caribbean / Celebrity website. Do not just put the cruiseURL in your browser. It is possible the room is not available. Going to the cruiseURL directly might give you a false alarm and you will look like an idiot calling your travel agent!
1. If it is lower than you paid for, the cabin is still bookable, and before final payment date call your Travel Agent or Royal Caribbean (if you booked direct) and they will reduce the price. Be careful, you will lose the onboard credit you got in your first booking, if the new booking does not still offer it!
1. Update the pricePaid field to the new price. Remove the `$` and any `,`
1. Works easiest on a Guarantee Cabin, where multiple of same cabin exist for purchase. If checking a "You Pick the Room", be sure to check the price of the same class of room you booked (Connecting Balcony, Balcony class 4D or 4A , etc). If the room you picked is no longer available, you need to get a URL of another room in that class. If there are no more rooms of the same class available to book, you will not be able to reprice. You will need to manually check back on the Royal website to see if a room opened up. The API does not return cruise prices, so we are left with scraping the website.
1. If you only want to check the cruise prices, you do not need to have your `accountInfo` and/or `apprise` in your config file, as they are not necessary.
   
## Apprise (Optional)
1. Review documentation for apprise at: https://github.com/caronc/apprise
1. 99% of people probably have gmail, so you can use the default already setup in the sample config.yaml
1. This will send you an email only if there is a price drop
1. Change username to your gmail username
1. Change password to your gmail password. If you use 2-factor authentication, you need to generate an app password. You cannot use use normal password
   - Documentation to generate an app password for gmail is here: https://security.google.com/settings/security/apppasswords
1. You can delete the whatsapp line, that is included so you know how to add other services. You can also add more lines for an additional gmail accounts.
1. To test apprise, add a key in your config.yaml that says `apprise_test: true` . This will send a notification, then quit and not run the price check. This key goes above the `apprise:` keys not inside it (see `Edit Config File` section above). Once you know apprise is working, remove the line or set value to `false`

## Run
1. `python CheckRoyalCaribbeanPrice.py` (recommended, any OS) or `CheckRoyalCaribbeanPrice.exe` (Windows only)
    - It will indicate if you should rebook or if you have the best price
    - It will also tell you if the price has gone up since you purchased (do not rebook in that case!)
    - If you setup apprise, it will notify you via your preferred method(s) if you should rebook
    - Will provide you a link to the order history for that cruise and also tell you the date/order number to cancel from that list
      - Log in to Royal Caribbean on the web browser before clicking the link, or the link will not bring you to the correct location
      - (There does not appear to be a way to construct a Web link to that specific order. If you find one, please let me know via an issue)  
    - After cancelling/modify the order, click the product image to reorder.

## Output
Will output information on your purchases (redacted output below)
```
09/04/25 06:02:01
royalcaribbean me@email.com
C&A: XXXXXXXXX DIAMOND 100 Points
CONFNUM1: 09/11/25 Quantum of the Seas Room 1234 (Mary, Jane)
Mary   (1234) has best price for La Cava de Marcelo: The Cheese Cave of: 84.99 (now 134.0)
Jane   (1234) has best price for La Cava de Marcelo: The Cheese Cave of: 84.99 (now 134.0)
Mary   (1234) has best price for Deluxe Beverage Package of: 56.99 (now 62.99)
Jane   (1234) has best price for Deluxe Beverage Package of: 56.99 (now 62.99)

CONFNUM2: 09/15/25 Brilliance of the Seas Room GTY (John, Mary)
John   (1234) has best price for Old and New San Juan City Tour with Airport Drop-Off of: 54.99 (now 99.0)
Mary   (1234) has best price for Old and New San Juan City Tour with Airport Drop-Off of: 54.99 (now 99.0)
John   (1234) has best price for Deluxe Beverage Package of: 62.99 (now 72.99)
Mary   (1234) has best price for Deluxe Beverage Package of: 62.99 (now 72.99)
Mary   (1234) has best price for VOOM SURF + STREAM Internet Package of: 16.99 (now 18.99)

2025-12-27 Vision of the Seas OUTSIDE 4N: You have best Price of 3612.12 
```
If any of the prices are lower, it will send a notification if you set up apprise. Notification will include a link to your order history and the specific date and order number to cancel. Notice on the 2nd reservation, the official room is GTY but the excursions show the currently assigned room in the Royal backend system. This room is likely what you will get!

## Automating
1. Linux: Put in a cron job, if running in linux, I am sure you know how! Be sure to either provide optional argument for the `config.yaml` path or be sure to execute the script from within the directory where the configuration script is present.
1. Home Assistant: Use directions in my [repo](https://github.com/jdeath/homeassistant-addons/tree/main/royalpricecheck)
1. Docker: See directions in docker section above
1. Windows: Use windows task schedular
    1. Create a basic task. Select a daily trigger, suggest a little before you wake up
    1. Action, select "Start a Program"
    1. In "Program/script" Select the CheckRoyalCaribbeanPrice.exe file you download from here. Make sure the config.yaml is in same directory as .exe (if running python script, should be able to put python.exe the full path of this the script location)
    1. In "Start in (optional)" enter the directory of the .exe/.yaml (you can copy the "Program/script" field, paste it, and remove the CheckRoyalCaribbeanPrice.exe)
    1. After clicking finish, you can right click on task, go to triggers, and add more times to trigger the script. Suggest a time right before you get home from work. Twice a day should be sufficient
    1. Ensure apprise notifications are working, because the window will close automatically after run.

## Other Notes
**Want to monitor a friend's cruise?** You can either link their cruise to your account or add their account the `config.yaml` account list. On the Royal Website, you need their reservation number, name, and birthdate to link the cruise to your account (select my name is not listed). Then this code will check their packages which avoids needing their username/password. For linked reservations, the passenger may appear to be in the wrong room. This is just a feature of the code which I cannot seem to fix. The correct passengers' first names booked in each room will be shown for each booking. If the item was purchased by someone besides the account being used to check the price, the email will notify you that someone else must cancel/rebook. The code cannot tell you who actually booked it. Note, linked reservations can be confusing to cancel/rebook. If the Royal App/Website says you cannot cancel the reservation because you did not make it, you need to try all combonations. For instance, try looking at the orders on your account on "My Cruise" and also the orders on your account but on "Linked Cruise".
If you have their username/password, you can add it to the list of accounts in the config.yaml and it will cycle though accounts automatically.

**Do you have a GTY Room and want to know the room you will likely get?** If a room is not officially assigned yet, the code displays GTY (meaning guarantee) for your room number. However, any excursion purchased will show the passenger's name and the room number currently associated with that excursion. Guess what? That room number is likely the room you will be officially assigned. Confirmed by the author, please post an issue if you can confirm this as well.

**Are you browsing the website for the best prices?** Always add the item to your cart and then go to the check phase where you enter your credit card. Often the price will be lower in the screen where you enter your credit card then in your cart. If on the fense, do the extra step and you may be suprised!

## Related Tools

- [RoyalPriceTracker.com](https://royalpricetracker.com/) – simpler, but you must enter purchases manually, public price only  
- [CruiseSpotlight Price Lookup](https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/) – public price lookup for any cruise  
- `BrowseRoyalCaribbeanPrice.py` – included here for fun; lets you explore public prices with one script  

## Credits

Thanks to contributors:
- Anonymous (Retrieve AccountID programmatically)
- @cyntil8 (Celebrity support, per-day pricing)  
- @tecmage (UDP, Coffee Card, Evian Water logic)  
- @iareanthony (fixed "The Key")  
- @jipis (internet pricing & passenger specials)  
- @ProxesOnBoxes (date display options, config improvements)  
- @RoyalCaribbeanBlog.com for featuring in an [article](https://www.royalcaribbeanblog.com/2025/04/19/cruise-price-trackers)
# Issues
1. Will not work if your password has an % in it. Change your password (replace % with ! for instance). Working on a fix. PRs welcome
1. Only checks adult prices (> 12 years old), if only have child prices in an order it may not work. I don't have kids, so I can not fix. PRs welcome
1. Handles orders made by other people in your party or linked cruises (even if you are not sailing on it)
1. It should give you the price of the item in the same currency you bought it in. Post an issue if not working correctly.
1. May not handle all orders correctly. Purchases of multiple coffee cards and Evian water should now work.
1. Prices of internet, beverage packages, and "The Key" are per day, this code divides by the length of your cruise. If you buy a partial package, this logic may not work correctly. If any per-day item is not calculated correctly, post an issue.
1. Please double check that the price is lower before you rebook! I am not responsible if you book at a higher price!
1. Double check you are cancelling the item for the correct cruise

# Browse RoyalCaribbean Prices
This is a new script that will browse any Royal Caribbean or Celebrity sailing and show current public prices for excursions/drink packages/etc. If you book the cruise, the price could be lower than shown due to C&A or casino specials. You simply run the script `python BrowseRoyalCaribbeanPrice.py` or `BrowseRoyalCaribbeanPrice.exe`. It will prompt you to select the ship and sailing. It will provide a link to the Royal Caribbean website which has the product prices for that cruise (be sure to be logged out of the RC website or link will not work). Code will also print all the prices. This does not require a Royal Caribbean or Celebrity account and can be used by anyone. Inspired by and similar functionality to `https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/`. Defaults to USD currency. If you want a different currency, for example DKK, run `python BrowseRoyalCaribbeanPrice.py -c DKK` or  `BrowseRoyalCaribbeanPrice.exe -c DKK`

There are no plans to add price checking/price history to this script. Use the `CheckRoyalCaribbeanPrice.py` script for that. If you really want to check public prices which may not be representative of the real deal you can get, just use `RoyalPriceTracker.com`.

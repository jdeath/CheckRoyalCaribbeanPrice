# CheckRoyalCaribbeanPrice
Checks if you have the cheapest price for your **Royal Caribbean** and **Celebrity Cruises** purchases (beverage packages, excursions, internet, etc.).  
- ✅ Automatically checks your purchased packages (no need to enter them manually)  
- ✅ Alerts you if a lower price is available (email, ntfy, Home Assistant, etc) 
- ✅ Finds deals specific to each passenger (loyalty or casino status, age-based or room specials) where other "royal price trackers" only find publicly available (often higher) prices
- ✅ Shows currently assigned cabin in Royal's backend system (*likely* the room you will get if purchased a GTY "We choose your room")
- ✅ Shows the payment balance Royal's backend system thinks they are owed (does not include TA's take!)
- ✅ Supports multiple Royal and Celebrity accounts or linked cruises
- ✅ Handles all currencies (checks each item based on the currency used to purchase it)
- ✅ Can automatically check **cabin prices** for any cruise you booked
- ✅ Can create a "watchlist" to check prices of items you have not purchased (thanks @jhedlund)  
- ✅ Can also watchlist **cabin prices** with just a booking URL (no login required)  
- ✅ Runs on Windows, macOS, Linux, Docker, iOS, and Home Assistant.
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

## Install (iOS / iPhone - May work for Android too)
This will run a stripped down version to work on the free Python iPhone app. As stripped down, it only supports excursion/drink packages etc. It does not support cruise fare price checks. It does not support apprise notifications, so you will have to watch the log to see any price drops. You need to edit the python file directly (directions below) because it does not use the config.yaml file. But allows you to check prices on the go. Works on the ship even *without* the internet package!

1. Get Python From Appstore. `https://apps.apple.com/us/app/python-coding-editor-ide-app/id6444399635`
   -   Free version is fine, no need to make inapp purchases
   -   Please let me know if there is an Android app equivalent that works
2. Download `https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/PhonePriceCheck.py` from the repo to your computer
   -   Use a text editor to add your username and password between the "" a few lines down.
   -   If you are are using a Celebrity account, remove `#` before `#cruiseLineName = "celebritycruises"`
   -   Ignore the `Edit Config File` section below, that only pretains to computer installations
3. Email yourself the edited `PhonePriceCheck.py`
   -    On your iPhone, save the emailed `PhonePriceCheck.py` to your files section. This can be done by clicking the attachment, select share, then select saved files
4. Open Python App
   -    Tap the blue hamburger icon just below the adverstisement
   -    Tap "Load from File"
   -    Select the PhonePriceCheck.py file you downloaded
   -    To run: tap the arrow icon at top right of screen (between a bug icon and a `...` icon)
6. Look for any price drops in the output

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

## Install (Home Assistant Addon/App)
See directions at: https://github.com/jdeath/homeassistant-addons/tree/main/royalpricecheck

## Edit Config File
Create your `config.yaml` file with the below information. Feel free to copy the file `SAMPLE-config.yaml` to `config.yaml`. Edit `config.yaml` and place it in same directory as `CheckRoyalCaribbeanPrice.py` or `CheckRoyalCaribbeanPrice.exe` or when running `CheckRoyalCaribbeanPrice.py` provide the optional argument `-c path/to/config.yaml`.
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
  - username: "user@gmail.com" # Your Celebrity User Name
    password: "pa$$word" # Your Celebrity Password (ensure no % in password)
    cruiseLine: "celebrity" # Must indicate if celebrity
displayCruisePrices: true # Optional, this will display current price for your booked cruises
minimumSavingAlert: 0.00 # Optional, only alert when savings are >= this amount (per-night/per-day items use total savings per item)
cruises: # Optional, this allows you to watch the price of a cruise you have not booked yet
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74"
  - cruiseURL: "https://www.celebritycruises.com/checkout/guest-info?groupId=RF04FLL-1098868345&packageCode=RF4BH246&sailDate=2025-08-11&country=USA&selectedCurrencyCode=USD&shipCode=RF&cabinClassType=INTERIOR&category=I&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=Y&r0f=Y&r0g=BESTRATE&r0h=n&r0A=1127.6" # Can have as many URLS and price paid as you want. Supports Celebrity too
    paidPrice: "1127.6"   
apprise_test: false # Optional
apprise:  # Optional, see https://github.com/caronc/apprise, can have as many lines as you want.
  - url: "mailto://user:password@gmail.com"
  - url: "ntfy://abcfeg3839439djd"
```

If you only want to check cruise addons (drink packages, excursions, etc) and do not want emails or check cruise prices, the config file is simpler. Start with this to see if works. You can have any number of Royal and/or Celebrity accounts:
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal
```

To display current cabin prices for your **booked** cruise(s), set displayCruisePrices to true. This will request the current price from Royal's website. The code automatically determines the number of adults and children from your booking. So the price should be accurrate.  The script will not tell you if there is a loyality special, but will find any publically offered OBC and display it (but not subtract it because it only given in USD). The script will tell you if the cabin class (Interior, Balcony, Connecting Balcony, etc) you booked is no longer for sale, which means you cannot reprice. The script will also tell you if you are beyond the final payment date (75-120 days before departure depending on length of cruise), which also means you cannot reprice.
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal
displayCruisePrices: true
```
If you want to compare cabin prices for your **booked** cruise(s), include the following info in your config, where XXXXXX and YYYYY are your reservation ID. The price can only have a `.` or `,` for the decimal place, do not use an indicator for thousands place. You must provide the price you paid as is not possible to look up via the API. Enter the price paid including taxes and subtract any OBC you received, but excluding any upgrades or gratuities. The code will identify if new booking has OBC and display it (but not subtract it since always give in USD). If price is lower and before the final payment date (even if you paid in full), do a mock booking on the website to confirm then call your travel agent. 
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password (ensure no % in password)
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal
displayCruisePrices: true
reservationPricePaid:
  'XXXXXX': 4568.48
  'YYYYYY': 4172.71
```
If you only want to check cruise prices you have **not** booked yet and do not want email notifications, the account information is not needed by the tool. Config file can look like this:
```yaml
cruises:
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74" 
```

If you would like to assign names to cruise reservation numbers to more easily correlate which cruise is being displayed populate the following section:
```yaml
reservationFriendlyNames:
  '1234567': "Summer Cruise"
  '8912345': "Winter Cruise
```

To override the system's default date format, set the dateDisplayFormat config value to your desired format:
```yaml
dateDisplayFormat: "%m/%d/%Y"
```

To override the currency from what the API returns (what you bought the item in), set the currencyOverride config value to your desired currencyOverride. This should not be needed and should now only be needed for testing.
```yaml
currencyOverride: 'DKK'
```

To only alert when a price drop meets a minimum savings threshold, set minimumSavingAlert. For items priced per night/per day, the threshold compares against the total savings per item across the cruise. Use case is prices change fluctuate and not worth it to you for cance/rebook. If not set or set to 0.00, alerts trigger on any price drop as before.
```yaml
minimumSavingAlert: 2.00
```


## Get Cruise URL for Watchlist Functionality (Optional - This is only for a cruise you have not booked!)
1. If you want to check the cabin price of a cruise you have booked, see above. This section is just for cruises you have *not* booked yet.
1. Be sure you are logged out of the Royal Caribbean / Celebrity Website. If you are logged in, the URL you get in Step 5 will not work.
1. Go to Royal Caribbean or Celebrity and do a mock booking of the room you want, with the same number of adults and kids
1. Select a cruise and select your room type/room and complete until they ask for your personal information.
1. At this point, you should see a blue bar at the bottom right of webpage with a price
1. Copy the entire URL from the top of your browser into the cruiseURL field. The url should start with `https://www.royalcaribbean.com/checkout/guest-info?...` or `https://www.celebritycruises.com/checkout/guest-info?...` where `...` is a bunch of stuff. Copy the entire URL
1. Put the price you paid in the paidPrice field. Remove the `$` and any `,` . Subtract any OBC you recieved from Royal or your TA.
1. Run the tool and see if it works
1. You can add multiple cruiseURL/paidPrice to track multiple cruises or rooms on a cruise
1. If the code says the price is cheaper, do a mock booking to see if cabin is still available. You need to do this from a new search on the Royal Caribbean / Celebrity website. Do not just put the cruiseURL in your browser.
1. If it is lower than you paid for and before final payment date call your Travel Agent or Royal Caribbean (if you booked direct) and they should (reports of pushback lately) reduce the price. Be careful, you will lose the onboard credit you got in your first booking, if the new booking does not still offer it! The code will print the OBC offered for the new cruise, but will not subtract it because OBC only given in USD
1. Update the pricePaid field to the new price. Remove the `$` ,`£` and any `,` (or `.` if non-USD currency for thousands designator)
1. If there are no more rooms of the same class available to book, you will not be able to reprice. You will need to wait until a room opens up. The code will print the cheapest interior, outside view, balcony or suite available. These are probably GTY for each class and not the exact type of room you wanted. This is all the public cruise price API returns.
1. If you only want to check the cruise prices with URL you provide, you do not need to have your `accountInfo` and/or `apprise` in your config file, as they are not necessary.
1. The latest version checks availabily only for the class of room you have (not a specific room number). This new way is better.
1. Should always find the current currency (except for OBC which is only in USD). If your currency is not supported, create an issue
   
## Watch List for Beverage Packages/Excursions/etc (Optional)
The watch list feature allows you to monitor specific cruise add-ons for price drops across all your bookings. When enabled, the system will check each passenger individually for the specified items and alert you if prices drop below your target price.

### Configuration
Add a `watchList` section to your `config.yaml` file:

```yaml
watchList: # Optional, items to monitor for price drops across all your bookings
  - name: "Deluxe Beverage Package"
    prefix: "pt_beverage"  # Category prefix
    product: "3005"        # Product ID
    price: 85.00           # Alert if current price drops below this amount. Use per night w/o gratutity price
    enabled: true          # Set to false to temporarily disable this item
    currency: "GBP"        # Optional currency code, defaults to "USD" if not set
    guestAgeString: "child" # "infant", "child", "adult" are only options. Optional, defaults to "adult" if not set.
    reservations: ['XXXXXXX', 'YYYYYYY'] # Optional. Check watchlist only for these reservation numbers. If not present, defaults to check all reservations   
  - name: "Premium WiFi 2 Device Package"
    prefix: "pt_internet"
    product: "33F1"
    price: 30.00
    enabled: false         # This item will be skipped
```

### How It Works
- **Per-Passenger Checking**: Each watchlist item is checked individually for every passenger in your bookings
- **Individual Pricing**: Passengers may have different pricing based on loyalty status, age, or room category
- **Output Format**: Results show as `[WATCH] Item Name - Passenger (Room): Message`
- **Enabled Control**: Use the `enabled` field to temporarily disable specific watchlist items without removing them

### Finding Product Information
To find the `prefix` and `product` values for items you want to watch:
1. Go to your Cruise Planner website and browse to the package you want to watch
2. Inspect the URL to find the `prefix` and `product`, for example for the Premium WIFI 2 Device Package the URL looks like:
   `https://www.celebritycruises.com/account/cruise-planner/category/pt_internet/product/33F1?bookingId=&shipCode=&sailDate=`
3. The `prefix` is the path following /category/ (`pt_internet` in this case)
4. The `product` is the value following /product/ (`33F1` in this case)
5. Use the advertised price in the cruise panner. Eg. Doo not include gratituty. Use per day price for Beverage Package, UDP, Internet, Key.
### Example Output
```
[WATCH] Deluxe Beverage Package - John (1234): Book! Deluxe Beverage Package Price is lower: 75.00 than 85.00
[WATCH] Internet Package - Mary (1234): price is higher than watch price: 25.00 (now 30.00)
```

## Notification Emails/Pushbullet/etc via Apprise (Optional)
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
**Want to monitor a friend's cruise?** You can either link their cruise to your account or add their account the `config.yaml` account list. 

On the Royal Website, you need their reservation number, name, and birthdate to link the cruise to your account (select my name is not listed). Then this code will check their packages which avoids needing their username/password. For linked reservations, the passenger may appear to be in the wrong room. This is just a feature of the code which I cannot seem to fix. The correct passengers' first names booked in each room will be shown for each booking. If the item was purchased by someone besides the account being used to check the price, the email will notify you that someone else must cancel/rebook. The code cannot tell you who actually booked it. Note, linked reservations can be confusing to cancel/rebook. If the Royal App/Website says you cannot cancel the reservation because you did not make it, you need to try all combonations. For instance, try looking at the orders on your account on "My Cruise" and also the orders on your account but on "Linked Cruise". If the other person you are linked to actually bought it, they will have to try both My Cruise and Linked Cruise.

If you have their username/password, you can add it to the list of accounts in the config.yaml and it will cycle though accounts automatically.

**Do you have a GTY Room and want to know the room you will likely get?** If a room is not officially assigned yet, the code displays GTY (meaning guarantee) for your room number. However, any excursion purchased will show the passenger's name and the room number currently associated with that excursion. Guess what? That room number is likely the room you will be officially assigned. Confirmed by the author, please post an issue if you can confirm this as well.

**Are you browsing the website for the best prices?** Always add the item to your cart and then go to the next page where you enter your credit card. Often the price will be lower in the screen where you enter your credit card then in your cart. If on the fense, do the extra step and you may be suprised!

## Related Tools

- [RoyalPriceTracker.com](https://royalpricetracker.com/) – simpler, but you must enter purchases manually, public price only which may miss many specials
- [CruiseSpotlight Price Lookup](https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/) – public price lookup for any cruise  
- `BrowseRoyalCaribbeanPrice.py` – included here for fun; lets you explore public prices with one script  

## Credits

Thanks to contributors:
- Anonymous (Retrieve AccountID programmatically)
- @cyntil8 (Celebrity support, per-day pricing, various bug fixes)  
- @tecmage (UDP, Coffee Card, Evian Water logic)  
- @iareanthony (fixed "The Key")  
- @jipis (internet pricing & passenger specials)  
- @ProxesOnBoxes (date display options, config improvements)
- @JDare (Docker support and documentation, github workflow)
- @jhedlund (Watchlist)
- @chblan (fix for iPhone script)
- @AESternberg (Formatting and updates to below Browse script)
- @RoyalCaribbeanBlog.com for featuring in an [article](https://www.royalcaribbeanblog.com/2025/04/19/cruise-price-trackers)
- Frommers.com for featuring in an [article](https://www.frommers.com/tips/cruise/how-to-save-hundreds-on-royal-caribbeans-packages-and-excursions/)
  
# Issues
1. Will not work if your password has an % in it. Change your password (replace % with ! for instance). Working on a fix. PRs welcome
1. OBC will display in USD even if cruise being checked in a different currency. Not fixable as this is how OBC is provided
1. Please double check that the price is lower before you rebook! I am not responsible if you book at a higher price!
1. Double check you are cancelling the item for the correct cruise

# Browse RoyalCaribbean Prices
This is a new script that will browse any Royal Caribbean or Celebrity sailing and show current public prices for excursions/drink packages/etc. If you book the cruise, the price could be lower than shown due to C&A or casino specials.  It will provide a link to the Royal Caribbean website which has the product prices for that cruise (be sure to be logged out of the RC website or link will not work). Code will also print all the prices. This does not require a Royal Caribbean or Celebrity account and can be used by anyone. Inspired by and similar functionality to `https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/`. 

You simply run the script. It will prompt you to select the ship and sailing from a menu.
- `python BrowseRoyalCaribbeanPrice.py` or `BrowseRoyalCaribbeanPrice.exe`. 

Defaults to USD currency. If you want a different currency, for example DKK:
- `python BrowseRoyalCaribbeanPrice.py -c DKK` or  `BrowseRoyalCaribbeanPrice.exe -c DKK`

If you are looking for a specific ship or sail date, you may also specify them on the command line as well.  Some examples are: 
- `python BrowseRoyalCaribbeanPrice.py -s Wonder` or `BrowseRoyalCaribbeanPrice.exe -s Wonder`
- `python BrowseRoyalCaribbeanPrice.py -d 05/10/27` or `BrowseRoyalCaribbeanPrice.exe -d 05/10/27`

Command-line options may be used in any combination.  They are:
- -c, --currency: currency (default: USD)
- -s, --ship: The ship to browse for; do not include 'of the Seas' after the ship name (Royal Caribbean) or 'Celebrity' before it (Celebrity)
- -d, --saildate: Date of the sailing to browse for (date format is mm/dd/yy)

There are no plans to add price checking/price history to this script. Use the `CheckRoyalCaribbeanPrice.py` script for that. If you really want to check public prices which may not be representative of the real deal you can get, just use `RoyalPriceTracker.com`.


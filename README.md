# CheckRoyalCaribbeanPrice
A script that checks if you have cheapest price for beverage package, excursions, internet, etc that you have purchased. Finds all purchased packages on your account, no need to enter them yourself. Can also check the price of a cabin if you provide the Royal Caribbean booking URL (no Royal account needed). You need to run this tool manually, inside a cron job (linux), in docker, or [task scheduler](https://www.windowscentral.com/how-create-automated-task-using-task-scheduler-windows-10) (windows). If you run Home Assistant, an addon is posted in my [addon repo](https://github.com/jdeath/homeassistant-addons) which can be called automatically.

This is not a hack. Developed only with Firefox and python. All the API calls are public and visible in the Firefox inspector. Everything in this code your browser is doing when you log into the Royal Caribbean website. Has saved me $200 on a cruise and $160 on execursions in one week since I wrote it! Hopefully it helps you too.

~~If anyone can figure out how to get the AccountID programmatically, please do a PR. I cannot figure that out.~~ Thanks to anonymous for the fix!

Latest version will tell you the remaining balance on your booked cruises. Post an issue if this is less than your Travel Agent says you owe. My cruise shows about 10% lower than the original total fare (eg. add your paid deposit back in). I wonder if this is the TA profit?

Pulls passengers' room numbers from the API. Sometimes individual excursions/packages have rooms returned by the API, where the booking still reports GTY (guarantee=room in promised class not yet assigned). If the room returned by the booking is the room you are assigned, please post an issue so this feature can be confirmed.

Thanks to help from @cyntil8, supports Celebrity Cruises too. Not fully tested yet.

Thanks to @tecmage for getting the tool to work mostly for the UDP, Coffee Card, and Evian Water purchases. Currently there is an issue if you buy 2x or more coffee cards. The API does not know how many you bought, so the tool will always say a cheaper price is available because it is comparing the price you paid for 2x cards with the price of a single card. Thanks to @iareanthony for fixing "The Key"

Thanks to @jipis for fixing internet pricing and identifying how to find specials for individual passengers. I changed the logic to check every single passenger's order to find specials only available to them (like an unlisted 40% refreshment package July 2025 sale only available to teens) which the code was not finding. Code also avoids checking orders multiple times, which can happen when reservations are linked. 

Want to monitor a friend's cruise? You can either link their cruise to your account or add their account the tracker's account list. You need their reservation number, name, and birthdate to link the cruise to your account (select my name is not listed). Then this code will check their packages which is better than having their username/password. For linked reservations, the passenger may appear to be in the wrong room. This is just a feature of the code which I cannot seem to fix. The passengers' first names booked in each room will be shown at the start of each booking. If the item was purchased by someone besides the account being used to check the price, the email will notify you that someone else must cancel/rebook. The code cannot tell you who actually booked it. If you have their username/password, you can add it to the list of accounts in the config.yaml and it will cycle though accounts automatically.

Thanks to @ProxesOnBoxes for various improvements for date display options, custom configuration files, and setting a cruise friendly name.

There is a free website that does price checks for beverage packages/excursions and does not log into your account. You have to add your packages manually and it will not find special deals exclusive to your account: `https://royalpricetracker.com/` . Consider using that for a simpler solution.  

Without an account, you can also go to: `https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/`  and look up your cruise. It will point you to all the beverage, shore, internet packages right on the royal caribbean website to see the prices. Thanks to redditer @illuminated0ne for finding that. I may add looking up cruises you have not booked at a later time.

## Install (Recommended, any Operating System, and you can edit code to your liking)
1. Install python3 (3.12 works fine) `https://www.python.org/downloads/`
1. Download the `CheckRoyalCaribbeanPrice.py` from this repo or `git clone https://github.com/jdeath/CheckRoyalCaribbeanPrice.git`
1. `pip install requests Apprise bs4`

## Install (Windows 10/11 Only)
1. Download `CheckRoyalCaribbeanPrice.exe` from release assets `https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases`
    -   Made with `pyinstaller -F --collect-all apprise --collect-all bs4 CheckRoyalCaribbeanPrice.py` (you do not need to run this command)
    -   Note: Python code in repo may be newer than .exe file, but exe is auto created upon every release

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
1. Works easiest on a Guarantee Cabin, where multiple of same cabin exist for purchase. If checking a "You Pick the Room", be sure to check the price of the same class of room you booked (Connecting Balcony, Balcony class 4D or 4A , etc). If the room you picked is no longer available, you need to get a URL of another room in that class. If there are no more rooms of the same class available to book, you will not be able to reprice. You will need to manually check back on the Royal website to see if a room opened up. It does not look like the API returns cruise prices, so we are left with scraping the website.
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
    - (There does not appear to be a way to construct a Web link to that specific order. If you find one, please let me know via an issue)
    - After cancelling/modify the order, click the product image to reorder.

## Output
Will output information on your purchases
```
CONFNUM1: You have the best price for Chacchoben Ruins Exclusive Drive of: 122.99
CONFNUM1: You have the best price for Tabyana Beach Break of: 66.99
CONFNUM2: You have the best price for Deluxe Beverage Package of: 67.99
CONFNUM2: 	Price of Deluxe Beverage Package is now higher: 72.99
CONFNUM2: You have the best price for VOOM SURF + STREAM Internet Package of: 17.99
2025-12-27 VI OUTSIDE 4N: You have best Price of 3612.12 
```
If any of the prices are lower, it will send a notification if you set up apprise. Notification will include a link to your order history and the specific date and order number to cancel

## Automating
1. Linux: Put in a cron job, if running in linux, I am sure you know how! Be sure to either provide optional argument for the `config.yaml` path or be sure to execute the script from within the directory where the configuration script is present.
1. Home Assistant: Use directions in my [repo](https://github.com/jdeath/homeassistant-addons/tree/main/royalpricecheck)
1. Windows: Use windows task schedular
1. Create a basic task. Select a daily trigger, suggest a little before you wake up
1. Action, select "Start a Program"
1. In "Program/script" Select the CheckRoyalCaribbeanPrice.exe file you download from here. Make sure the config.yaml is in same directory as .exe (if running python script, should be able to put python.exe the full path of this the script location)
1. In "Start in (optional)" enter the directory of the .exe/.yaml (you can copy the "Program/script" field, paste it, and remove the CheckRoyalCaribbeanPrice.exe)
1. After clicking finish, you can right click on task, go to triggers, and add more times to trigger the script. Suggest a time right before you get home from work. Twice a day should be sufficient
1. Ensure apprise notifications are working, because the window will close automatically after run.
   
## Notes
1. Confirm price is still lower on website, because it could have gone up since running this bot
1. You need to first cancel your beverage package, shore excursion, internet, etc
1. Wait about 10s
1. Rebook at the lower price.
1. It takes about a week for Royal to refund your credit card, but they charge you new price right away!
1. Enjoy youtube videos on cruising from: `https://www.royalcaribbeanblog.com/`, `https://www.youtube.com/royalcaribbeanblog` and `https://www.youtube.com/@LifeWellCruised`
1. If you cancel the Ultimate Dinning Package and rebook, all your reservations will be cancelled. Doing this too close to the cruise may cause you to not get your old reservation times. Reddit says to go to any open restaurant when you board to book/switch to your prefered times.
1. Maybe Matt or Ilana will feature this tool in a video !
1. Update: Mentioned on RoyalCaribbeanBlog.com: `https://www.royalcaribbeanblog.com/2025/04/19/cruise-price-trackers` 

# Issues
1. Will not work if your password has an % in it. Change your password (replace % with ! for instance). Working on a fix
1. Only checks adult prices, if only have child prices in an order it may not work. I don't have kids, so can not check.
1. It should handle orders made by other people in your party (works in my partner's account for what I booked)
1. May not handle all orders correctly.
1. Prices of internet, beverage packages, and "The Key" are per day, this code divides by the length of your cruise. If you buy a partial package, this logic may not work correctly.
1. If other prices are per day, it will not work. Let me know if other daily purchases are not calculating correctly.
1. Please double check that the price is lower before you rebook! I am not responsible if you book at a higher price!

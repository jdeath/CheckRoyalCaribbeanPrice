[Back to README](../README.md)

## Edit Config File
If a config file is not found, code will prompt if you want it to automatically download a simple config file. Fill in user/password and run. Only look below after getting basic configuration working.

Create your `config.yaml` file with the below information. Feel free to copy the file `SAMPLE-config.yaml` to `config.yaml`. Edit `config.yaml` and place it in same directory as `CheckRoyalCaribbeanPrice.py` or `CheckRoyalCaribbeanPrice.exe` or when running `CheckRoyalCaribbeanPrice.py` provide the optional argument `-c path/to/config.yaml`. The spacing/alignment is important. (eg. The `-` under accountInfo must be 3 spaces over under the 2nd c in account).  
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password
  - username: "user@gmail.com" # Your Celebrity User Name
    password: "pa$$word" # Your Celebrity Password
    cruiseLine: "celebrity" # Must indicate if celebrity
displayCruisePrices: true # Optional, this will display current price for your booked cruises. Use above discount codes.
minimumSavingAlert: 0.00 # Optional, only alert when savings are >= this amount (per-night/per-day items use total savings per item)
notifyOnError: false # Optional, send Apprise alert if the script fails (default: false)
cruises: # Optional, this allows you to watch the price of a cruise you have not booked yet
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74"
  - cruiseURL: "https://www.celebritycruises.com/checkout/guest-info?groupId=RF04FLL-1098868345&packageCode=RF4BH246&sailDate=2025-08-11&country=USA&selectedCurrencyCode=USD&shipCode=RF&cabinClassType=INTERIOR&category=I&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=Y&r0f=Y&r0g=BESTRATE&r0h=n&r0A=1127.6" # Can have as many URLS and price paid as you want. Supports Celebrity too
    paidPrice: "1127.6"
apprise_test: false # Optional
apprise:  # Optional, see https://github.com/caronc/apprise, can have as many lines as you want.
  - url: "mailto://user:password@gmail.com"
  - url: "ntfy://abcfeg3839439djd"
logFile: "output.txt"
outputWatchAsJson: true # Optional, write the watchlist add-on prices from each run to a JSON file
outputJsonFile: "output-json-watch.txt" # Optional, override the JSON output path
```

If `outputWatchAsJson` is true, the add-on watch prices checked during each run are also written as a JSON list.
Set `outputJsonFile` to change the output path; it defaults to `output-json-watch.txt`.

If you only want to check cruise addons (drink packages, excursions, etc) and do not want emails or check cruise prices, the config file is simpler. Start with this to see if works. You can have any number of Royal and/or Celebrity accounts:
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password
    cruiseLine: "royal" # or "celebrity", This is optional and defaults to royal if not present
```

To log your output to a file, add the LogFile: line and change "output.txt" to whatever you want. If you do not want logging, remove the line
```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password
    cruiseLine: "royal" # or "celebrity", This is optional and defaults to royal if not present
logFile: "output.txt"
```

To display current cabin prices for your **booked** cruise(s), set `displayCruisePrices` to true. 

```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal (note the L is capitalized)
displayCruisePrices: true
```

This will request the current price from Royal's website. The code automatically determines the number of adults and children from your booking. If you are Diamond Plus 340+ on a solo booking, it will automatically apply. It will find any publicly offered OBC and display it (but not subtract it because it is only given in USD). The script will tell you if the cabin class (Interior, Balcony, Connecting Balcony, etc) you booked is no longer for sale, which means you cannot reprice. The script will also tell you if you are beyond the final payment date (75-120 days before departure depending on length of cruise), which also means you cannot reprice. If you need any special fares or discounts, see below section to compare to the price you paid.

If price is lower and before the final payment date (even if you paid in full), do a mock booking on the website to confirm then call your travel agent.

In some cases, the API may not contain the price of your booked cruise. This is rare and may only occur for group bookings. In this case, you must provide the price you paid and any discounts. You may also want to manually set the price if there is a change fee or you lose your deposit. For instance, if it will cost you $500 to cancel/rebook, set the `pricePaid` flag to $500 less than you actually paid. Include the following info in your config, where XXXXXX and YYYYY are your reservation ID. The price can only have a `.` or `,` for the decimal place, do not use an indicator for thousands place. Enter the price paid including taxes and subtract any OBC you received. The code will identify if new booking has OBC and display it (but not subtract it since always give in USD). If you booked a special fare, you must set the corresponding keys. You only need to set what you need, will default to false. If you booked with a refundable deposit, set `refundable = true`. If you booked with included gratuities, set `gratuities=true`. If Celebrity with All-In price, set `allInUpgrade=true`. If you booked with trip insurance, set `tripInsurance=true`. All of the others keys are optional, if you do not set them they default to false or will use the information (state, loyalty number, etc) from your account. This will let the code to request the correct new price from the API. Note some GTY rooms do not have the proper information set in your account. You may need to override the category codes, the code will print an error message if this applies to you. You can find the category codes by doing a mock booking and for these values `r0e=subcategoryOverride` and `r0f=categoryOverride` in the URL bar of your browser. Examples, `XB` for GTY Ocean View Balcony, `YO` for GTY Ocean view, etc. The category and subcategory are usually the same, but can be different for Celebrity. Post an issue if you need help.

```yaml
accountInfo:
  - username: "user@gmail.com" # Your Royal Caribbean User Name
    password: "pa$$word" # Your Royal Caribbean Password 
    cruiseLine: "royal" or "celebrity" # This is optional and defaults to royal
displayCruisePrices: true
reservationPricePaid:
  - reservation:  XXXXXX # Required
    paidPrice: 4172.71 # Required
  - reservation:  YYYYY 
    paidPrice: 3172.71
    allInUpgrade: false # Optional, defaults to false
    gratuities: false # Optional, defaults to false
    tripInsurance: true # Optional, defaults to false
    refundable: false # Optional, defaults to false
    categoryOverride: "XB" # Optional, defaults to not override if this line is not present
    subcategoryOverride : "XB" # Optional, defaults to not override if this line is not present
    senior: false # Optional, defaults to false
    military: false # Optional, defaults to false
    fire: false # Optional, defaults to false
    police: false # Optional, defaults to false
    state: "CA" # Optional, defaults to state in your account
    loyaltyNumber: 12345 # Optional, defaults to loyalty number in your account
    couponCode: DP340 # Optional, defaults to none (unless your account says you are DP with 340 nights on a solo trip)
```

If you only want to check cruise prices you have **not** booked yet and do not want email notifications, the account information is not needed by the tool. Config file can look like this. Do not add letters before this paidPrice.
```yaml
cruises:
  - cruiseURL: "https://www.royalcaribbean.com/checkout/guest-info?sailDate=2025-12-27&shipCode=VI&groupId=VI12BWI-753707406&packageCode=VI12L049&selectedCurrencyCode=USD&country=USA&cabinClassType=OUTSIDE&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n&r0q=n&r0t=n&r0d=OUTSIDE&r0D=y&rgVisited=true&r0C=y&r0e=N&r0f=4N&r0g=BESTRATE&r0h=n&r0j=2138&r0w=2&r0B=BD&r0x=AF&r0y=6aa01639-c2d8-4d52-b850-e11c5ecf7146"
    paidPrice: "3833.74"
```

If you would like to assign names to cruise reservation numbers to more easily correlate which cruise is being displayed populate the following section:
```yaml
reservationFriendlyNames:
  '1234567': "Summer Cruise"
  '8912345': "Winter Cruise"
```

To override the system's default date format, set the dateDisplayFormat config value to your desired format:
```yaml
dateDisplayFormat: "%m/%d/%Y"
```

To only alert when a price drop meets a minimum savings threshold, set minimumSavingAlert. For items priced per night/per day, the threshold compares against the total savings per item across the cruise. Use case is prices change fluctuate and not worth it to you for cancel/rebook. If not set or set to 0.00, alerts trigger on any price drop as before.
```yaml
minimumSavingAlert: 2.00
```

To get an alert if the script fails to run (crash, bad config, network error, etc), set notifyOnError. This sends a short Apprise notification and exits with a non-zero code so schedulers can detect the failure. If not set or set to false, failures only show in the console/log.
```yaml
notifyOnError: true
```

To display active sitewide promotions (flash sales, percentage-off deals) for each of your sailings, set showPromos to true. This queries the Royal Caribbean promotions API and shows any current deals with their discount, valid dates, and countdown timers.
```yaml
showPromos: true
```

If you see timeout errors because the Royal Caribbean API is slow for you, set requestTimeout to raise the number of seconds the script waits for each API response before retrying/giving up. If not set, defaults to 30 seconds.
```yaml
requestTimeout: 60
```

The end-of-run summary table (see [Output](output.md) section) shows whether each cruise is paid in full. Travel agent bookings often expose no payment status via the API, so those show as "status unknown". If you have verified with your travel agent that a reservation is paid in full, list it here to show it as paid:
```yaml
reservationsPaidInFull:
  - '1234567'
  - '8912345'
```

To keep passwords out of your config file, any config value that is exactly `${VAR_NAME}` is replaced with that environment variable when the config is loaded. Useful for Docker/Home Assistant setups or shared machines.
```yaml
accountInfo:
  - username: "user@gmail.com"
    password: "${RCCL_PASSWORD}" # reads the RCCL_PASSWORD environment variable
```

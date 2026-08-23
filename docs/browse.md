[Back to README](../README.md)

# Browse RoyalCaribbean Prices
This will browse any Royal Caribbean or Celebrity sailing and display current public prices for **every** excursion/drink package/dinning package sold on a cruise. If you book the cruise, the price could be lower than shown due to C&A or casino specials.  It will provide a link to the Royal Caribbean or Celebrity website which has the product prices for that cruise (be sure to be logged out of the website or link will not work). It will  print any scheduled activities for the cruise, such as trivia and gameshows and theme nights. It will also print the current price of cheapest room in each category (inside, oceanview, balcony, suite). It will print MDR menus. This program does **not** require a configuration file nor a Royal Caribbean/Celebrity account. Inspired by and similar functionality to `https://cruisespotlight.com/royal-caribbean-cruise-planner-price-lookup/` website. 

Windows download [BrowseRoyalCaribbeanPrice.exe](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/BrowseRoyalCaribbeanPrice.exe) ,
MacOS Intel download [BrowseRoyalCaribbeanPrice_MacOS_intel](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/BrowseRoyalCaribbeanPrice_MacOS_intel)  ,
MacOS arm64/Silicon download [BrowseRoyalCaribbeanPrice_MacOS_arm64](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/latest/download/BrowseRoyalCaribbeanPrice_MacOS_arm64)  

Vibe Coded (Claude) Android (or Fire Tablet) App [Android APK](https://github.com/jdeath/CheckRoyalCaribbeanPrice/releases/download/3.3.1/BrowseRoyalCaribbeanPrice.apk) . This is nice because can search and change sorting on the fly. Vibe coding just to convert from hand-written python.

You simply run the script. It will prompt you to select the ship and sailing from a menu.
- `python BrowseRoyalCaribbeanPrice.py` or `BrowseRoyalCaribbeanPrice.exe` or `BrowseRoyalCaribbeanPrice_MacOS`
-  MacOS users will need to disable the Malware warning as explained in [above documentation](install-macos.md)
-  iOS / Android users can also run the script as is. Download python script and basically follow above [iOS/Android](install-ios.md) directions.
-  Android or Amazon Fire Tablet users can also run a vibe-coded native android app: see above
  
Defaults to system defined currency. If you want a different currency, for example DKK:
- `python BrowseRoyalCaribbeanPrice.py -c DKK` or  `BrowseRoyalCaribbeanPrice.exe -c DKK`

If you are looking for a specific ship or sail date, you may also specify them on the command line as well.  Some examples are: 
- `python BrowseRoyalCaribbeanPrice.py -s Wonder` or `BrowseRoyalCaribbeanPrice.exe -s Wonder`
- `python BrowseRoyalCaribbeanPrice.py -d 05/10/2027` or `BrowseRoyalCaribbeanPrice.exe -d 05/10/2027`
  - Please note that you may need to adjust the date format for your particular locale setting (for example, '5/10/2027' insead of '05/10/2027')

You may sort the resulting list per category alphabetically, by price, or using the default order from Royal Caribbean's servers.  Some examples are:
- `python BrowseRoyalCaribbeanPrice.py -o alpha` or `python BrowseRoyalCaribbeanPrice.py -o price`

Command-line options may be used in any combination.  They are:
- -c, --currency: currency (default: System currency) (e.g USD, GBP, DKK or others)
- -s, --ship: The ship to browse for; do not include 'of the Seas' after the ship name (Royal Caribbean) or 'Celebrity' before it (Celebrity)
- -d, --saildate: Date of the sailing to browse for (date format is mm/dd/yy)
- -k, --sortkey: Sort each category alphabetically, by price (lowest to highest), or the default order from the server (default)
- -o, --sortorder: Sort each category in ascending or descending order, based on the sortkey value
- -w, --watchlistcodes: Display the codes for each product to put in `CheckRoyalCaribbeanPrice.py` product watchlist function (default no display)
- -l, --logfile: Output also saves to file (eg. output.txt)
   
Note: Due to API limiations, `BrowseRoyalCaribbeanPrice.py` only shows price for the default variant (eg. 1 Wifi device not 2, 12 evian bottles not 24), These items are for sale, but the API does not return price. The `CheckRoyalCaribbeanPrice.py` script will find the price for these.
There are no plans to add price checking/price history to this script. Use the `CheckRoyalCaribbeanPrice.py` script for that. If you really want to check public prices which may not be representative of the real deal you can get, just use `RoyalPriceTracker.com`.

Cruise activity schedule, such as trivia and game shows, often only populated a few days before the cruise. Look at sailing 0) or 1) in the list to get an idea of current activities on the ship. This is much faster than changing your cruise in the App! Will print MDR menus if available. For Royal, look for the "Dress Code" activity and for Celebrity look for "Tonight's Attire" activity to see the theme nights.

You can run this on the iPhone, following the [iPhone install directions](install-ios.md) and download the [BrowseRoyalCaribbeanPrice.py](https://raw.githubusercontent.com/jdeath/CheckRoyalCaribbeanPrice/refs/heads/main/BrowseRoyalCaribbeanPrice.py) to your phone, no need to edit as the Browse script does not need username/password.

There is also an EXPIRIMENTAL vibe-coded browser based version of the BrowseRoyalCaribbeanPrice available at `https://jdeath.github.io/` . You must disable CORS in your broweser for it to work.

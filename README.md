# CheckRoyalCaribbeanPrice
A bot that checks if you have cheapest price for beverage package, excursions, etc. Does not price out the actual cruise! You need to run manually or inside a cron job. If you run home assistant, an addon is posted in my home assistant addon repo.

This is not a hack. Developed only with Firefox and python. All the API calls are public and visible in the Firefox inspector. Everything in this code your browser is doing when log into Royal Caribbean website.

If anyone can figure out how to get the AccountID programatically, please do a PR. I cannot figure that out.

# Install
1. Install python3 (3.12 works fine) `https://www.python.org/downloads/`
1. Download the raw files from this repo or `git clone https://github.com/jdeath/CheckRoyalCaribbeanPrice.git`
1. `cd CheckRoyalCaribbeanPrice`
1. `pip install requests Apprise`

Edit `config.yaml`
```
username: "user@gmail.com" # Your Royal Caribbean User Name
password: "pa$$word" # Your Royal Caribbean Password
accountId: "abcdefgh-abcd-1234-1234-abcdefghijk"  # Your Royal Caribbean Account ID (see below)
apprise:  # Optional, see https://github.com/caronc/apprise, can have as many lines as you want.
  - url: "mailto://user:password@gmail.com"
  - url: "whatsapp://AccessToken@FromPhoneID/ToPhoneNo"
```

## Get Royal Caribbean Account ID
1. Use Firefox to load Royal Caribbean Website
1. Right Click in window, select `Inspect (Q)`
1. Click SIGN IN on the Royal website (top right)
1. Enter you username and password and click login
1. Click the network button (has an up/down arrow) in the inspector section of firefox
1. In the "filter urls" box type `profileBookings/enriched`
1. Select the line that comes up that has information in the "File" column
1. When you click, you see something like: `https://aws-prd.api.rccl.com/v1/profileBookings/enriched/XXXX-XXX-XXXX-XXX-XXXXXXX?brand=R&includeCheckin=true`
1. Copy the `XXXX-XXX-XXXX-XXX-XXXXXXX`, that is your account id.
1. Paste into config.yaml `accountId:` field
1. This key should last about a month. You will need to get it again in a month and update config.yaml
1. If anyone knows how to get this value via python, please let me know.

## Run
1. `python CheckRoyalCaribbeanPrice.py`
1. It will indicate if you should rebook or if you have the best price
1. It will also tell you if the price has gone up since you purchases (do not rebook in that case!)
1. If you setup apprise, it will notify you via your prefered method(s) if you should rebook

# Notes
1. Confirm price is still lower on website, because it could have gone up since running this bot
1. You need to first cancel your beverage package, shore excursion, internet, etc
1. Wait about 10s
1. Rebook at the lower price.
1. It takes about a week for Royal to refund your credit card, but they charge you new price right away!
1. Enjoy youtube videos on cruising from: `https://www.royalcaribbeanblog.com/`, `https://www.youtube.com/royalcaribbeanblog` and `https://www.youtube.com/@LifeWellCruised` 

# Updates
1. Probably not going to update much, unless I find an issue
1. Only checks adult prices, if only have child prices in an order it may will not work.
1. It should handle orders made by other people in your party
1. May not handle all orders correcty.
1. Prices of internet and beverage are per day, this code needs to divide by the length of your cruise. If you buy a partial package, it may not work correctly.
1. If other prices are per day, it will not work. Let me know what other things are not calculating correctly

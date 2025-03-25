# CheckRoyalCaribbeanPrice
A bot that checks if you have cheapest price for beverage package, excursions, etc. Does not price out the actual cruise! You need to run manually or inside a cron job. If you run home assistant, an addon will be posted shortly

This is not a hack. Developed only with Firefox and knowedlge of python. All the API calls are visible in the Firefox inspector.

# Install
Download the files
`pip install requests Apprise`

Edit `config.yaml`
```
username: "user@gmail.com" # Your Royal Caribbean User Name
password: "pa$$word" # Your Royal Caribbean Password
accountId: "abcdefgh-abcd-1234-1234-abcdefghijk"  # Your Royal Caribbean Account ID (see below)
apprise:  # Optional, see https://github.com/caronc/apprise
  - url: "mailto://user@gmail.com&pass=password"
```

## Get Royal Caribbean Account ID
1. Use Firefox to load Royal Caribbean Website
1. Right Click in window, select `Inspect (Q)`
1. Enter you username and password
1. Click the network button
1. In the "filter urls" box type `profile`
1. Select the line that comes up
1. When you click, you see something like: `https://aws-prd.api.rccl.com/v1/profileBookings/enriched/XXXX-XXX-XXXX-XXX-XXXXXXX?brand=R&includeCheckin=true`
1. Copy the `XXXX-XXX-XXXX-XXX-XXXXXXX`, that is your account id.

## Run
1. `python CheckRoyalCaribbeanPrice.py`
1. It will indicate if you should rebook or if you have the best price
1. If you setup apprise, it will notify you via your prefered method

# Updates
1. Probably not going to update much, unless I find an issue
1. Only checks adult prices
1. May not handle all orders correcty.

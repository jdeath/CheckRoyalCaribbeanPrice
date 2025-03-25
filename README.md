# CheckRoyalCaribbeanPrice
A bot that checks if you have cheapest price for beverage package, excursions, etc

# Install
Download the files
`pip install requests yaml Apprise`

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

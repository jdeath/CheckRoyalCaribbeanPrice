[Back to README](../README.md)

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
1. Inspect the URL to find the `prefix` and `product`, for example for the Premium WIFI 2 Device Package the URL looks like:
   `https://www.celebritycruises.com/account/cruise-planner/category/pt_internet/product/33F1?bookingId=&shipCode=&sailDate=`
1. The `prefix` is the path following /category/ (`pt_internet` in this case)
1. The `product` is the value following /product/ (`33F1` in this case)
1. Use the advertised price in the cruise panner. Eg. Do not include gratituty. Use per day price for Beverage Package, UDP, Internet, Key.
1. You can also run the `BrowseRoyalCaribbeanPrice.py` with the `-w` flag to print the watchlist codes for every item in a cruise.
1. Note: product numbers can be different on different cruises: Eg. Royal Deluxe Beverage package can be 3222 or 3224

### Example Output
```
[WATCH] Deluxe Beverage Package - John (1234): Book! Deluxe Beverage Package Price is lower: 75.00 than 85.00
[WATCH] Internet Package - Mary (1234): price is higher than watch price: 25.00 (now 30.00)
```

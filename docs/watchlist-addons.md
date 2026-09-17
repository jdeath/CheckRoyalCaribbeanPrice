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
    price: 85.00           # Alert if current price drops below this amount. Use per night w/o gratuity price
    enabled: true          # Set to false to temporarily disable this item
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
1. Use the advertised price in the cruise planner. Eg. Do not include gratuity. Use per day price for Beverage Package, UDP, Internet, Key.
1. You can also run the `BrowseRoyalCaribbeanPrice.py` with the `-w` flag to print the watchlist codes for every item in a cruise.
1. Note: product numbers can be different on different cruises: Eg. Royal Deluxe Beverage package can be 3222 or 3224

### Example Output
```
[WATCH] Deluxe Beverage Package - John (1234): Book! Deluxe Beverage Package Price is lower: 75.00 than 85.00
[WATCH] Internet Package - Mary (1234): price is higher than watch price: 25.00 (now 30.00)
```

### Ignore selected price notifications

Use the top-level `ignoredPriceAlerts` list to mute a product for a specific cruise
reservation. This also applies to automatically discovered booked add-ons, which
are checked even when they do not appear in `watchList`.
Rules apply to booked add-ons and manual product watches evaluated for that
reservation; they do not mute watches without a reservation number.

```yaml
ignoredPriceAlerts:
  - reservation: "1000001" # Fictional reservation; replace with yours
    prefix: "pt_spa"      # Use the category from the product's Cruise Planner URL
    product: "PRODUCT_CODE"
    # guest: "GUEST_ID"   # Optional passenger ID, not a name
```

Each rule requires an exact reservation number, category `prefix` and `product`
ID. Find the category and product in the Cruise Planner product URL as described
above. The optional `guest` is Royal's passenger ID; it is also recorded as
`guest_id` in the optional `historyDb` price history. Without `guest`, the rule
applies to all passengers in that reservation. Quoted numbers are recommended;
integer IDs are accepted too. Unknown keys and empty identifiers are rejected.

The tool still checks and displays the price in the console, marked
`Notification suppressed by ignoredPriceAlerts`. Optional watch JSON and price
history still receive the result. Price history records
`rebook_decision: suppressed_by_configuration` and `notified: false` for muted
price drops. Other products, reservations and cabin alerts are unaffected,
including other products on the same order.

For example, this can mute a spa product whose advertised sale only applies to
appointment times you do not want. It does not compare prices for your booked
time. All appointments for that product within the rule's reservation/guest scope
are muted. Remove the rule to resume its notifications on the next regular check.
An omitted, empty or null `ignoredPriceAlerts` section disables exclusions.
Other section types (such as `false`, `0` or a mapping) remain configuration errors.

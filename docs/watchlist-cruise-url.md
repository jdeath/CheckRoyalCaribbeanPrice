[Back to README](../README.md)

## Get Cruise URL for Watchlist Functionality (Optional - This is only for a cruise you have not booked!)
1. If you want to check the cabin price of a cruise you have booked, see [Edit Config File](config.md). This section is just for cruises you have *not* booked yet.
1. Be sure you are logged out of the Royal Caribbean / Celebrity Website. If you are logged in, the URL you get in Step 5 will not work.
1. Go to Royal Caribbean or Celebrity and do a mock booking of the room you want, with the same number of adults and kids
1. Select a cruise and select your room type/room. Be sure to enter your C&A number and any senior/military/police discounts in the "Apply Promo Code and Exclusive Rates" link to the left. **Use your C&A or Celebrity # depending on the cruise. Do not use username. Bug in Royal Website requires your number not username**
1. If you want a refundable deposit, trip insurance, included gratituies, or Celebrity only All-In package be sure to select them on the "preferences screen"
1. If you have a coupon code (e.g dp340 for Diamond Plus 340+ on a solo booking), be sure to enter it.
1. Complete until they ask for your personal/guest information
1. At this point, you should see a blue bar at the bottom right of webpage with a price
1. Copy the entire URL from the top of your browser into the cruiseURL field. The url should start with `https://www.royalcaribbean.com/checkout/guest-info?...` or `https://www.celebritycruises.com/checkout/guest-info?...` where `...` is a bunch of stuff. Copy the entire URL
1. Put the price you paid in the paidPrice field. Remove the `$` and any `,` . Subtract any OBC you recieved from Royal or your TA. Do not add letters in this price, the code can tell from the URL if you included gratities, All-In package, etc.
1. Run the tool and see if it works
1. You can add multiple cruiseURL/paidPrice to track multiple cruises or rooms on a cruise
1. If the code says the price is cheaper, do a mock booking to see if cabin is still available. You need to do this from a new search on the Royal Caribbean / Celebrity website. Do not just put the cruiseURL in your browser.
1. If it is lower than you paid for and before final payment date call your Travel Agent or Royal Caribbean (if you booked direct) and they should (reports of pushback lately) reduce the price. Be careful, you will lose the onboard credit you got in your first booking, if the new booking does not still offer it! The code will print the OBC offered for the new cruise, but will not subtract it because OBC only given in USD
1. Update the pricePaid field to the new price. Remove the `$` ,`£` and any `,` (or `.` if non-USD currency for thousands designator)
1. If there are no more rooms of the same class available to book, you will not be able to reprice. You will need to wait until a room opens up. The code will print the cheapest interior, outside view, balcony or suite available. These are probably GTY for each class and not the exact type of room you wanted. This is all the public cruise price API returns.
1. If you only want to check the cruise prices with URL you provide, you do not need to have your `accountInfo` and/or `apprise` in your [config file](config.md), as they are not necessary.
1. Should always give price in your current currency (except for OBC which is only in USD). If your currency is not supported, create an issue
   
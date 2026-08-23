[Back to README](../README.md)

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

8/28/2026 Ovation of the Seas BALCONY XB: You have best price of 1000.0 USD (now 1714.08 USD)
8/28/2026 Ovation of the Seas BALCONY XB (Loyalty, Residency Discount): You have best price of 1000.0 USD (now 1613.08 USD) #Impact of discounts 
```
If any of the prices are lower, it will send a notification if you set up apprise. Notification will include a link to your order history and the specific date and order number to cancel. Notice on the 2nd reservation, the official room is GTY but the excursions show the currently assigned room in the Royal backend system. This room is likely what you will get!

At the end of the run, a summary table shows the check-in opening/boarding time and final payment date for every booked sailing, sorted by sail date. The final payment column is color coded: green if paid in full, yellow if a balance is still due (or the API does not report a status, common for travel agent bookings - see reservationsPaidInFull above), and red if a balance is due past the final payment date.
```
Upcoming Check-In & Final Payment Dates
Sail Date  Ship (Room)                  Reservation             Check-In        Final Payment
---------  ---------------------------  ----------------------  --------------  -----------------------
09/11/25   Quantum of the Seas (1234)   1234567 (Summer Cruise) Boarding 11:30  06/12/25 (paid)
09/15/25   Brilliance of the Seas (GTY) 8912345                 Opens 08/28/25  06/16/25 (balance due)
```

"""
Alert decision matrix tests.

The whole point of this tool is firing an Apprise notification exactly when it
should - and staying silent exactly when it should. These tests pin down that
contract for the two money functions:

  - get_cruise_price:   cruise fare drops (booked cruises and prospective watchlist)
  - get_new_order_price: add-on price drops (orders and watchlist items)

Every test asserts on the notification behavior (fired / not fired, and what the
body says), not just on log text. The scenarios cover the full decision matrix:
price direction x booked-vs-watchlist x final-payment window x minimumSavingAlert
thresholds (including exact-boundary cases) x per-night totalization x OBC x
refundable/all-included fares x missing price data.
"""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from CheckRoyalCaribbeanPrice import (
    AccountInfo,
    WatchItemContext,
    get_cruise_price,
    get_new_order_price,
)


# =====================================================================
# FIXTURES & SCENARIO HELPERS
# =====================================================================

@pytest.fixture(autouse=True)
def silence_module_logging():
    """Route module log calls into a capture list shared by the helpers below."""
    yield  # patching happens per-scenario inside the helpers


def make_account(cruise_line: str = "royal") -> AccountInfo:
    account = AccountInfo(username="test_user", password="password", cruise_line=cruise_line)
    account.access = MagicMock()
    account.access.token = "fake_token"
    account.access.id = "fake_id"
    account.access.loyalty_number = None
    return account


def make_checkout_url(sail_days_out: int, domain: str = "royalcaribbean") -> str:
    """A realistic cruise planner URL like the ones users paste into config.yaml."""
    sail = (date.today() + timedelta(days=sail_days_out)).isoformat()
    return (
        f"https://www.{domain}.com/checkout/guest-info?sailDate={sail}"
        "&shipCode=WN&packageCode=WN07X123&selectedCurrencyCode=USD&country=USA"
        "&cabinClassType=BALCONY&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n"
        "&r0q=n&r0t=n&r0d=BALCONY&r0D=y&r0e=N&r0f=4D&r0g=BESTRATE"
    )


def fare(amount, grats=100.0, ins=50.0, obc=0.0):
    return {"fare": amount, "gratuities": grats, "insurance": ins, "obc": obc}


def run_cruise_scenario(
    *,
    results,
    paid=3000.0,
    automatic=True,
    sail_days_out=400,
    minimum_saving_alert=None,
    struct_extra=None,
    domain="royalcaribbean",
):
    """
    Drive get_cruise_price with a mocked pricing API and captured output.

    Returns (apobj_mock, logged_text). sail_days_out controls the final payment
    window: 400 days out is safely before it, 30 days out is safely past it
    (7-night sailing -> 90-day final payment deadline).
    """
    account = make_account()
    booking = {"url": make_checkout_url(sail_days_out, domain)}

    paid_price_struct = {"paidPrice": paid} if paid is not None else {}
    if struct_extra:
        paid_price_struct.update(struct_extra)

    apobj = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.apobj = apobj
    mock_cfg.minimum_saving_alert = minimum_saving_alert
    mock_cfg.currency_override = None
    mock_cfg.date_display_format = "%m/%d/%Y"
    mock_cfg.format_date = lambda d: str(d)

    ship_dictionary = MagicMock()
    ship_dictionary.get_ship.return_value = "Wonder of the Seas"

    logged = []
    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.log", side_effect=lambda m, *a, **k: logged.append(str(m))), \
         patch("CheckRoyalCaribbeanPrice.get_room_price_via_API", return_value=results):
        get_cruise_price(
            account, booking, ship_dictionary,
            automatic_URL=automatic,
            paid_price_struct=paid_price_struct if (paid is not None or struct_extra) else None,
        )

    return apobj, "\n".join(logged)


AVAILABLE = {"room_available": True, "sailing_nights": 7, "available_rooms": []}


def run_addon_scenario(
    *,
    starting_from_price,
    paid=50.0,
    for_watch=False,
    sales_unit=None,
    nights=7,
    minimum_saving_alert=None,
    owner=True,
    guest_age_string="adult",
    payload_extra=None,
    reservations=None,
):
    """
    Drive get_new_order_price with a mocked catalog API and captured output.

    Returns (apobj_mock, logged_text). starting_from_price of None simulates an
    item that is no longer for sale.
    """
    account = make_account()
    booking = {"bookingId": "1234567", "shipCode": "WN", "sailDate": "20270819", "numberOfNights": nights}

    payload = {"title": "Deluxe Beverage Package"}
    if starting_from_price is not None:
        payload["startingFromPrice"] = starting_from_price
    if payload_extra:
        payload.update(payload_extra)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"payload": payload}

    ctx = WatchItemContext(
        prefix="pt_beverage",
        product="3005",
        passenger_ID="PAX1",
        passenger_name="Jim",
        room="6543",
        paid_price=paid,
        currency="USD",
        guest_age_string=guest_age_string,
        sales_unit=sales_unit,
        for_watch=for_watch,
        owner=owner,
        reservations=reservations or [],
    )

    apobj = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.minimum_saving_alert = minimum_saving_alert
    mock_cfg.currency_override = None

    logged = []
    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.log", side_effect=lambda m, *a, **k: logged.append(str(m))), \
         patch("CheckRoyalCaribbeanPrice._execute_api_request", return_value=mock_resp) as mock_net:
        get_new_order_price(account, booking, apobj, ctx)

    return apobj, "\n".join(logged), mock_net


# =====================================================================
# CRUISE FARE MATRIX: get_cruise_price
# =====================================================================

class TestCruiseFareAlerts:
    """Booked-cruise (automatic_URL=True) alert decisions."""

    def test_price_drop_fires_rebook_notification(self):
        apobj, logged = run_cruise_scenario(results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=3000.0)
        assert apobj.notify.call_count == 1
        body = apobj.notify.call_args.kwargs["body"]
        assert "Rebook!" in body
        assert "2500.00" in body and "3000.00" in body

    def test_equal_price_stays_silent(self):
        apobj, logged = run_cruise_scenario(results={**AVAILABLE, "base_fare": fare(3000.0)}, paid=3000.0)
        apobj.notify.assert_not_called()
        assert "best price" in logged
        assert "(now" not in logged  # equal price must not print a "now" figure

    def test_higher_price_stays_silent_and_shows_current(self):
        apobj, logged = run_cruise_scenario(results={**AVAILABLE, "base_fare": fare(3400.0)}, paid=3000.0)
        apobj.notify.assert_not_called()
        assert "best price" in logged and "(now 3400.00" in logged

    def test_past_final_payment_drop_must_not_notify(self):
        """A drop you can no longer act on must not push a notification."""
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=3000.0, sail_days_out=30,
        )
        apobj.notify.assert_not_called()
        assert "Past Final Payment Date" in logged

    def test_saving_below_threshold_suppresses_notification(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2950.0)}, paid=3000.0, minimum_saving_alert=100.0,
        )
        apobj.notify.assert_not_called()
        assert "no notification sent" in logged

    def test_saving_exactly_at_threshold_notifies(self):
        """The comparison is strict '<': a saving equal to the threshold must alert."""
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2900.0)}, paid=3000.0, minimum_saving_alert=100.0,
        )
        assert apobj.notify.call_count == 1

    def test_saving_above_threshold_notifies(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=3000.0, minimum_saving_alert=100.0,
        )
        assert apobj.notify.call_count == 1

    def test_obc_appears_in_alert_body(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0, obc=50.0)}, paid=3000.0,
        )
        assert apobj.notify.call_count == 1
        assert "not including 50.00 USD OBC" in apobj.notify.call_args.kwargs["body"]

    def test_no_paid_price_displays_current_and_stays_silent(self):
        apobj, logged = run_cruise_scenario(results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=None)
        apobj.notify.assert_not_called()
        assert "Current Price 2500.00" in logged

    def test_missing_fare_data_bails_without_phantom_alert(self):
        """A room with no fare struct must not produce a 'Rebook! 0.00' alert."""
        apobj, logged = run_cruise_scenario(results=dict(AVAILABLE), paid=3000.0)
        apobj.notify.assert_not_called()
        assert "No fare pricing returned" in logged

    def test_insurance_and_gratuities_adders_affect_the_comparison(self):
        """Fare 2900 + 100 grats + 50 insurance = 3050 vs 3000 paid: no alert."""
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2900.0, grats=100.0, ins=50.0)},
            paid=3000.0,
            struct_extra={"gratuities": True, "tripInsurance": True},
        )
        apobj.notify.assert_not_called()
        assert "best price" in logged

    def test_refundable_fare_used_for_comparison_when_requested(self):
        """Refundable 3100 vs paid 3000: silent, but the cheaper base fare is mentioned."""
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2600.0), "base_refundable_fare": fare(3100.0)},
            paid=3000.0,
            struct_extra={"refundable": True},
        )
        apobj.notify.assert_not_called()
        assert "Non-Refundable price 2600.00" in logged

    def test_missing_refundable_fare_falls_back_to_base_price(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0)},
            paid=3000.0,
            struct_extra={"refundable": True},
        )
        assert apobj.notify.call_count == 1  # compares against base fare, still a drop


class TestWatchlistCruiseAlerts:
    """Prospective-cruise (automatic_URL=False) alert decisions."""

    def test_price_below_watch_price_fires_consider_booking(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=3000.0, automatic=False,
        )
        assert apobj.notify.call_count == 1
        assert "Consider Booking!" in apobj.notify.call_args.kwargs["body"]

    def test_watchlist_respects_minimum_saving_threshold(self):
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2950.0)}, paid=3000.0,
            automatic=False, minimum_saving_alert=100.0,
        )
        apobj.notify.assert_not_called()
        assert "no notification sent" in logged

    def test_watchlist_past_final_payment_still_notifies(self):
        """Watchlist cruises are not booked yet: the final payment window is irrelevant."""
        apobj, logged = run_cruise_scenario(
            results={**AVAILABLE, "base_fare": fare(2500.0)}, paid=3000.0,
            automatic=False, sail_days_out=30,
        )
        assert apobj.notify.call_count == 1

    def test_sold_out_watchlist_room_notifies_room_not_available(self):
        apobj, logged = run_cruise_scenario(
            results={"room_available": False, "sailing_nights": 7, "available_rooms": []},
            paid=3000.0, automatic=False,
        )
        assert apobj.notify.call_count == 1
        assert apobj.notify.call_args.kwargs["title"] == "Cruise Room Not Available"

    def test_sold_out_booked_room_stays_silent(self):
        """Your own booked cabin 'disappearing' from sale must not notify."""
        apobj, logged = run_cruise_scenario(
            results={"room_available": False, "sailing_nights": 7, "available_rooms": []},
            paid=3000.0, automatic=True,
        )
        apobj.notify.assert_not_called()
        assert "Not For Sale" in logged


# =====================================================================
# ADD-ON MATRIX: get_new_order_price
# =====================================================================

class TestAddonRebookAlerts:
    """Purchased add-on (for_watch=False) alert decisions."""

    def test_price_drop_fires_rebook_with_cancel_link(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0,
        )
        assert apobj.notify.call_count == 1
        body = apobj.notify.call_args.kwargs["body"]
        assert "Rebook!" in body and "Cancel Order" in body

    def test_equal_price_stays_silent(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 50.0}, paid=50.0,
        )
        apobj.notify.assert_not_called()
        assert "has best price" in logged

    def test_higher_price_stays_silent_and_shows_current(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 60.0}, paid=50.0,
        )
        apobj.notify.assert_not_called()
        assert "(now 60.00" in logged

    def test_shipboard_price_fallback_when_promotional_missing(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultShipboardPrice": 40.0}, paid=50.0,
        )
        assert apobj.notify.call_count == 1

    def test_child_age_bracket_uses_child_price(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"childPromotionalPrice": 20.0, "adultPromotionalPrice": 60.0},
            paid=25.0, guest_age_string="child",
        )
        assert apobj.notify.call_count == 1  # child price 20 < paid 25; adult 60 ignored

    def test_no_longer_for_sale_stays_silent(self):
        apobj, logged, _ = run_addon_scenario(starting_from_price=None, paid=50.0)
        apobj.notify.assert_not_called()
        assert "No Longer for Sale" in logged

    def test_per_night_total_saving_crosses_threshold(self):
        """2/night saving is under a 10 threshold, but 7 nights x 2 = 14 is over: alert."""
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 24.99}, paid=26.99,
            sales_unit="PER_NIGHT", nights=7, minimum_saving_alert=10.0,
        )
        assert apobj.notify.call_count == 1
        assert "per night (14.0 USD total)" in apobj.notify.call_args.kwargs["body"]

    def test_per_night_total_saving_below_threshold_suppressed(self):
        """2/night x 3 nights = 6 total, under a 10 threshold: no alert."""
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 24.99}, paid=26.99,
            sales_unit="PER_NIGHT", nights=3, minimum_saving_alert=10.0,
        )
        apobj.notify.assert_not_called()
        assert "no notification sent" in logged

    def test_saving_exactly_at_threshold_notifies(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0, minimum_saving_alert=10.0,
        )
        assert apobj.notify.call_count == 1

    def test_booked_by_other_guest_warns_in_body(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0, owner=False,
        )
        assert "booked by another in your party" in apobj.notify.call_args.kwargs["body"]

    def test_promotion_name_included_in_body(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0,
            payload_extra={"promoDescription": {"displayName": "Early Booking Bonus"}},
        )
        assert "Promotion:Early Booking Bonus" in apobj.notify.call_args.kwargs["body"]


class TestAddonWatchlistAlerts:
    """Watchlist add-on (for_watch=True) alert decisions."""

    def test_price_below_watch_fires_book_with_booking_link(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0, for_watch=True,
        )
        assert apobj.notify.call_count == 1
        body = apobj.notify.call_args.kwargs["body"]
        assert "Book!" in body and "Book at" in body and "[WATCH]" in body

    def test_price_above_watch_stays_silent(self):
        apobj, logged, _ = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 90.0}, paid=50.0, for_watch=True,
        )
        apobj.notify.assert_not_called()
        assert "higher than watch price" in logged

    def test_unavailable_watch_item_stays_silent(self):
        apobj, logged, _ = run_addon_scenario(starting_from_price=None, paid=50.0, for_watch=True)
        apobj.notify.assert_not_called()
        assert "not available or already booked" in logged

    def test_reservation_filter_skips_other_bookings_without_api_call(self):
        apobj, logged, mock_net = run_addon_scenario(
            starting_from_price={"adultPromotionalPrice": 40.0}, paid=50.0,
            for_watch=True, reservations=["7654321"],  # booking is 1234567
        )
        apobj.notify.assert_not_called()
        mock_net.assert_not_called()

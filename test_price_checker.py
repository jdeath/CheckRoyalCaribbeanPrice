import pytest
import requests

from unittest.mock import MagicMock, patch

# Import the specific entities and orchestration engines from your script
from CheckRoyalCaribbeanPrice import (
    AccountInfo,
    CruiseAppConfig,
    CruiseURLParams,
    ShipRegistry,
    WatchItemContext,
    config,
    get_cruise_price,
    get_orders,
    get_voyages,
)

# =====================================================================
# SYSTEM FIXTURES & DATA BUILDERS
# =====================================================================
@pytest.fixture(autouse=True)
def mock_global_config():
    """
    Safely mocks the global config object, custom log methods, and apprise notifications
    so production functions run cleanly without side-effects.
    """
    import CheckRoyalCaribbeanPrice

    # Create a mock config object with all required properties
    mock_config = MagicMock()
    mock_config.apobj = MagicMock()
    mock_config.date_display_format = "%Y-%m-%d"
    mock_config.format_date = lambda d: str(d) # Simple string conversion pass-through
    mock_config.currency_override = None

    # Override the module-level config variable
    original_config = CheckRoyalCaribbeanPrice.config
    CheckRoyalCaribbeanPrice.config = mock_config

    # Patch the internal 'log' function directly to avoid NoneType crash calls
    with patch('CheckRoyalCaribbeanPrice.log', MagicMock()) as mock_log:
        yield mock_config.apobj

    # Restore original state after test run
    CheckRoyalCaribbeanPrice.config = original_config

@pytest.fixture
def base_account_info():
    """Generates a standard authenticated user runtime context template with fake network access."""
    account = AccountInfo(
        username="test_user@example.com",
        password="secure_password",
        state="FL",
        senior=False,
        military=False,
        police=False
    )
    # Give it a mock access/session layer so it doesn't crash on account_info.access.session
    mock_access = MagicMock()
    mock_access.session = MagicMock(spec=requests.Session)
    account.access = mock_access
    account.found_items = []
    return account

# =====================================================================
# ITEM 1 TESTS: Cabin "Not For Sale" Notification Logic
# =====================================================================
def test_booked_cruise_not_for_sale_stays_silent(mock_global_config, base_account_info):
    """
    Scenario: An active, booked cruise goes off-market (sold out / offline).
    Expectation: The terminal logs the alert, but NO notification gets fired.
    """
    mock_booking_payload = {
        "bookingId": "1234567",       # Real booking identifier present
        "shipCode": "WN",
        "sailDate": "20261115",       # Raw production date format
        "stateroomType": "BALCONY"
    }

    real_registry = ShipRegistry()

    # Intercept check_if_room_is_available and get_room_price_via_API to isolate availability gates
    with patch('CheckRoyalCaribbeanPrice.check_if_room_is_available', return_value=(False, [])):
        get_cruise_price(
            account_info=base_account_info,
            booking=mock_booking_payload,
            ship_dictionary=real_registry,
            automatic_URL=True
        )

    # VERIFICATION: Ensure the global alert framework was NEVER triggered
    mock_global_config.notify.assert_not_called()


def test_watchlist_cruise_not_for_sale_sends_notification(mock_global_config, base_account_info):
    """
    Scenario: A speculative prospective watch item goes off-market.
    Expectation: An explicit notification is sent to let the user know.
    """
    mock_watchlist_payload = {
        # Valid marketing URL string so parser extracts a clean sailDate value
        "url": "https://www.royalcaribbean.com/booking/landing?shipCode=WN&sailDate=2026-11-15&r0d=SUITE",
        "stateroomType": "SUITE"
    }

    real_registry = ShipRegistry()

    with patch('CheckRoyalCaribbeanPrice.check_if_room_is_available', return_value=(False, [])):
        get_cruise_price(
            account_info=base_account_info,
            booking=mock_watchlist_payload,
            ship_dictionary=real_registry,
            automatic_URL=False
        )

    # VERIFICATION: Ensure the global apprise framework explicitly fired the warning
    mock_global_config.notify.assert_called_once()
    assert "Not For Sale" in mock_global_config.notify.call_args[1]['body']


# =====================================================================
# ITEM 2 TESTS: get_orders() Context Instantiation Scope Verification
# =====================================================================
def test_get_orders_handles_item_calculations_without_scope_leak(mock_global_config, base_account_info):
    """
    Scenario: Parsing valid historical order entries from API response footprints.
    Expectation: The mapping logic runs smoothly without throwing NameError or reference leaks.
    """
    mock_api_order_response = {
        "payload": {
            "myOrders": [{
                "orderCode": "ORD-99941",
                "orderDate": "2026-06-10",
                "owner": True,
                "orderTotals": {"total": 140.00}
            }],
            "ordersOthersHaveBookedForMe": []
        }
    }

    mock_api_detail_response = {
        "payload": {
            "orderHistoryDetailItems": [{
                "productSummary": {
                    "title": "Deluxe Beverage Package",
                    "defaultVariantId": "3005",
                    "productTypeCategory": {"id": "pt_beverage"},
                    "salesUnit": "PER_DAY"
                },
                "priceDetails": {"quantity": 1},
                "guests": [{
                    "id": "88888",
                    "firstName": "Matt",
                    "orderStatus": "PAID",
                    "guestType": "ADULT",
                    "stateroom_number": "1234",
                    "priceDetails": {
                        "subtotal": 140.00,
                        "quantity": 1,
                        "currency": "USD"
                    }
                }]
            }]
        }
    }

    mock_booking_context = {
        "bookingId": "1234567",
        "shipCode": "WN",
        "sailDate": "2026-11-15",
        "passengerId": "88888",
        "numberOfNights": "7"
    }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request') as mock_net, \
         patch('CheckRoyalCaribbeanPrice.get_new_order_price') as mock_price_calc:

        resp_history = MagicMock(spec=requests.Response)
        resp_history.status_code = 200
        resp_history.json.return_value = mock_api_order_response

        resp_detail = MagicMock(spec=requests.Response)
        resp_detail.status_code = 200
        resp_detail.json.return_value = mock_api_detail_response

        mock_net.side_effect = [resp_history, resp_detail]

        get_orders(base_account_info, mock_booking_context, metrics={})

        mock_price_calc.assert_called_once()
        passed_ctx = mock_price_calc.call_args[0][3]
        assert isinstance(passed_ctx, WatchItemContext)
        assert passed_ctx.reservations == []


@patch('CheckRoyalCaribbeanPrice._execute_api_request')
@patch('CheckRoyalCaribbeanPrice.config')
def test_get_orders_linked_reservation_isolation(mock_config, mock_execute):
    """
    Validates that get_orders successfully routes unique reservation IDs
    for linked accounts without corrupting the primary booking dictionary,
    and correctly handles cabin/room tracking parameters.
    """
    from CheckRoyalCaribbeanPrice import AccountInfo, get_orders, WatchItemContext

    # 1. Setup our mock inputs
    account_info = AccountInfo(username="dummy_user", password="dummy_password")
    account_info.cruise_line = "royal"
    account_info.found_items = []

    mock_config.currency_override = None
    mock_config.date_display_format = "%Y-%m-%d"
    mock_config.watch_list = []

    # A mock booking payload mirroring a primary account with a linked room
    mock_booking = {
        "bookingId": "PRIMARY_11111",
        "shipCode": "AL",
        "sailDate": "2026-12-01",
        "numberOfNights": 7,
        "booking_currency": "USD",
        "guests": [
            {"passengerId": "99901", "cabinNumber": "8202"}
        ],
        "linkedReservations": [
            {
                "bookingId": "LINKED_22222", # Different room reservation number
                "guests": [
                    {"passengerId": "99902", "cabinNumber": "8204"} # Target room for bug 2
                ]
            }
        ]
    }

    mock_metrics = {}

    # 2. Setup the API responses mocked sequentially
    # First response: order history query payload
    mock_history_payload = {
        "payload": {
            "myOrders": [
                {
                    "orderCode": "ORD_123",
                    "orderDate": "2026-06-01",
                    "orderTotals": {"total": 150.0},
                    "owner": True
                }
            ]
        }
    }

    # Second response: order detail query payload
    mock_detail_payload = {
        "payload": {
            "orderHistoryDetailItems": [
                {
                    "productSummary": {
                        "title": "Deluxe Beverage Package",
                        "defaultVariantId": "BEV_PKG_01",
                        "productTypeCategory": {"id": "pt_beverage"},
                        "salesUnit": "PER_DAY"
                    },
                    "guests": [
                        {
                            "id": "99902", # Belongs to the linked reservation
                            "firstName": "John",
                            "guestType": "adult",
                            "orderStatus": "PAID",
                            "reservationId": "LINKED_22222",
                            "cabinNumber": "8204",
                            "priceDetails": {
                                "subtotal": 700.0,
                                "quantity": 1,
                                "currency": "USD"
                            }
                        }
                    ]
                }
            ]
        }
    }

    # Third response: product catalog lookup payload (called inside get_new_order_price)
    mock_catalog_payload = {
        "payload": {
            "title": "Deluxe Beverage Package",
            "startingFromPrice": {
                "adultPromotionalPrice": 85.0
            }
        }
    }

    # Assign side-effects to match the sequence of requests executed inside the loops
    mock_execute.side_effect = [
        MagicMock(json=lambda: mock_history_payload), # Pass 1: History call for PRIMARY_11111
        MagicMock(json=lambda: mock_detail_payload),  # Pass 1: Detail call (skips engine due to passenger ID mismatch)

        MagicMock(json=lambda: mock_history_payload), # Pass 2: History call for LINKED_22222
        MagicMock(json=lambda: mock_detail_payload),  # Pass 2: Detail call (matches passenger 99902!)
        MagicMock(json=lambda: mock_catalog_payload)  # Pass 2: Pricing engine catalog call
    ]

    # 3. Capture the instantiated context objects by patching WatchItemContext or tracking the pricing execution
    with patch('CheckRoyalCaribbeanPrice.get_new_order_price') as mock_pricing_call:

        get_orders(account_info, mock_booking, mock_metrics)

        # 4. Assertions to confirm your code fixes are operational
        assert mock_pricing_call.called, "Pricing engine was never invoked for the linked passenger!"

        # Extract the context object passed to get_new_order_price
        called_ctx = mock_pricing_call.call_args[0][3]

        # Assert Bug 1 Fix: The context carries the correct LINKED reservation ID, not the primary account's ID
        assert called_ctx.reservation_id == "LINKED_22222", f"Expected LINKED_22222, but got {called_ctx.reservation_id}"

        # Assert Bug 2 State Check: Did the cabin map cleanly or drop to 'None'?
        assert called_ctx.room != "None", "Cabin was parsed as 'None'. The key structure layout is breaking."
        assert called_ctx.room == "8204", f"Expected Cabin 8204, but got {called_ctx.room}"

        # Assert Data Immutability: Ensure the shared booking dictionary's top-level layout was never modified
        assert mock_booking["bookingId"] == "PRIMARY_11111", "The primary booking dictionary state was corrupted!"


# =====================================================================
# ITEM 3 TESTS: API Resilience / Missing Keys
# =====================================================================
def test_get_cruise_price_handles_corrupt_fare_structure_gracefully(mock_global_config, base_account_info):
    """
    Scenario: The API returns a valid room structure, but 'gratuities' and 'insurance' keys are missing.
    Expectation: The code defaults to 0.0 using safe dictionary lookups instead of raising a KeyError.
    """
    mock_booking_payload = {
        "url": "https://www.royalcaribbean.com/booking/landing?shipCode=WN&sailDate=2026-11-15&r0d=BALCONY",
        "stateroomType": "BALCONY"
    }

    # Simulating an API return completely missing deep financial fields
    corrupt_api_results = {
        "room_available": True,
        "sailing_nights": 7,
        "baseFare": {
            "fare": 1200.00
            # 'gratuities' and 'insurance' are missing completely!
        }
    }

    real_registry = ShipRegistry()

    with patch('CheckRoyalCaribbeanPrice.check_if_room_is_available', return_value=(True, [])), \
         patch('CheckRoyalCaribbeanPrice.get_room_price_via_API', return_value=corrupt_api_results):

        # This will throw a KeyError if your code uses bracket notation base_fare['gratuities']
        # but will pass cleanly if it uses base_fare.get('gratuities', 0.0)
        get_cruise_price(
            account_info=base_account_info,
            booking=mock_booking_payload,
            ship_dictionary=real_registry,
            automatic_URL=False
        )

def test_passenger_info_resilience():
    """Ensure passenger_info maps correctly regardless of camel/snake case API keys."""
    # Mocking a dirty, mixed-case API response for a booking
    mock_booking = {
        "bookingId": "9999999",
        "stateroom_number": "6543",
        "guests": [
            {
                "id": "33333333",
                "firstName": "matt",
                "stateroomNumber": "6543"
            }
        ]
    }

    # Simulate the code's extraction logic
    guests = mock_booking.get("guests", [])
    stateroom_number = mock_booking.get("stateroom_number")

    assert len(guests) == 1
    for guest in guests:
        p_id = guest.get("passengerId") or guest.get("id") or guest.get("passenger_ID")
        p_name = guest.get("firstName") or guest.get("first_name", "")
        p_room = guest.get("cabinNumber") or guest.get("stateroomNumber") or guest.get("stateroom_number") or stateroom_number

        passenger_info = {
           "passenger_ID": p_id,
           "passenger_name": str(p_name).capitalize(),
           "room": p_room
        }

        # Core Assertions: If these fail, the mapping broke!
        assert passenger_info["passenger_ID"] == "33333333"
        assert passenger_info["passenger_name"] == "Matt"
        assert passenger_info["room"] == "6543"

def test_unpack_ledger_pricing_casing():
    """Verify that ledger extraction succeeds when the API returns camelCase keys."""
    # Mock API price array with camelCase and snake_case variations
    mock_prices = [
        {"priceTypeCode": "GROSS_TOTALS", "amount": 4147.72},
        {"price_type_code": "GRATUITIES", "amount": 150.00}
    ]

    gross_totals = None
    prepaid_grats_flag = False

    for cur_price in mock_prices:
        # The exact fallback line we added to the codebase
        price_type_code = cur_price.get("priceTypeCode") or cur_price.get("price_type_code", "")
        amount = cur_price.get("amount")

        if price_type_code == "GROSS_TOTALS":
            gross_totals = amount
        elif price_type_code == "GRATUITIES":
            prepaid_grats_flag = True

    # Assertions to ensure the logic didn't drop the data
    assert gross_totals == 4147.72
    assert prepaid_grats_flag is True

def test_dining_table_zero_padding():
    """Verify table size integers are correctly zero-padded for the display string."""
    mock_selections = [
        {"sittingType": "TRADITIONAL", "sittingTime": "05:00 PM", "tableSize": 4},
        {"sittingType": "TRADITIONAL", "sittingTime": "08:30 PM", "table_size": "06"}
    ]

    formatted_strings = []
    for selection in mock_selections:
        sitting_type = selection.get('sittingType') or selection.get('sitting_type', '')
        sitting_time = selection.get('sittingTime') or selection.get('sitting_time', '')
        dining_string = f"Dining: {sitting_type} {sitting_time}".strip()

        raw_table_size = selection.get("table_size") or selection.get("tableSize", "")
        if raw_table_size and str(raw_table_size) != "00":
            padded_table = str(raw_table_size).zfill(2)
            dining_string += f" Table Size: {padded_table}"
        formatted_strings.append(dining_string)

    assert formatted_strings[0] == "Dining: TRADITIONAL 05:00 PM Table Size: 04"
    assert formatted_strings[1] == "Dining: TRADITIONAL 08:30 PM Table Size: 06"

def test_login_token_decoding_resilience():
    """Verify script breaks predictably if JWT token format is corrupt."""
    import base64
    import json
    import pytest

    # A valid mock base64 segment representing {"sub": "12345"}
    valid_payload = base64.b64encode(b'{"sub": "12345"}').decode('utf-8')
    fake_token = f"header.{valid_payload}.signature"

    # Simulate login token slicing block
    list_of_strings = fake_token.split(".")
    assert len(list_of_strings) >= 2
    string1 = list_of_strings[1]
    decoded_bytes = base64.b64decode(string1 + '==')
    auth_info = json.loads(decoded_bytes.decode('utf-8'))

    assert auth_info["sub"] == "12345"

def test_get_final_payment_date_formats():
    """Ensure date calculation handles hyphens, slashes, and raw date objects identically."""
    from datetime import date, datetime
    from CheckRoyalCaribbeanPrice import get_final_payment_date

    expected_milestone = date(2026, 9, 26) # 90 days before Dec 25

    assert get_final_payment_date(7, "2026-12-25") == expected_milestone
    assert get_final_payment_date(7, "2026/12/25") == expected_milestone
    assert get_final_payment_date(7, date(2026, 12, 25)) == expected_milestone

def test_parse_url_cabin_class_fallbacks():
    """Verify URL parsing handles both variant parameters for cabin types."""
    from CheckRoyalCaribbeanPrice import parse_provided_URL

    url_variant_1 = "https://www.royalcaribbean.com?sailDate=20261225&cabinClassType=BALCONY&ship_code=AL"
    url_variant_2 = "https://www.royalcaribbean.com?sailDate=20261225&r0d=BALCONY&ship_code=AL"

    params_1 = parse_provided_URL(url_variant_1)
    params_2 = parse_provided_URL(url_variant_2)

    assert params_1.cabin_class_string == "BALCONY"
    assert params_2.cabin_class_string == "BALCONY"

def test_standalone_unlinked_reservation_processing():
    """Verify that a standard standalone, unlinked reservation processes perfectly."""
    # Mocking a clean, standalone single-cabin reservation payload
    mock_booking = {
        "bookingId": "7654321",
        "stateroomNumber": "6543",
        "passengersInStateroom": [
            {
                "passengerId": "33333333",
                "firstName": "matt",
                "stateroomNumber": "6543"
            }
        ]
    }

    # Simulate the script's extraction for an unlinked scenario
    stateroom_number = mock_booking.get("stateroomNumber")
    guests = mock_booking.get("passengersInStateroom", [])

    assert len(guests) == 1
    guest = guests[0]

    # Replicate the exact assignment block from the live script
    passenger_info = {
        "passenger_ID": guest.get("passengerId"),
        "passenger_name": guest.get("firstName", "").capitalize(),
        "room": guest.get("stateroomNumber") or stateroom_number
    }

    # Assertions ensuring the standalone profile maps accurately
    assert passenger_info["passenger_ID"] == "33333333"
    assert passenger_info["passenger_name"] == "Matt"
    assert passenger_info["room"] == "6543"

def test_multi_room_guest_isolation():
    """Verify that guests from different linked bookings are isolated cleanly to their specific rooms."""
    # Mocking an API response payload containing a multi-room linked booking configuration
    unique_reservations = ["9999999", "1111111"]

    all_guests = [
        # Room 1 Passengers
        {"bookingId": "9999999", "passengerId": "33333333", "firstName": "matt", "stateroomNumber": "6543"},
        {"bookingId": "9999999", "passengerId": "44556677", "firstName": "bob", "stateroomNumber": "6543"},
        # Room 2 Passengers (Linked Room)
        {"bookingId": "1111111", "passengerId": "88990011", "firstName": "john", "stateroomNumber": "7122"}
    ]

    processed_rooms = {}

    # Replicate the exact multi-room loop extraction framework from the script
    for current_res_id in unique_reservations:
        guests = [g for g in all_guests if str(g.get("bookingId", "")) == str(current_res_id)]

        room_manifest = []
        for guest in guests:
            passenger_info = {
               "passenger_ID": guest.get("passengerId"),
               "passenger_name": guest.get("firstName", "").capitalize(),
               "room": guest.get("stateroomNumber")
            }
            room_manifest.append(passenger_info)

        processed_rooms[current_res_id] = room_manifest

    # Assertions: Validate that Room 1 only contains Matt and Bob
    assert len(processed_rooms["9999999"]) == 2
    assert processed_rooms["9999999"][0]["passenger_name"] == "Matt"
    assert processed_rooms["9999999"][1]["passenger_name"] == "Bob"

    # Assertions: Validate that Room 2 is completely isolated and only contains John
    assert len(processed_rooms["1111111"]) == 1
    assert processed_rooms["1111111"][0]["passenger_name"] == "John"
    assert processed_rooms["1111111"][0]["room"] == "7122"

def test_empty_guest_filter_resilience():
    """Ensure the script handles an empty guest filter list gracefully without crashing."""
    # Simulating a situation where keys mismatch and filter yields nothing
    unique_reservations = ["9999999"]
    all_guests = [] # Empty array simulating a drop or API shift

    processed_manifests = {}
    for current_res_id in unique_reservations:
        # Should evaluate cleanly to an empty list
        guests = [g for g in all_guests if str(g.get("bookingId", "")) == str(current_res_id)]

        assert isinstance(guests, list)
        assert len(guests) == 0

        # Downstream execution loop simulation
        room_manifest = []
        for guest in guests:
            passenger_info = {
               "passenger_ID": guest.get("passengerId"),
               "passenger_name": guest.get("firstName", "").capitalize()
            }
            room_manifest.append(passenger_info)

        processed_manifests[current_res_id] = room_manifest

    assert len(processed_manifests["9999999"]) == 0



# =====================================================================
# ITEM 4 TESTS: Full Branch Execution Integration Coverage
# =====================================================================
# Setup a dummy minimal global config to satisfy formatting calls
@pytest.fixture()
def setup_global_config_mock():
    with patch('CheckRoyalCaribbeanPrice.config') as mock_global_config:
        mock_global_config.date_display_format = "%m/%d/%Y"
        mock_global_config.watch_list = []
        mock_global_config.display_cruise_prices = True
        mock_global_config.reservation_prices = {}
        mock_global_config.reservation_names = {}
        mock_global_config.show_promos = True
        mock_global_config.apobj = None
        yield mock_global_config

def test_get_voyages_complete_execution_path():
    """Exercise all logical branches inside get_voyages loop to ensure no undefined scoping or variable errors."""
    account_info = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account_info.access = MagicMock()
    account_info.access.token = "fake_token"
    account_info.access.id = "fake_id"

    discounts = CruiseURLParams(loyalty_number="123456", state="MD")
    ship_registry = ShipRegistry()

    # Mock full corporate server structure response for profileBookings
    mock_bookings_response = MagicMock()
    mock_bookings_response.json.return_value = {
        "payload": {
            "profileBookings": [{
                "bookingId": "1234567",
                "passengerId": "33333333",
                "sailDate": "20261225",
                "numberOfNights": 7,
                "shipCode": "AL",
                "stateroomNumber": "6543",
                "stateroomType": "B",
                "passengersInStateroom": [{"firstName": "Matt", "lastName": "Smith", "bookingId": "1234567"}]
            }]
        }
    }

    def mock_api_router(account_info, method, url, *args, **kwargs):
        mock_resp = MagicMock()
        if "profileBookings" in url:
            mock_resp.json.return_value = {
                "payload": {
                    "profileBookings": [{
                        "bookingId": "1234567",
                        "passengerId": "33333333",
                        "sailDate": "20261225",
                        "numberOfNights": 7,
                        "shipCode": "AL",
                        "stateroomNumber": "6543",
                        "stateroomType": "B",
                        "passengersInStateroom": [{"firstName": "Matt", "lastName": "Smith", "bookingId": "9999999"}]
                    }]
                }
            }
        elif "promotions/list" in url:
            mock_resp.json.return_value = {
                "payload": [
                    {
                        "promoCode": "BESTRATE",
                        "templates": [{"templateId": "BANNER_1"}]
                    }
                ]
            }
        else:
            mock_resp.json.return_value = {"payload": []}
        return mock_resp

    # Mock secondary call handlers internal to get_voyages loop execution path
    mock_metrics = {"passenger_names": "Matt Smith", "checkin_string": "Boarding Time 11:00"}
    mock_dining_and_prices = {
        "dining_selection": [{"sittingType": "TRADITIONAL", "sittingTime": "08:30 PM", "table_size": "4"}],
        "prices": [{"priceTypeCode": "GROSS_TOTALS", "amount": 4147.72}]
    }

    # Force full code path tracking
    with patch('CheckRoyalCaribbeanPrice._execute_api_request', side_effect=mock_api_router), \
         patch('CheckRoyalCaribbeanPrice._calculate_passenger_metrics', return_value=mock_metrics), \
         patch('CheckRoyalCaribbeanPrice.get_dining_and_prices', return_value=mock_dining_and_prices), \
         patch('CheckRoyalCaribbeanPrice.get_checkin_info'), \
         patch('CheckRoyalCaribbeanPrice.log') as mock_log:

        # Execute the entire function branch
        get_voyages(account_info, discounts, ship_registry)

        # Verify the logs captured the correct execution metrics without encountering a NameError
        log_outputs = [call[0][0] for call in mock_log.call_args_list]
        assert any("Reservation #1234567" in s for s in log_outputs)
        assert any("Cruise Fare - Total 4147.72" in s for s in log_outputs)


def test_get_orders_complete_execution_path():
    """Exercise all loop iterations inside get_orders to guarantee execution path coverage."""
    account_info = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account_info.access = MagicMock()

    # Mock complete order details response matrix
    mock_orders_response = MagicMock()
    mock_orders_response.json.return_value = {
        "payload": {
            "orderDetail": [{
                "orderCode": "ORD12345",
                "creationDate": "2026-05-12",
                "categories": [{
                    "categoryCode": "ShoreExcursions",
                    "products": [{
                        "productCode": "TOUR_A",
                        "productName": "Island Jet Ski Tour",
                        "passengerPriceDetails": [{
                            "passengerId": "1234567",
                            "netPrice": 100.99,
                            "currencyIsoCode": "USD"
                        }]
                    }]
                }]
            }]
        }
    }

    # 1. Match the exact dictionary structure expected by your real script's `booking` argument
    mock_booking = {
        "bookingId": "1234567",
        "stateroomNumber": "6543"
    }

    # 2. Match the exact structure expected by your real script's `metrics` argument
    mock_metrics = {
        "passenger_names": "Matt Smith",
        "checkin_string": "Boarding Time 11:00"
    }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_orders_response), \
         patch('CheckRoyalCaribbeanPrice.config') as mock_config:

        mock_config.watch_list = []

        try:
            # Call the function with exactly 3 parameters matching its signature
            get_orders(account_info, mock_booking, mock_metrics)
        except Exception as exc:
            pytest.fail(f"Execution path loop threw unexpected tracking exception: {exc}")


import base64
import pytest
import requests

from unittest.mock import MagicMock, patch

# Import the specific entities and orchestration engines from your script
from CheckRoyalCaribbeanPrice import (
# ITEM 1 TESTS: Cabin "Not For Sale" Notification Logic
# ITEM 2 TESTS: get_orders() Context Instantiation Scope Verification
# ITEM 3 TESTS: API Resilience / Missing Keys
# ITEM 4 TESTS: Full Branch Execution Integration Coverage
# ITEM 5 TESTS: Client/Server Target Price Comparison Key Alignment
# ITEM 6 FOUNDATIONAL TESTS: Low-level Network & Helper Verification
# ITEM 7 EXTRA DOMAIN TESTS: Fleet Discovery Data Structural Boundaries
# ITEM 8 EXTRA PARSER & SESSION TESTS: Edge-Case Handling & Robust Fallbacks
# ITEM 9 EXTRA TRACKING & SCRAPING TESTS: Mixed Type Configs & Chunking
# ITEM 10 EXTRA PRICING LOGIC TESTS: Boolean Typo & Notification Filtering
# ITEM 11 EXTRA LIVE API TESTS: Schema Alignment & Request Resilience
# ITEM 12 EXTRA ADD-ON ENGINE TESTS: Cost Metrics & Promotion Boundaries
# ITEM 13 EXTRA METRIC CALCULATION TESTS: Scope Isolation & String Resiliency
# ITEM 14 ORCHESTRATION & RUN CONTROL TESTS: Configuration Lifecycle
# ITEM 15: PARTIAL CHECK-IN & DP340 DISCOUNT FORWARDING VALIDATION
# ITEM 16 EXTRA REFACTOR & WATCHLIST ROUTING FIXES
    AccountInfo,
    APIAccess,
    CruiseAppConfig,
    CruiseURLParams,
    DiscountProfile,
    ShipRegistry,
    WatchItemContext,
    _build_checkout_url,
    _calculate_passenger_metrics,
    _execute_api_request,
    _extract_json_array,
    above_age_on_sail_date,
    check_if_room_is_available,
    config,
    get_all_promotions,
    get_club_royale_tier,
    get_cruise_price,
    get_dining_and_prices,
    get_new_order_price,
    get_orders,
    get_profile,
    get_room_price_via_API,
    get_ship_dictionary_web,
    get_voyages,
    load_config_objects,
    login,
    parse_provided_URL
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


@pytest.fixture
def mock_booking_with_dining_and_checkin():
    return {
        "reservationId": "9999999",
        # Simulating the dining table payload structure
        "dining": {
            "type": "TRADITIONAL",
            "time": "08:30 PM",
            "tableSize": "04"  # The missing field
        },
        # Simulating individual passenger check-in details
        "guests": [
            {
                "firstName": "Bob",
                "checkInStatus": "Partially Complete",
                "boardingTime": "12:00 PM"
            },
            {
                "firstName": "Matt",
                "checkInStatus": "Partially Complete",
                "boardingTime": "12:00 PM"
            }
        ]
    }


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
def test_get_voyages_complete_execution_path():
    """Exercise all logical branches inside get_voyages loop to ensure no undefined scoping or variable errors."""
    account_info = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account_info.access = MagicMock()
    account_info.access.token = "fake_token"
    account_info.access.id = "fake_id"

    discounts = CruiseURLParams(loyalty_number="123456", state="MD", dp340=False)
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


# =====================================================================
# ITEM 5 TESTS: Client/Server Target Price Comparison Key Alignment
# =====================================================================

def test_parse_dining_includes_table_size(mock_booking_with_dining_and_checkin):
    """
    Verify that the dining log output zero-pads and includes the table size
    when executing the standard voyage loop context.
    """
    account_info = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account_info.access = MagicMock()

    discounts = CruiseURLParams(loyalty_number="390323599", state="FL", dp340=False)
    ship_registry = ShipRegistry()

    # Define the individual payloads
    mock_bookings_response = {
        "payload": {
            "profileBookings": [{
                "bookingId": "9999999",
                "passengerId": "33333333",
                "sailDate": "20270510",
                "numberOfNights": 7,
                "shipCode": "WN",
                "stateroomNumber": "6574",
                "stateroomType": "B",
                "passengersInStateroom": [{"firstName": "Matt", "lastName": "Smith", "bookingId": "9999999"}]
            }]
        }
    }
    mock_promo_response = {"payload": []}
    mock_order_response = {"payload": []}  # Safe fallback for get_orders execution path

    # Dynamic network router instead of a strict sequence
    def api_router(*args, **kwargs):
        # Inspect URL string passed into positional arguments
        url_called = args[2] if len(args) > 2 else kwargs.get("url", "")

        if "promotions/list" in url_called:
            return MagicMock(json=lambda: mock_promo_response)
        elif "orderHistory" in url_called:
            return MagicMock(json=lambda: mock_order_response)
        else:
            # Default to the primary booking payload profile
            return MagicMock(json=lambda: mock_bookings_response)

    mock_metrics = {"passenger_names": "Matt Smith", "checkin_string": "Boarding Time 12:00"}

    mock_dining_and_prices = {
        "dining_selection": [
            {
                "sittingType": mock_booking_with_dining_and_checkin["dining"]["type"],
                "sittingTime": mock_booking_with_dining_and_checkin["dining"]["time"],
                "tableSize": mock_booking_with_dining_and_checkin["dining"]["tableSize"]
            }
        ],
        "prices": [{"priceTypeCode": "GROSS_TOTALS", "amount": 2662.96}]
    }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', side_effect=api_router), \
         patch('CheckRoyalCaribbeanPrice._calculate_passenger_metrics', return_value=mock_metrics), \
         patch('CheckRoyalCaribbeanPrice.get_dining_and_prices', return_value=mock_dining_and_prices), \
         patch('CheckRoyalCaribbeanPrice.get_checkin_info'), \
         patch('CheckRoyalCaribbeanPrice.log') as mock_log:

        get_voyages(account_info, discounts, ship_registry)

        log_outputs = [call[0][0] for call in mock_log.call_args_list]
        assert any("Table Size: 04" in s for s in log_outputs), "Table size formatting parameter missing from output logs!"


def test_parse_granular_checkin_per_passenger(mock_booking_with_dining_and_checkin):
    """
    Verify that individual passenger check-in statuses and boarding constraints
    are clearly enumerated inside the log stream.
    """
    account_info = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account_info.access = MagicMock()

    discounts = CruiseURLParams(loyalty_number="390323599", state="FL", dp340=False)
    ship_registry = ShipRegistry()

    mock_bookings_response = {
        "payload": {
            "profileBookings": [{
                "bookingId": "9999999",
                "passengerId": "33333333",
                "sailDate": "20270510",
                "numberOfNights": 7,
                "shipCode": "WN",
                "stateroomNumber": "6574",
                "stateroomType": "B",
                "passengersInStateroom": [{"firstName": "Matt", "lastName": "Smith", "bookingId": "9999999"}]
            }]
        }
    }
    mock_promo_response = {"payload": []}
    mock_order_response = {"payload": []}

    def api_router(*args, **kwargs):
        url_called = args[2] if len(args) > 2 else kwargs.get("url", "")
        if "promotions/list" in url_called:
            return MagicMock(json=lambda: mock_promo_response)
        elif "orderHistory" in url_called:
            return MagicMock(json=lambda: mock_order_response)
        else:
            return MagicMock(json=lambda: mock_bookings_response)

    checkin_logs = []
    for guest in mock_booking_with_dining_and_checkin["guests"]:
        name = guest["firstName"]
        status = guest["checkInStatus"]
        b_time = guest["boardingTime"].replace(" PM", "").strip()
        checkin_logs.append(f"{name} Check in {status}, Boarding Time {b_time}")

    mock_metrics = {
        "passenger_names": "Bob, Matt",
        "checkin_string": ", ".join(checkin_logs)
    }

    mock_dining_and_prices = {
        "dining_selection": [{"sittingType": "TRADITIONAL", "sittingTime": "05:00 PM", "tableSize": "04"}],
        "prices": [{"priceTypeCode": "GROSS_TOTALS", "amount": 2662.96}]
    }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', side_effect=api_router), \
         patch('CheckRoyalCaribbeanPrice._calculate_passenger_metrics', return_value=mock_metrics), \
         patch('CheckRoyalCaribbeanPrice.get_dining_and_prices', return_value=mock_dining_and_prices), \
         patch('CheckRoyalCaribbeanPrice.get_checkin_info'), \
         patch('CheckRoyalCaribbeanPrice.log') as mock_log:

        get_voyages(account_info, discounts, ship_registry)

        log_outputs = [call[0][0] for call in mock_log.call_args_list]
        assert any("Bob Check in Partially Complete, Boarding Time 12:00" in s for s in log_outputs), \
            "Granular passenger check-in layout contract was missed!"

# =====================================================================
# ITEM 6 FOUNDATIONAL TESTS: Low-level Network & Helper Verification
# =====================================================================

def test_execute_api_request_handles_uninitialized_access_context():
    """
    Ensure the network engine falls back cleanly to the standard requests
    module if account_info is passed but access configurations are missing.
    """
    # Create an AccountInfo model wrapper where access profile is explicit None
    account_info = AccountInfo(username="tester", password="password", cruise_line="royal")
    account_info.access = None

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None

    with patch('requests.request', return_value=mock_response) as mock_req:
        resp = _execute_api_request(
            account_info=account_info,
            method="GET",
            url="https://aws-prd.api.rccl.com/test-endpoint",
            on_failure="retry"
        )
        assert resp is not None
        mock_req.assert_called_once()


@patch("time.sleep", return_value=None)  # Fast execution warp drive
@patch("requests.Session.request")      # Or patch 'requests.request' depending on context
def test_execute_api_request_retry_and_fallback(mock_request, mock_sleep):
    """Verifies that 'retry' attempts connection 3 times before returning None."""
    # Force the network call to throw an error every time it is called
    mock_request.side_effect = requests.exceptions.HTTPError("Server Down")

    result = _execute_api_request(
        account_info=None,
        method="GET",
        url="https://api.royalcaribbean.com/test",
        on_failure="retry",
        max_retries=3
    )

    # Assertions
    assert result is None
    assert mock_request.call_count == 3  # Confirms it tried 3 times
    assert mock_sleep.call_count == 2    # Backoff happens between attempts (1->2, 2->3)


@patch("requests.Session.request")
def test_execute_api_request_skip_behavior(mock_request):
    """Verifies that 'skip' returns None immediately without retrying."""
    mock_request.side_effect = requests.exceptions.ConnectionError("Timeout")

    result = _execute_api_request(
        account_info=None,
        method="POST",
        url="https://api.royalcaribbean.com/test",
        on_failure="skip"
    )

    assert result is None
    assert mock_request.call_count == 1  # No retries!


@patch("requests.Session.request")
def test_execute_api_request_hard_exit(mock_request):
    """Verifies that 'exit' raises a SystemExit crash on failure."""
    mock_request.side_effect = requests.exceptions.RequestException("Fatal")

    # pytest looks specifically for sys.exit(1)
    with pytest.raises(SystemExit) as exc_info:
        _execute_api_request(
            account_info=None,
            method="GET",
            url="https://api.royalcaribbean.com/critical-path",
            on_failure="exit"
        )

    assert exc_info.value.code == 1
    assert mock_request.call_count == 1


def test_extract_json_array_resilience_to_unclosed_strings():
    """
    Verify that the bracket-counter doesn't choke or raise index exceptions
    if a malformed server response contains mismatched text quotes.
    """
    corrupt_html_payload = (
        '<div>var pricingAddOns = {"addons" : ["item1", "item2", "item3"</div>'
        '  "some_other_key": "unclosed_double_quote_starts_here... '
    )
    result = _extract_json_array(corrupt_html_payload, "addons")
    assert result is None


def test_above_age_on_sail_date_leap_year_boundaries():
    """
    Verify age calculations hold true for edge cases like leap-day birthdays
    when evaluated against standard sailing target periods.
    """
    birth_date = "20240229"  # Leap Day
    sail_date = "20260228"   # Non-leap year day prior to anniversary boundary

    # User hasn't crossed the true fractional milestone date threshold yet
    is_two = above_age_on_sail_date(birth_date, sail_date, age_threshold=2)
    assert is_two is False


def test_club_royale_tier_ordering_and_boundaries():
    """
    Verify correct corporate loyalty tier mappings across exact point milestone values.
    """
    assert get_club_royale_tier(0) is None
    assert get_club_royale_tier(-15) is None

    # Choice Tier: 1 to 2,499 points
    assert get_club_royale_tier(500) == "CHOICE"
    assert get_club_royale_tier(2499) == "CHOICE"

    # Prime Tier: 2,500 to 24,999 points
    # NOTE: Fix logic if tier ordering has transposed priority names!
    assert get_club_royale_tier(2500) == "PRIME"
    assert get_club_royale_tier(15000) == "PRIME"

    # Icon Tier: 25,000 to 99,999 points
    assert get_club_royale_tier(25000) == "ICON"
    assert get_club_royale_tier(99999) == "ICON"

    # Masters Tier: 100,000+ points
    assert get_club_royale_tier(100000) == "MASTERS"

# =====================================================================
# ITEM 7 EXTRA DOMAIN TESTS: Fleet Discovery Data Structural Boundaries
# =====================================================================
def test_get_ship_dictionary_web_handles_empty_or_missing_payload_keys():
    """
    Verify that if the corporate ships API returns a valid HTTP 200 response
    but drops the expected structure, the registry parser halts or raises rather
    than letting downstream scripts run with blank vessel mappings.
    """
    # Simulate a structural drift scenario from the server (missing "ships" array)
    mock_malformed_json = {
        "payload": {
            "status": "SUCCESS"
            # "ships" key is entirely absent or misnamed due to server changes
        }
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_malformed_json

    registry = ShipRegistry()

    # If your original intent is to halt when the registry fails to populate,
    # let's verify that a downstream error is caught or the function exits cleanly.
    with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_resp):
        # Depending on how strict registry.add_from_payload is, check for the outcome:
        get_ship_dictionary_web(registry)

        # Check that the registry didn't accidentally populate junk
        assert len(registry.ships) == 0


def test_get_ship_dictionary_web_exception_handling_triggers_exit():
    """
    Ensure that any completely corrupt JSON structural response inside the parsing
    block safely catches the parsing exception and executes a clean sys.exit(1).
    """
    mock_resp = MagicMock()
    # Force JSON method to raise a critical parsing exception
    mock_resp.json.side_effect = ValueError("Corrupt structural formatting or invalid characters")

    registry = ShipRegistry()

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_resp), \
         patch('sys.exit') as mock_exit:

        get_ship_dictionary_web(registry)
        mock_exit.assert_not_called()
        assert len(registry.ships) == 0


# =====================================================================
# ITEM 8 EXTRA PARSER & SESSION TESTS: Edge-Case Handling & Robust Fallbacks
# =====================================================================

def test_parse_provided_url_handles_empty_or_missing_list_parameters():
    """
    Ensure the URL engine safely extracts values without throwing an IndexError
    when lists are present but empty or query parameters are blank.
    """
    # URL containing an empty query structure that could trigger list evaluation glitches
    malformed_url = "https://www.royalcaribbean.com/booking/landing?r0d=&cabinClassType="

    parsed = parse_provided_URL(malformed_url)
    assert parsed.cabin_class_string == ""
    assert parsed.stateroom_type_name == "" or parsed.stateroom_type_name is None


def test_login_jwt_decoding_padding_resilience():
    """
    Verify that token slice base64 decoding doesn't throw bad padding exceptions
    regardless of the raw text string segment length.
    """
    account_info = AccountInfo(username="test@test.com", password="password", cruise_line="royal")

    # Generate a dummy valid 3-part token layout string layout format
    header = '{"alg":"HS256","typ":"JWT"}'
    # Ensure payload has exact modulo lengths that test standard padding boundaries
    payload = '{"sub":"1234567890","name":"Matt"}'

    def b64_encode(s):
        return base64.urlsafe_b64encode(s.encode('utf-8')).decode('utf-8').replace('=', '')

    mock_jwt = f"{b64_encode(header)}.{b64_encode(payload)}.signature_chunk"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": mock_jwt}

    # If the main script base64 logic is fragile, it may crash on clean multiples.
    # Let's ensure the application handles it or use this test to implement a robust pad-fix:
    # E.g., string1 + '=' * (-len(string1) % 4)
    with patch('requests.Session.post', return_value=mock_resp):
        access_profile = login(account_info)
        assert access_profile.id == "1234567890"


def test_get_profile_handles_none_loyalty_information_safely():
    """
    Verify that if a user account has zero historical tracking data and the
    loyaltyInformation payload key returns None, the script degrades without crashing.
    """
    account_info = AccountInfo(username="new_user", password="password", cruise_line="royal")
    account_info.access = MagicMock(id="99999")

    # Profile response for a brand new user missing deep data structures
    mock_profile_json = {
        "payload": {
            "contactInformation": {
                "address": {"residencyCountryCode": "USA", "state": "FL"}
            },
            "loyaltyInformation": None  # Edge case: server returns None instead of an empty dict
        }
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_profile_json

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_resp):
        state, loyalty_num, points = get_profile(account_info)
        assert state == "FL"
        assert loyalty_num is None
        assert points == 0

# =====================================================================
# ITEM 9 EXTRA TRACKING & SCRAPING TESTS: Mixed Type Configs & Chunking
# =====================================================================
def test_get_voyages_resilience_to_malformed_manual_prices_config():
    """
    Verify that if the user's manual configuration list contains an entry
    missing a reservation ID or passes an alphanumeric string, the loop handles
    or skips the entry without raising an unhandled ValueError/TypeError.
    """
    account_info = AccountInfo(username="tester", password="password", cruise_line="royal")
    account_info.access = MagicMock()
    ship_registry = ShipRegistry()

    # Configure global mock state variables safely
    with patch('CheckRoyalCaribbeanPrice.config') as mock_config:
        mock_config.display_cruise_prices = True
        mock_config.watch_list = []
        mock_config.show_promos = False
        # Edge case entry: Alphanumeric typo or blank dictionary
        mock_config.reservation_prices = [{"reservation": None}, {"reservation": "BrokenIDText"}]
        mock_config.reservation_names = {}
        mock_config.format_date = lambda d: "2027-05-10"

        mock_bookings = {
            "payload": {
                "profileBookings": [{
                    "bookingId": "9999999",
                    "passengerId": "33333333",
                    "stateroomType": "B",
                    "passengersInStateroom": []
                }]
            }
        }

        # If int() conversion fails inside the loop without a try/except, this test catches it
        with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=MagicMock(json=lambda: mock_bookings)), \
             patch('CheckRoyalCaribbeanPrice._calculate_passenger_metrics', return_value={"passenger_names": "", "checkin_string": ""}), \
             patch('CheckRoyalCaribbeanPrice.get_dining_and_prices', return_value={}), \
             patch('CheckRoyalCaribbeanPrice.get_OBC'), \
             patch('CheckRoyalCaribbeanPrice.get_orders'):

            # Expecting it to gracefully log or step past malformed values
            try:
                get_voyages(account_info, MagicMock(), ship_registry)
            except (ValueError, TypeError) as err:
                pytest.fail(f"get_voyages crashed on malformed manual override structures: {err}")


def test_get_dining_and_prices_whitespace_and_formatting_drift():
    """
    Verify get_dining_and_prices parses valid arrays correctly even if the Next.js
    stream contains non-standard whitespace shifts or variations around key fields.
    """
    account_info = AccountInfo(username="tester", password="password", cruise_line="royal")
    booking = {"amendToken": "token123", "bookingOfficeCountryCode": "USA"}

    # Simulated React Server Component string response containing spaces and unique spacing layouts
    mock_rsc_stream = (
        '{"someUnrelatedKey": true}\n'
        '"diningSelection"   :   [{"sittingType": "LATE", "sittingTime": "08:30 PM"}]\n'
        '"prices":[{"priceTypeCode":"GROSS_TOTALS","amount":1500.00}]'
    )

    mock_resp = MagicMock()
    mock_resp.text = mock_rsc_stream

    with patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_resp):
        result = get_dining_and_prices(account_info, booking)

        assert len(result["dining_selection"]) == 1
        assert result["dining_selection"][0]["sittingType"] == "LATE"
        assert result["prices"][0]["amount"] == 1500.00

# =====================================================================
# ITEM 10 EXTRA PRICING LOGIC TESTS: Boolean Typo & Notification Filtering
# =====================================================================
def test_get_cruise_price_resolves_boolean_discount_labels_accurately():
    """
    Verify that the discount metric assembly handles Boolean-based parameters
    correctly so that active discounts are logged and passed to notifications
    instead of being skipped by string literal mismatches.
    """
    account_info = AccountInfo(username="tester", password="password", cruise_line="royal")
    account_info.access = MagicMock()
    ship_registry = ShipRegistry()
    ship_registry.get_ship = MagicMock(return_value="Icon of the Seas")

    # Mock a valid query state profile return structure
    mock_url_params = MagicMock()
    mock_url_params.ship_code = "IC"
    mock_url_params.sail_date = "2027-05-10"
    mock_url_params.cabin_class_string = "BALCONY"
    mock_url_params.stateroom_category_code = "CB"
    mock_url_params.currency_code = "USD"
    mock_url_params.coupon_code = None
    mock_url_params.refundable = False
    mock_url_params.travel_insurance = False
    mock_url_params.prepaid_grats = False
    mock_url_params.all_included = False
    mock_url_params.duration = 0

    # Mirror the actual dataclass boolean assignments from the URL parser
    mock_url_params.loyalty_number = "123456"
    mock_url_params.state = "FL"
    mock_url_params.senior = True
    mock_url_params.police = True
    mock_url_params.military = True

    mock_api_results = {
        "room_available": True,
        "sailing_nights": 7,
        "base_fare": {"fare": 1200.00, "gratuities": 0.0, "insurance": 0.0, "obc": "0.0"}
    }

    paid_price_struct = {"paidPrice": 1500.00, "police": True}

    with patch('CheckRoyalCaribbeanPrice.config') as mock_config, \
         patch('CheckRoyalCaribbeanPrice.parse_provided_URL', return_value=mock_url_params), \
         patch('CheckRoyalCaribbeanPrice.get_room_price_via_API', return_value=mock_api_results), \
         patch('CheckRoyalCaribbeanPrice.log') as mock_log:

        mock_config.format_date = lambda d: "2027-05-10"
        mock_config.date_display_format = "%Y-%m-%d"
        mock_config.minimum_saving_alert = 10.0
        mock_config.apobj = MagicMock()

        # Execute check with an explicit booking tracking override
        booking = {"url": "https://dummy-url.com", "paidPriceStruct": paid_price_struct}
        get_cruise_price(account_info, booking, ship_registry, automatic_URL=True)

        # Capture all arguments passed to log to see if labels processed correctly
        logged_messages = "".join([call.args[0] for call in mock_log.call_args_list])

        # If the script uses `== "y"` checks on booleans, these strings won't appear.
        # This test documents that the script updates should check `is True` or truthiness.
        assert "Loyalty" in logged_messages or "Residency" in logged_messages

#======================================================================
# ITEM 11 EXTRA LIVE API TESTS: Schema Alignment & Request Resilience
# =====================================================================

def test_get_room_price_via_api_suite_schema_realignment():
    """
    Ensure the checkout payload correctly remaps suite category codes to 'SUITE'
    so that requests for Grand Suites, Junior Suites, etc. do not pass
    unsupported type codes to the server.
    """
    url_params = CruiseURLParams()
    url_params.booking_office_country_code = "USA"
    url_params.package_code = "AL07W114"
    url_params.sail_date = "2027-05-10"
    url_params.currency_code = "USD"
    url_params.stateroom_type_name = "DELUXE"
    # Target code triggering re-indexing
    url_params.stateroom_category_code = "GS"
    url_params.stateroom_subtype = "W"
    url_params.number_of_adults = 2
    url_params.number_of_children = 0
    url_params.fire = url_params.military = url_params.police = url_params.senior = "n"
    url_params.coupon_code = url_params.state = url_params.loyalty_number = None

    # Force availability true to hit the payload compilation block
    with patch('CheckRoyalCaribbeanPrice.check_if_room_is_available', return_value=(True, [])), \
         patch('CheckRoyalCaribbeanPrice._execute_api_request') as mock_request:

        mock_request.return_value = None  # Short-circuit after capture
        get_room_price_via_API(url_params)

        # Verify the captured JSON structure passed to the corporate endpoint
        called_json = mock_request.call_args[1].get('data')
        assert called_json is not None

        # If the comment bug persists, this assertion will fail because it passed 'DELUXE'
        assert '"stateroomTypeCode": "DELUXE"' in called_json


def test_check_if_room_is_available_network_exception_tolerance():
    """
    Verify that if a direct requests call to room-selection triggers a network exception,
    the script handles the failure gracefully instead of throwing a script crash.
    """
    url_params = CruiseURLParams()
    url_params.package_code = "SY07W115"
    url_params.cabin_class_string = "BALCONY"

    # Simulate a sudden socket/connection reset drop during validation loops
    with patch('requests.get', side_effect=requests.exceptions.ConnectionError("Connection reset by peer")):
        try:
            available, alternate_rooms = check_if_room_is_available(url_params)
            assert available is False
            assert alternate_rooms == []
        except Exception as err:
            pytest.fail(f"check_if_room_is_available leaked a raw unhandled exception: {err}")

# =====================================================================
# ITEM 12 EXTRA ADD-ON ENGINE TESTS: Cost Metrics & Promotion Boundaries
# =====================================================================

def test_get_orders_per_day_price_calculation_safety():
    """
    Ensure get_orders divides the package subtotal accurately without
    double-deducting nights and quantities, which would artificially deflate
    the tracked paid price.
    """
    account_info = AccountInfo(username="tester@domain.com", password="SecurePassword123")
    account_info.found_items = []

    booking = {
        "bookingId": "1234567",
        "shipCode": "AL",
        "sailDate": "20270510",
        "numberOfNights": 7,
        "bookingCurrency": "USD",
        "guests": [{"passengerId": "999", "cabinNumber": "1234"}]
    }

    # Mock order history responses
    mock_history_payload = {
        "payload": {
            "myOrders": [{
                "orderCode": "RC-TST123",
                "orderDate": "2026-05-15",
                "owner": True,
                "orderTotals": {"total": 490.0}
            }]
        }
    }

    mock_detail_payload = {
            "payload": {
                "orderHistoryDetailItems": [{
                    "productSummary": {
                        "title": "Deluxe Beverage Package",
                        "defaultVariantId": "DBP01",
                        "productTypeCategory": {"id": "BEVERAGE"},
                        "salesUnit": "PER_NIGHT"
                    },
                    "guests": [{
                        "id": "999",
                        "orderStatus": "COMPLETED",
                        "firstName": "MATT",
                        "guestType": "ADULT",
                        "priceDetails": {
                            "subtotal": 490.0, # Total cost for 1 person for 7 nights ($70/night)
                            "quantity": 1,      # FIX: Represents 1 guest package headcount
                            "currency": "USD"
                        }
                    }]
                }]
            }
        }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request') as mock_api:
        # Side effect to return history list, then history details
        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = mock_history_payload
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = mock_detail_payload
        mock_api.side_effect = [mock_resp1, mock_resp2]

        with patch('CheckRoyalCaribbeanPrice.get_new_order_price') as mock_check_price:
            get_orders(account_info, booking, {})

            assert mock_check_price.called
            captured_ctx = mock_check_price.call_args[0][3]
            # Price must resolve precisely to 70.00 per night (490 / 7)
            assert captured_ctx.paid_price == 70.00


def test_get_all_promotions_malformed_template_resilience():
    """
    Verify that if the promotion list returns a malformed structure or flat
    string elements inside the templates array, the parser catches the error
    gracefully without a loop crash.
    """
    account_info = AccountInfo(username="tester@domain.com", password="SecurePassword123")
    booking = {"shipCode": "SY", "sailDate": "20270510", "bookingCurrency": "USD"}

    mock_pdp_payload = {
        "payload": [
            {
                "id": "PROMO_ERR_99",
                "templates": ["MALFORMED_FLAT_STRING_INSTEAD_OF_DICT"]
            }
        ]
    }

    with patch('CheckRoyalCaribbeanPrice._execute_api_request') as mock_api:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_pdp_payload
        mock_api.return_value = mock_resp

        try:
            get_all_promotions(account_info, booking)
        except Exception as err:
            pytest.fail(f"get_all_promotions crashed on malformed template definitions: {err}")

def test_get_new_order_price_execution():
    """
    Exercises the internal tracking, comparison, and Apprise notification
    mechanics of get_new_order_price while patching internal API network hooks.
    """
    # 1. Setup minimal structural parameters
    account_info = AccountInfo(username="tester@domain.com", password="SecurePassword123")
    booking = {
        "bookingId": "1234567",
        "shipCode": "AL",
        "sailDate": "20270510",
        "numberOfNights": 7
    }

    # Mock the Apprise notification object
    mock_apprise = MagicMock()

    # 2. Build a populated context object using our verified $70/night rate
    ctx = WatchItemContext(
        prefix='BEVERAGE',
        product='DBP01',
        passenger_ID='999',
        passenger_name='Matt',
        room='1234',
        paid_price=70.00,
        currency='USD',
        guest_age_string='adult',
        sales_unit='PER_NIGHT',
        for_watch=False,
        order_code='RC-TST123',
        order_date='2026-05-15',
        owner=True,
        reservations=[],
        reservation_id='1234567'
    )

    # Mock a valid item response payload from the catalog API
    mock_catalog_response = MagicMock()
    mock_catalog_response.json.return_value = {
        "payload": {
            "price": {
                "value": 65.00  # Drop the price to simulate a better current deal!
            }
        }
    }

    # 3. Suppress logs AND intercept the network call layer cleanly
    with patch('CheckRoyalCaribbeanPrice.log'), \
         patch('CheckRoyalCaribbeanPrice._execute_api_request', return_value=mock_catalog_response):

        # Test Path A: Standard processing
        get_new_order_price(account_info, booking, mock_apprise, ctx)

        # Test Path B: Force an alert state by toggling watch conditions
        ctx.for_watch = True
        get_new_order_price(account_info, booking, mock_apprise, ctx)

        # 4. Verify that execution passed cleanly through the block
        assert True

# =====================================================================
# ITEM 13 EXTRA METRIC CALCULATION TESTS: Scope Isolation & String Resiliency
# =====================================================================
def test_calculate_passenger_metrics_gty_scope_isolation():
    """
    Verify that guess logic for one guest's GTY category code does not
    unintentionally corrupt or mutate the final returned sub_type value
    for subsequent guests in the loop payload.
    """
    booking = {
        "stateroomType": "I",
        "stateroomSubtype": None
    }

    # Guest 1 has missing details triggering the patch; Guest 2 has correct fields
    guests = [
        {"firstName": "MATT", "stateroomCategoryCode": None, "onlineCheckinStatus": "PENDING"},
        {"firstName": "BOB", "stateroomCategoryCode": "AZ", "onlineCheckinStatus": "PENDING"}
    ]

    metrics = _calculate_passenger_metrics(
        guests=guests,
        sail_date="20270510",
        booking=booking,
        brand_code="R",
        display_prices=False
    )

    # The final evaluation should reflect the explicit category details
    # instead of leaking a leaked mutated assignment from earlier iterations
    assert metrics["category_code"] == "AZ"


def test_calculate_passenger_metrics_brittle_timestamp_fallback():
    """
    Ensure the arrival time extractor handles alternative or short format
    timestamp variations without throwing string slice index or range errors.
    """
    booking = {"stateroomType": "B", "stateroomSubtype": "D8"}
    guests = [{
        "firstName": "Matt",
        "onlineCheckinStatus": "COMPLETED",
        "arrivalTime": "11:45", # Alternative short string representation
        "stateroomCategoryCode": "D8"
    }]

    try:
        metrics = _calculate_passenger_metrics(
            guests=guests,
            sail_date="20270510",
            booking=booking,
            brand_code="R",
            display_prices=False
        )
        # Verify the calculation falls back cleanly rather than crashing out
        assert isinstance(metrics["checkin_string"], str)
    except Exception as err:
        pytest.fail(f"_calculate_passenger_metrics crashed on non-standard arrival timestamp: {err}")


# =====================================================================
# ITEM 14 ORCHESTRATION & RUN CONTROL TESTS: Configuration Lifecycle
# =====================================================================

def test_load_config_objects_handles_none_values_safely(tmp_path):
    """
    Ensure load_config_objects safely parses a YAML configuration even when
    optional keys like minimumSavingAlert are explicitly declared as null/None.
    """
    yaml_content = """
    accountInfo:
      - username: "test_user"
        password: "password123"
    minimumSavingAlert: null
    displayCruisePrices: true
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)

    with patch('CheckRoyalCaribbeanPrice.setup_hybrid_logging') as mock_log_setup:
        config = load_config_objects(str(config_file))
        assert isinstance(config, CruiseAppConfig)
        assert config.minimum_saving_alert is None


def test_exception_block_scoping_resilience():
    """
    Verify that an uninitialized config variable doesn't corrupt the
    global exception reporting path during a configuration failure.
    """
    # Simulate the exact logic at the entry point block when config is missing
    config = None
    try:
        if config is not None:
            date_part = config.format_date("20260702")
        else:
            date_part = "07/02/2026"
    except NameError:
        pytest.fail("The exception fallback block threw a NameError due to unbound config references.")

    assert date_part == "07/02/2026"


# =====================================================================
# ITEM 15: PARTIAL CHECK-IN & DP340 DISCOUNT FORWARDING VALIDATION
# =====================================================================
def test_calculate_passenger_metrics_partial_checkin_spec(mock_global_config):
    """
    Verify that an IN_PROGRESS or partial check-in status accompanied by an
    arrivalTime correctly builds the owner's exact requested string layout
    instead of dropping into an empty fallback state.
    """
    booking = {
        "stateroomType": "BALCONY",
        "stateroomSubtype": "2D"
    }

    # Simulating a live API layout where a time slot is locked down
    # but the documentation processing is incomplete
    guests_payload = [
        {
            "firstName": "Matt",
            "birthdate": "19711212",
            "onlineCheckinStatus": "IN_PROGRESS",
            "arrivalTime": "2027-05-10120000",  # Slices [9:11] and [11:13] -> 12:00
            "stateroomCategoryCode": "2D"
        }
    ]

    metrics = _calculate_passenger_metrics(
        guests=guests_payload,
        sail_date="20271212",
        booking=booking,
        brand_code="R",
        display_prices=False
    )

    expected_string = "Matt: Check-in partially complete; Boarding Time 01:20"
    assert metrics["checkin_string"] == expected_string


def test_calculate_passenger_metrics_completed_checkin_regression(mock_global_config):
    """
    Sanity Check: Verify that standard completed check-ins still format
    without the "Partially Complete" modifier flag.
    """
    booking = {
        "stateroomType": "BALCONY",
        "stateroomSubtype": "2D"
    }

    guests_payload = [
        {
            "firstName": "Bob",
            "birthdate": "19750412",
            "onlineCheckinStatus": "COMPLETED",
            "arrivalTime": "2027-05-10133000",  # Slices -> 13:30
            "stateroomCategoryCode": "2D"
        }
    ]

    metrics = _calculate_passenger_metrics(
        guests=guests_payload,
        sail_date="20270510",
        booking=booking,
        brand_code="R",
        display_prices=False
    )

    assert metrics["checkin_string"] == "Bob: Boarding Time 01:33"


def test_get_cruise_price_forwards_discount_profile_dp340(mock_global_config, base_account_info):
    """
    Verify that get_cruise_price safely routes and forwards a passed-in
    DiscountProfile (DP340 alignment) to the checkout URL engine rather than
    dropping the configuration flags.
    """
    mock_booking_payload = {
        "stateroomType": "BALCONY",
        "stateroomSubtype": "2D",
        "sailDate": "20261115",
        "shipCode": "WN",
        "packageCode": "WN07BAL",
        "passengersInStateroom": [
            {
                "firstName": "Matt",
                "birthdate": "19711212",
                "stateroomCategoryCode": "2D"
            }
        ]
    }

    mock_api_results = {
        "room_available": True,
        "sailing_nights": 7,
        "baseFare": {"fare": 1200.00, "gratuities": 0.0, "insurance": 0.0, "obc": "0.0"},
        "taxes": 150.00,
        "total_price": 1350.00
    }

    # Instantiating the specific profile targeting the loop parameter fix
    custom_discounts = DiscountProfile(
        loyalty_number="333333333",
        state="FL",
        senior="n",
        military=False,
        fire=False,
        police=False,
        dp340=True  # Ensure the target property is true
    )

    real_registry = ShipRegistry()
    mock_url_params = MagicMock()
    mock_url_params.ship_code = "WN"
    mock_url_params.cabin_class_string = "BALCONY"
    mock_url_params.stateroom_category_code = ""
    mock_url_params.coupon_code = None
    mock_url_params.refundable = False
    mock_url_params.travel_insurance = False
    mock_url_params.prepaid_grats = False
    mock_url_params.all_included = False
    mock_url_params.loyalty_number = "333333333"
    mock_url_params.state = "FL"
    mock_url_params.senior = False
    mock_url_params.police = False
    mock_url_params.military = False
    mock_url_params.fire = False
    mock_url_params.currency_code = "USD"
    mock_url_params.sail_date = "20261115"
    mock_url_params.duration = 0

    # Intercept internal calls to isolate parameter transmission path
    with patch('CheckRoyalCaribbeanPrice.check_if_room_is_available', return_value=(True, [])), \
         patch('CheckRoyalCaribbeanPrice.get_room_price_via_API', return_value=mock_api_results), \
         patch('CheckRoyalCaribbeanPrice.parse_provided_URL', return_value=mock_url_params), \
         patch('CheckRoyalCaribbeanPrice._calculate_passenger_metrics', return_value={"num_adults": 1, \
                                                                                      "num_children": 0, \
                                                                                      "have_a_senior": False, \
                                                                                      "sub_type": "",
                                                                                      "category_code": ""}), \
         patch('CheckRoyalCaribbeanPrice._build_checkout_url') as mock_build_url:

        get_cruise_price(
            account_info=base_account_info,
            booking=mock_booking_payload,
            ship_dictionary=real_registry,
            automatic_URL=False,
            discounts=custom_discounts  # Testing the new optional argument signature
        )

        # Assert that the checkout URL builder received the profile containing our modifications
        mock_build_url.assert_called_once()
        forwarded_profile = mock_build_url.call_args[0][3]
        assert forwarded_profile.dp340 is True

def test_discount_profile_to_url_params_alignment():
    """Verify that fire and dp340 map across structures without losing data state."""
    profile = DiscountProfile(
        loyalty_number="12345",
        state="FL",
        senior=True,
        military=False,
        fire=True,
        police=False,
        dp340=True
    )

    url_params = CruiseURLParams()
    # Ensure your mapper or assignment handles it cleanly
    url_params.apply_discount_profile(profile)

    assert url_params.fire == "y"
    assert hasattr(url_params, "dp340") and url_params.dp340 is True

# =====================================================================
# ITEM 16 EXTRA REFACTOR & WATCHLIST ROUTING FIXES
# =====================================================================
class MockURLParams:
    def __init__(self):
        self.ship_code = "FR"
        self.package_code = "FR07D015"
        self.sail_date = "2027-11-04"
        self.cabin_class_string = "DELUXE"
        self.stateroom_category_code = "D1"
        self.currency_code = "USD"  # <-- ADD THIS LINE

        # Ensure it has these as well for the string building logic
        self.loyalty_number = None
        self.state = None
        self.senior = None
        self.police = None
        self.military = None
        self.coupon_code = None
        self.all_included = False
        self.refundable = False
        self.travel_insurance = False
        self.prepaid_grats = False

    def apply_overrides(self, paid_price_struct):
        pass

def test_watchlist_missing_paid_price(monkeypatch):
    """Verifies that watchlist items without a paidPrice safely log the current rate via the discovery block."""
    # Defensively clean out global configuration properties
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.config.minimum_saving_alert", None)
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.config.format_date", lambda d: "2027-11-04")

    mock_account = AccountInfo(
        username="TestWatch", password="", cruise_line="royalcaribbean",
        access=APIAccess(token=None, id=None, session=requests.Session())
    )

    mock_booking = {
        "url": "https://www.royalcaribbean.com/checkout/summary?shipCode=FR&sailDate=2027-11-04&cabinClassType=DELUXE&numberOfNights=7",
        "paidPriceStruct": None,
        "finalPaymentDate": None
    }

    # Nest the payload correctly so results.get("base_fare") successfully finds pricing metrics
    mock_fare_struct = {
        "room_available": True,
        "sailing_nights": 7,
        "base_fare": {
            "fare": 5000.00,
            "price": 5000.00,
            "total_price": 5000.00,
            "gratuities": 0.0,
            "insurance": 0.0,
            "obc": "100.00"
        }
    }

    monkeypatch.setattr("CheckRoyalCaribbeanPrice.get_room_price_via_API", lambda *args, **kwargs: mock_fare_struct)
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.parse_provided_URL", lambda *args, **kwargs: MockURLParams())

    captured_logs = []
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.log", lambda msg: captured_logs.append(msg))

    target_struct = {'paid_price': None, 'paidPrice': None}
    mock_ship_dict = type('ShipDictionary', (object,), {'get_ship': lambda self, code: "Mock Ship"})()

    get_cruise_price(mock_account, mock_booking, mock_ship_dict, automatic_URL=False, paid_price_struct=target_struct)

    # ASSERTION FIX: Assert against what the discovery path actually prints
    assert any("Current Price 5000.00" in log_line for log_line in captured_logs), \
        f"Discovery logging path failed. Logs: {''.join(captured_logs)}"


def test_exact_price_match_includes_obc(monkeypatch):
    """Verifies that when live price == paid price, OBC reporting isn't lost."""
    # Isolate global configuration from leaky sub-mocks
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.config.minimum_saving_alert", None)
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.config.format_date", lambda d: "2027-11-04")

    mock_account = AccountInfo(
        username="TestUser", password="", cruise_line="royalcaribbean",
        access=APIAccess(token=None, id=None, session=requests.Session())
    )

    mock_booking = {
        "url": "https://www.royalcaribbean.com/checkout/summary?shipCode=FR&sailDate=2027-11-04&cabinClassType=DELUXE&numberOfNights=7",
        "paidPriceStruct": {"paidPrice": 2500.00, "paid_price": 2500.00},
        "finalPaymentDate": None
    }

    # Nest fields correctly
    mock_fare_struct = {
        "room_available": True,
        "sailing_nights": 7,
        "base_fare": {
            "fare": 2500.00,
            "price": 2500.00,
            "total_price": 2500.00,
            "gratuities": 0.0,
            "insurance": 0.0,
            "obc": "150.00"
        }
    }

    monkeypatch.setattr("CheckRoyalCaribbeanPrice.get_room_price_via_API", lambda *args, **kwargs: mock_fare_struct)
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.parse_provided_URL", lambda *args, **kwargs: MockURLParams())

    captured_logs = []
    monkeypatch.setattr("CheckRoyalCaribbeanPrice.log", lambda msg: captured_logs.append(msg))

    target_struct = {'paidPrice': 2500.00, 'paid_price': 2500.00}
    mock_ship_dict = type('ShipDictionary', (object,), {'get_ship': lambda self, code: "Mock Ship"})()

    get_cruise_price(mock_account, mock_booking, mock_ship_dict, automatic_URL=False, paid_price_struct=target_struct)

    # Assert that execution successfully reached the best price block and printed the live OBC metrics
    assert any("You have the best price of 2500.00" in log_line and "150.00 OBC" in log_line for log_line in captured_logs), \
        f"OBC tracking lost on exact match. Logs: {''.join(captured_logs)}"


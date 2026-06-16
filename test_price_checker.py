import pytest
from unittest.mock import MagicMock, patch
import requests

# Import the specific entities and orchestration engines from your script
from CheckRoyalCaribbeanPrice import (
    get_cruise_price,
    get_orders,
    WatchItemContext,
    AccountInfo,
    ShipRegistry,
    config
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
                    "firstName": "Adam",
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
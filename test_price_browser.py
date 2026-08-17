import json
import pytest
import requests
import unittest

from unittest.mock import patch, MagicMock

# Import targets from module
from BrowseRoyalCaribbeanPrice import (
    _execute_api_request,
    get_all_activities_web,
    get_cruise_price_from_API,
    get_MDR_locations,
    get_products_graph_all_pages,
    get_ships_web,
    get_sailing_details_web,
    get_sailings_web,
    get_web_categories,
    main,
    print_all_products,
    IMPERSONATE_ARGS
)


# ==============================================================================
# FIXTURES & GLOBAL AUTO-MOCK SETUP
# ==============================================================================

@pytest.fixture(autouse=True)
def mock_global_dependencies():
    """
    Intercept module-level stdout logging calls across all tests
    to prevent un-bound custom method crashes.
    """
    with patch('BrowseRoyalCaribbeanPrice.log', MagicMock()) as mock_log:
        yield mock_log


@pytest.fixture
def base_headers():
    return {
        'User-Agent': 'TestAgent/1.0',
        'Accept': 'application/json',
        'appkey': 'test_secret_key'
    }


@pytest.fixture
def mock_response_success():
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ==============================================================================
# UNIT TESTS: EXECUTE API REQUEST
# ==============================================================================
class TestExecuteApiRequest(unittest.TestCase):

    @patch('BrowseRoyalCaribbeanPrice.requests.Session')
    def test_execute_request_success(self, mock_session_cls):
        mock_session_inst = MagicMock()
        mock_session_cls.return_value = mock_session_inst

        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_session_inst.request.return_value = mock_response

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint")

        self.assertIsNotNone(response)
        self.assertEqual(mock_session_inst.request.call_count, 1)


    @patch('BrowseRoyalCaribbeanPrice.time.sleep', return_value=None)
    @patch('BrowseRoyalCaribbeanPrice.requests.Session')
    def test_execute_request_non_critical_failure_returns_none(self, mock_session_cls, mock_sleep):
        mock_session_inst = MagicMock()
        mock_session_cls.return_value = mock_session_inst
        mock_session_inst.request.side_effect = requests.exceptions.ConnectionError("Connection reset")

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint", on_failure="retry")

        self.assertIsNone(response)
        self.assertEqual(mock_session_inst.request.call_count, 3)


    @patch('BrowseRoyalCaribbeanPrice.time.sleep', return_value=None)
    @patch('BrowseRoyalCaribbeanPrice.requests.Session')
    def test_execute_request_recovers_after_transient_server_error(self, mock_session_cls, mock_sleep):
        mock_session_inst = MagicMock()
        mock_session_cls.return_value = mock_session_inst

        bad_response = MagicMock(spec=requests.Response)
        bad_response.status_code = 503

        good_response = MagicMock(spec=requests.Response)
        good_response.status_code = 200

        mock_session_inst.request.side_effect = [bad_response, good_response]

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint", on_failure="retry")

        self.assertEqual(response, good_response)
        self.assertEqual(mock_session_inst.request.call_count, 2)


    @patch('BrowseRoyalCaribbeanPrice.requests.Session')
    def test_execute_request_does_not_retry_definitive_http_errors(self, mock_session_cls):
        mock_session_inst = MagicMock()
        mock_session_cls.return_value = mock_session_inst

        not_found_response = MagicMock(spec=requests.Response)
        not_found_response.status_code = 404

        http_error = requests.exceptions.HTTPError("404 Client Error", response=not_found_response)
        not_found_response.raise_for_status.side_effect = http_error

        mock_session_inst.request.return_value = not_found_response

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint", on_failure="retry")

        self.assertIsNone(response)
        self.assertEqual(mock_session_inst.request.call_count, 1)


# ==============================================================================
# FLEET & SAILING DISCOVERY TESTS
# ==============================================================================
class TestFleetAndSailingParsers:

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_ships_web_handles_empty_payload_gracefully(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"payload": None}
        mock_execute.return_value = mock_resp

        ships = get_ships_web()
        assert ships == []


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_ships_web_normalizes_allcaps_names(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "payload": {
                "ships": [
                    {"shipCode": "AL", "name": "Allure of the Seas"},
                    {"shipCode": "HE", "name": "HERO OF THE SEAS"}
                ]
            }
        }
        mock_execute.return_value = mock_resp

        ships = get_ships_web()

        assert len(ships) == 2
        assert ships[0] == {'code': 'AL', 'name': 'Allure of the Seas'}
        assert ships[1] == {'code': 'HE', 'name': 'Hero of the Seas'}


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailings_web_valid_dates(self, mock_execute):
        """Validates exact return count and item properties from get_sailings_web."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "payload": {
                "voyages": [
                    {
                        "shipCode": "AL",
                        "sailDate": "20261115",
                        "duration": 7,
                        "voyageCode": "AL07W001",
                        "voyageDescription": "7 Night Eastern Caribbean"
                    },
                    {
                        "shipCode": "AL",
                        "sailDate": "20261122",
                        "duration": 7,
                        "voyageCode": "AL07W002",
                        "voyageDescription": "7 Night Western Caribbean"
                    }
                ]
            }
        }
        mock_execute.return_value = mock_resp

        sailings = get_sailings_web("AL")

        # Strict checks against normalized output dict
        assert len(sailings) == 2
        assert sailings[0]["date"] == "20261115"
        assert sailings[0]["duration"] == 7
        assert sailings[0]["voyageCode"] == "AL07W001"
        assert sailings[1]["date"] == "20261122"


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailings_web_resilient_to_malformed_json(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.side_effect = ValueError("Corrupted JSON body text string")
        mock_execute.return_value = mock_resp

        sailings = get_sailings_web("AL")
        assert sailings == []


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailings_web_missing_payload(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"payload": None}
        mock_execute.return_value = mock_resp

        sailings = get_sailings_web("AL")
        assert sailings == []


# ==============================================================================
# SAILING ITINERARY & WEB DETAILS TESTS
# ==============================================================================
class TestSailingDetailsWeb:

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailing_details_web_handles_empty_or_failed_responses(self, mock_execute):
        # 1. API request failure (None)
        mock_execute.return_value = None
        assert get_sailing_details_web("AL", "20261115") == {}

        # 2. JSON parsing exception
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("Malformed JSON")
        mock_execute.return_value = mock_resp
        assert get_sailing_details_web("AL", "20261115") == {}


    @patch('BrowseRoyalCaribbeanPrice.log')
    @patch('BrowseRoyalCaribbeanPrice.sanitize_string', side_effect=lambda s: s)
    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailing_details_web_full_itinerary_branches(self, mock_execute, mock_sanitize, mock_log):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "payload": {
                "sailingInfo": [
                    {
                        "itinerary": {
                            "events": [
                                {
                                    # Day 1: Embarkation port
                                    "day": 1,
                                    "port": {
                                        "portName": "Port Canaveral",
                                        "portType": "EMBARK",
                                        "arrivalDateTime": "20261115T060000",
                                        "departureDateTime": "20261115T163000"
                                    }
                                },
                                {
                                    # Day 2: Cruising day
                                    "day": 2,
                                    "port": {
                                        "portName": "Cruising",
                                        "portType": "SEA",
                                        "arrivalDateTime": "20261116T000000",
                                        "departureDateTime": "20261116T235959"
                                    }
                                },
                                {
                                    # Day 3: Port call + Tendered
                                    "day": 3,
                                    "port": {
                                        "portName": "CocoCay",
                                        "portType": "TENDERED",
                                        "arrivalDateTime": "20261117T080000",
                                        "departureDateTime": "20261117T170000"
                                    }
                                },
                                {
                                    # Day 4: Missing arrivalDateTime (hits early loop continue)
                                    "day": 4,
                                    "port": {
                                        "portName": "At Sea",
                                        "portType": "SEA",
                                        "arrivalDateTime": None
                                    }
                                },
                                {
                                    # Day 5: Debarkation port
                                    "day": 5,
                                    "port": {
                                        "portName": "Port Canaveral",
                                        "portType": "DEBARK",
                                        "arrivalDateTime": "20261119T060000",
                                        "departureDateTime": "20261119T080000"
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
        mock_execute.return_value = mock_resp

        ports = get_sailing_details_web("AL", "20261115")

        assert ports == {
            1: "Port Canaveral",
            2: "Cruising",
            3: "CocoCay",
            4: "At Sea",
            5: "Port Canaveral"
        }
        assert mock_log.called


# ==============================================================================
# COMMERCE CATALOG & GRAPHQL TESTS
# ==============================================================================
class TestCommerceCatalogGraphQL:

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_web_categories_handles_empty_graphql_container(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"data": {"categories": None}}
        mock_execute.return_value = mock_resp

        categories = get_web_categories("AL", "20261115")
        assert categories == {}


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_web_categories_success(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "data": {
                "categories": {
                    "categories": [
                        {"id": "BEVERAGE", "name": "Beverage Packages"},
                        {"id": "DINING", "name": "Specialty Dining"}
                    ]
                }
            }
        }
        mock_execute.return_value = mock_resp

        categories = get_web_categories("AL", "20261115")
        assert categories == {"BEVERAGE": "Beverage Packages", "DINING": "Specialty Dining"}


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_web_categories_exception_returns_empty(self, mock_execute):
        mock_execute.return_value = None

        categories = get_web_categories("AL", "20261115")
        assert categories == {}


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_products_graph_pagination_exhaustion(self, mock_execute):
        mock_page_1 = MagicMock(spec=requests.Response)
        mock_page_1.json.return_value = {
            "data": {
                "products": {
                    "commerceProducts": [{"id": "PROD_123", "title": "Deluxe Beverage Package"}]
                }
            }
        }

        mock_terminal_page = MagicMock(spec=requests.Response)
        mock_terminal_page.json.return_value = {
            "data": {
                "products": {
                    "commerceProducts": []
                }
            }
        }

        mock_execute.side_effect = [mock_page_1, mock_terminal_page]

        products = get_products_graph_all_pages(
            ship_code="AL",
            sail_date="20261115",
            duration=7,
            currency="USD",
            sortkey="price",
            sortorder="asc",
            key="beverage"
        )

        assert len(products) == 1
        assert products[0]["id"] == "PROD_123"
        assert mock_execute.call_count == 2


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_products_graph_handles_none_response(self, mock_execute):
        mock_execute.return_value = None

        products = get_products_graph_all_pages(
            ship_code="AL",
            sail_date="20261115",
            duration=7,
            currency="USD",
            sortkey="price",
            sortorder="asc",
            key="beverage"
        )
        assert products == []


    @patch('BrowseRoyalCaribbeanPrice.print_and_sort_products')
    @patch('BrowseRoyalCaribbeanPrice.get_products_graph_all_pages')
    @patch('BrowseRoyalCaribbeanPrice.get_web_categories')
    def test_print_all_products_orchestration(self, mock_get_cats, mock_get_prods, mock_print_sort):
        mock_get_cats.return_value = {
            "shorex": "Shore Excursions",
            "dining": "Dining & Packages"
        }

        def product_side_effect(*args, **kwargs):
            key = args[6]
            day_param = args[7]
            if key == "shorex" and day_param == "1":
                return [{"id": "EXC_1", "name": "Snorkel Tour"}]
            if key == "dining" and day_param == "all":
                return [{"id": "DIN_1", "name": "Chops Grille"}]
            return []

        mock_get_prods.side_effect = product_side_effect

        ports = {1: "CocoCay", 2: "Nassau"}

        print_all_products(
            ship_code="AL",
            sail_date="20261115",
            duration=3,
            currency="USD",
            sort_key="price",
            sort_order="asc",
            show_watchlist_codes=False,
            ports=ports
        )

        assert mock_get_prods.call_count >= 5  # Ran 1..5 for shorex + 1 for dining
        assert mock_print_sort.call_count == 2


# ==============================================================================
# CRUISE PRICE BRAND ROUTING & ROOM SELECTION
# ==============================================================================
class TestCruisePriceBrandRouting:

    @staticmethod
    def _room_selection_text(cheap=1503.43, expensive=2200.00):
        return json.dumps({"rooms": [{"options": {"stateroomTypes": [
            {"name": "Interior", "stateroomSubtypes": [
                {"code": "ZI", "categoryCode": "ZI",
                 "pricing": {"invoice": {"total": expensive}}},
                {"code": "V4", "categoryCode": "V4",
                 "pricing": {"invoice": {"total": cheap}}},
            ]}
        ]}}]})


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_celebrity_routes_to_celebrity_host(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = self._room_selection_text()
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "EG12K185", "20270102", 2, 0, is_royal=False)

        called_url = mock_exec.call_args[1]["url"]
        assert "celebritycruises.com" in called_url
        assert "room-selection/type-and-subtype" in called_url


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_royal_routes_to_royal_host(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = self._room_selection_text()
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "IC07E484", "20270102", 2, 0, is_royal=True)

        assert "royalcaribbean.com" in mock_exec.call_args[1]["url"]


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_reports_cheapest_subtype_total_per_class(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = self._room_selection_text(cheap=1503.43, expensive=2200.00)
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "IC07E484", "20270102", 2, 0, is_royal=True)

        logged = "\n".join(str(c[0][0]) for c in mock_global_dependencies.call_args_list)
        assert "1503.43" in logged
        assert "2200.0" not in logged


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_no_rooms_reports_sold_out(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = "no rooms payload here"
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "EG99Z999", "20200101", 2, 0, is_royal=False)

        logged = "\n".join(str(c[0][0]) for c in mock_global_dependencies.call_args_list)
        assert "Sailing is sold out" in logged


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_pricing_ignores_invalid_json_body(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = "Internal Server Error / HTML page"
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "IC07E484", "20270102", 2, 0, is_royal=True)

        logged = "\n".join(str(c[0][0]) for c in mock_global_dependencies.call_args_list)
        assert "Sailing is sold out" in logged


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_pricing_handles_empty_rooms_list(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"rooms": []})
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("USD", "IC07E484", "20270102", 2, 0, is_royal=True)

        logged = "\n".join(str(c[0][0]) for c in mock_global_dependencies.call_args_list)
        assert "Sailing is sold out" in logged


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_pricing_handles_different_currencies(self, mock_exec, mock_global_dependencies):
        mock_resp = MagicMock()
        mock_resp.text = self._room_selection_text(cheap=1200.00, expensive=1800.00)
        mock_exec.return_value = mock_resp

        get_cruise_price_from_API("GBP", "IC07E484", "20270102", 2, 0, is_royal=True)

        logged = "\n".join(str(c[0][0]) for c in mock_global_dependencies.call_args_list)
        assert "1200.0" in logged


# ==============================================================================
# ADDITIONAL DEEP COVERAGE TESTS FOR UNCOVERED BRANCHES
# ==============================================================================
class TestDeepModuleCoverage:

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailings_web_fallback_branches(self, mock_execute):
        # Hits lines 528-538 by passing 'voyages' at top-level
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "voyages": [{"sailingId": "AL07E010", "sailDate": "20261115"}]
        }
        mock_execute.return_value = mock_resp

        res = get_sailings_web("AL")
        assert isinstance(res, list)


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_web_categories_non_dict_data(self, mock_execute):
        """Hits lines 707-709 empty categories handling safely."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"data": None}
        mock_execute.return_value = mock_resp

        cats = get_web_categories("AL", "20261115")
        assert cats == {}


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_products_graph_all_pages_error_payload(self, mock_execute):
        # Hits lines 757-760 GraphQL error response handling
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"errors": [{"message": "Validation error"}]}
        mock_execute.return_value = mock_resp

        products = get_products_graph_all_pages("AL", "20261115", 7, "USD", "price", "asc", "beverage")
        assert products == []


# ==============================================================================
# DEEP COVERAGE SUITE FOR BrowseRoyalCaribbeanPrice.py
# ==============================================================================

class TestBrowseCoverageExpansionTargeted:

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_products_graph_all_pages_pagination(self, mock_execute):
        """Simulates multi-page pagination through WebProductsByCategory GraphQL API."""

        # Page 1 response payload matching data -> products -> commerceProducts
        mock_resp_page1 = MagicMock(spec=requests.Response)
        mock_resp_page1.json.return_value = {
            "data": {
                "products": {
                    "commerceProducts": [
                        {
                            "id": "BEV1",
                            "title": "Deluxe Beverage Package",
                            "variantOptions": [],
                            "price": {
                                "currency": "USD",
                                "promotionalPrice": 79.99,
                                "shipboardPrice": 98.00,
                                "formattedPromotionalPrice": "$79.99",
                                "formattedBasePrice": "$98.00",
                                "formattedDailyPrice": "$79.99/day",
                                "formattedPromoDailyPrice": "$79.99/day",
                                "salesUnit": {"code": "PER_DAY", "name": "Per Day", "label": "Per Day"}
                            }
                        }
                    ]
                }
            }
        }

        # Page 2 response payload
        mock_resp_page2 = MagicMock(spec=requests.Response)
        mock_resp_page2.json.return_value = {
            "data": {
                "products": {
                    "commerceProducts": [
                        {
                            "id": "BEV2",
                            "title": "Refreshment Package",
                            "variantOptions": [],
                            "price": {
                                "currency": "USD",
                                "promotionalPrice": 29.99,
                                "shipboardPrice": 38.00,
                                "formattedPromotionalPrice": "$29.99",
                                "formattedBasePrice": "$38.00",
                                "formattedDailyPrice": "$29.99/day",
                                "formattedPromoDailyPrice": "$29.99/day",
                                "salesUnit": {"code": "PER_DAY", "name": "Per Day", "label": "Per Day"}
                            }
                        }
                    ]
                }
            }
        }

        # Page 3 terminal response payload (empty list terminates pagination)
        mock_resp_page3 = MagicMock(spec=requests.Response)
        mock_resp_page3.json.return_value = {
            "data": {
                "products": {
                    "commerceProducts": []
                }
            }
        }

        mock_execute.side_effect = [mock_resp_page1, mock_resp_page2, mock_resp_page3]

        products = get_products_graph_all_pages(
            ship_code="AL",
            sail_date="20261115",
            duration=7,
            currency="USD",
            sortkey="price",
            sortorder="asc",
            key="beverage",
            day_number="all"
        )

        assert len(products) == 2
        assert products[0]["id"] == "BEV1"
        assert products[1]["id"] == "BEV2"
        assert mock_execute.call_count == 3


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_all_activities_web_parsing(self, mock_execute):
        """Hits lines 1048-1135 (non-revenue schedulable products parser)."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "payload": {
                "products": [
                    {
                        "productType": {"productType": "NON_REVENUE_SCHEDULABLE"},
                        "productTitle": "Laser Tag",
                        "productLocation": {"locationTitle": "Studio B"},
                        "offering": [
                            {"offeringDate": "20261116", "offeringTime": "10:00 AM"}
                        ]
                    }
                ]
            }
        }
        mock_execute.return_value = mock_resp

        activities = get_all_activities_web("AL", "20261115")
        assert len(activities) == 1
        assert activities[0]["productTitle"] == "Laser Tag"


    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_MDR_locations_royal_and_celebrity(self, mock_execute):
        """Hits lines 1349-1459 (GraphQL main dining venue extraction logic)."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "data": {
                "productsByVenueCategories": {
                    "venueCategories": [
                        {
                            "venueSubCategories": [
                                {
                                    "venues": [
                                        {"id": "V101", "title": "Main Dining Room Deck 3"},
                                        {"id": "V102", "title": "My Time Dining"}
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        }
        mock_execute.return_value = mock_resp

        # Test Royal Caribbean early return logic (returns first non-'My Time' Main Dining Room)
        royal_venues = get_MDR_locations("AL", "20261115", is_royal=True)
        assert royal_venues == ["V101"]

        # Test Celebrity multi-venue retention logic
        celebrity_venues = get_MDR_locations("EG", "20261115", is_royal=False)
        assert len(celebrity_venues) == 2


class TestMainCLIOrchestration:

    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    def test_main_invalid_ship_name_returns_early(self, mock_log, mock_get_ships, mock_logging):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]

        # Invoke main with a ship that doesn't match
        main(['--ship', 'UnknownShip'])

        mock_log.assert_any_call("I can't find that ship name UnknownShip; please try again")


    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.get_sailings_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    def test_main_invalid_saildate_returns_early(self, mock_log, mock_get_sailings, mock_get_ships, mock_logging):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]
        mock_get_sailings.return_value = [{'displayDate': '11/15/26 7 Night', 'description': 'Eastern Caribbean'}]

        main(['--ship', 'Allure', '--saildate', '12/25/99'])

        mock_log.assert_any_call("I can't find that sail date 12/25/99 for Allure of the Seas; please try again")


    @patch('BrowseRoyalCaribbeanPrice.sys.stdin.isatty', return_value=False)
    @patch('BrowseRoyalCaribbeanPrice.print_MDR_menus')
    @patch('BrowseRoyalCaribbeanPrice.get_MDR_locations', return_value=['MDR1'])
    @patch('BrowseRoyalCaribbeanPrice.print_all_activities')
    @patch('BrowseRoyalCaribbeanPrice.get_all_activities_web', return_value=[])
    @patch('BrowseRoyalCaribbeanPrice.print_all_products')
    @patch('BrowseRoyalCaribbeanPrice.get_cruise_price_from_API')
    @patch('BrowseRoyalCaribbeanPrice.get_sailing_details_web', return_value={1: 'Port Canaveral'})
    @patch('BrowseRoyalCaribbeanPrice.get_sailings_web')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.get_system_currency', return_value='USD')
    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    def test_main_successful_end_to_end_cli_run(
        self, mock_setup_log, mock_currency, mock_get_ships, mock_get_sailings,
        mock_get_details, mock_get_price, mock_print_prods, mock_get_acts,
        mock_print_acts, mock_get_mdr, mock_print_mdr, mock_isatty
    ):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]
        mock_get_sailings.return_value = [{
            'displayDate': '11/15/26',
            'date': '20261115',
            'voyageCode': 'AL07W001',
            'description': '7 Night Eastern Caribbean',
            'duration': 7
        }]

        main(['--ship', 'Allure', '--saildate', '11/15/26', '--currency', 'System'])

        mock_get_details.assert_called_once_with('AL', '20261115')
        mock_print_prods.assert_called_once()
        mock_get_acts.assert_called_once_with('AL', '20261115')
        mock_print_mdr.assert_called_once()


class TestBrowseInteractiveAndBrandEdgeCases:

    # -------------------------------------------------------------------------
    # 1. Ship Selection Edge Cases (No --ship flag passed)
    # -------------------------------------------------------------------------
    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    @patch('builtins.input', return_value='99')
    def test_interactive_ship_selection_out_of_bounds(self, mock_input, mock_log, mock_get_ships, mock_logging):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]

        main([])  # No CLI args, forces interactive menu

        mock_log.assert_any_call("Invalid ship selection")


    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    @patch('builtins.input', return_value='not_a_number')
    def test_interactive_ship_selection_non_numeric(self, mock_input, mock_log, mock_get_ships, mock_logging):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]

        main([])

        mock_log.assert_any_call("Invalid ship selection")


    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    @patch('builtins.input', return_value='q')
    def test_interactive_ship_selection_quit(self, mock_input, mock_log, mock_get_ships, mock_logging):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]

        main([])

        mock_log.assert_any_call("Have a nice day!")


    # -------------------------------------------------------------------------
    # 2. Sailing Selection Edge Cases (Ship matched via CLI, interactive sailing)
    # -------------------------------------------------------------------------
    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.get_sailings_web')
    @patch('BrowseRoyalCaribbeanPrice.log')
    @patch('builtins.input', return_value='55')
    def test_interactive_sailing_selection_out_of_bounds(
        self, mock_input, mock_log, mock_get_sailings, mock_get_ships, mock_logging
    ):
        mock_get_ships.return_value = [{'name': 'Allure of the Seas', 'code': 'AL'}]
        mock_get_sailings.return_value = [{'displayDate': '11/15/26', 'description': 'Eastern Caribbean'}]

        main(['--ship', 'Allure'])

        mock_log.assert_any_call("Invalid sailing selection")


    # -------------------------------------------------------------------------
    # 3. Celebrity Brand Routing Logic
    # -------------------------------------------------------------------------
    @patch('BrowseRoyalCaribbeanPrice.sys.stdin.isatty', return_value=False)
    @patch('BrowseRoyalCaribbeanPrice.print_MDR_menus')
    @patch('BrowseRoyalCaribbeanPrice.get_MDR_locations', return_value=['MDR1'])
    @patch('BrowseRoyalCaribbeanPrice.print_all_activities')
    @patch('BrowseRoyalCaribbeanPrice.get_all_activities_web', return_value=[])
    @patch('BrowseRoyalCaribbeanPrice.print_all_products')
    @patch('BrowseRoyalCaribbeanPrice.get_cruise_price_from_API')
    @patch('BrowseRoyalCaribbeanPrice.get_sailing_details_web', return_value={})
    @patch('BrowseRoyalCaribbeanPrice.get_sailings_web')
    @patch('BrowseRoyalCaribbeanPrice.get_ships_web')
    @patch('BrowseRoyalCaribbeanPrice.get_system_currency', return_value='USD')
    @patch('BrowseRoyalCaribbeanPrice.setup_hybrid_logging')
    @patch('BrowseRoyalCaribbeanPrice.log')
    def test_celebrity_ship_brand_routing(
        self, mock_log, mock_setup_log, mock_currency, mock_get_ships,
        mock_get_sailings, mock_get_details, mock_get_price, mock_print_prods,
        mock_get_acts, mock_print_acts, mock_get_mdr, mock_print_mdr, mock_isatty
    ):
        # Celebrity ship naming structure
        mock_get_ships.return_value = [{'name': 'Celebrity Apex', 'code': 'AP'}]
        mock_get_sailings.return_value = [{
            'displayDate': '11/15/26',
            'date': '20261115',
            'voyageCode': 'AP07W001',
            'description': '7 Night Caribbean',
            'duration': 7
        }]

        main(['--ship', 'Apex', '--saildate', '11/15/26'])

        # Verify Celebrity Cruise Planner URL root was generated
        mock_log.assert_any_call("Direct Link To Celebrity Cruise Planner Website: ")

        # Verify is_royal=False passed into API pricing and MDR locations
        mock_get_price.assert_called_once_with('USD', 'APAP07W001', '20261115', 2, 0, False)
        mock_get_mdr.assert_called_once_with('AP', '20261115', False)

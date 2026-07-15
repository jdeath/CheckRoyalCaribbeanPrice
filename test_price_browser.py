import pytest
from unittest.mock import patch, MagicMock
import requests

# Import the targets from your script module
from BrowseRoyalCaribbeanPrice import (
    _execute_api_request,
    get_ships_web,
    get_sailings_web,
    get_web_categories,
    get_products_graph_all_pages
)

# ==============================================================================
# FIXTURES & GLOBAL AUTO-MOCK SETUP
# ==============================================================================

@pytest.fixture(autouse=True)
def mock_global_dependencies():
    """
    Automatically intercept module-level stdout logging calls across all tests
    to prevent un-bound custom method crashes (TypeError: NoneType).
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
# UNIT TESTS
# ==============================================================================

class TestExecuteApiRequest:
    """Validates the centralized HTTP transport core."""

    @patch('BrowseRoyalCaribbeanPrice.requests.request')
    def test_execute_request_success(self, mock_request, mock_response_success):
        mock_response_success.json.return_value = {"status": "ok"}
        mock_request.return_value = mock_response_success

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint")

        assert response is not None
        assert response.status_code == 200
        mock_request.assert_called_once_with(
            method="GET",
            url="https://api.test.com/v1/endpoint",
            params=None,
            data=None,
            json=None,
            headers={"appkey": "hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm"},  # Defaults to Web key
            timeout=15
        )

    @patch('BrowseRoyalCaribbeanPrice.time.sleep', return_value=None)
    @patch('BrowseRoyalCaribbeanPrice.requests.request')
    @patch('BrowseRoyalCaribbeanPrice.sys.exit')
    def test_execute_request_critical_failure_exits(self, mock_exit, mock_request, mock_sleep):
        """Ensures critical network failures trigger graceful application exits after retries."""
        mock_request.side_effect = requests.exceptions.ConnectionError("Connection timed out")

        _execute_api_request("GET", "https://api.test.com/v1/endpoint", exit_on_fail=True)

        mock_exit.assert_called_once_with(1)
        assert mock_request.call_count == 3  # Connection errors are retried before exiting

    @patch('BrowseRoyalCaribbeanPrice.time.sleep', return_value=None)
    @patch('BrowseRoyalCaribbeanPrice.requests.request')
    def test_execute_request_non_critical_failure_returns_none(self, mock_request, mock_sleep):
        """Ensures soft page bounds pass exit exemptions smoothly."""
        mock_request.side_effect = requests.exceptions.ConnectionError("Connection reset")

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint", exit_on_fail=False)

        assert response is None
        assert mock_request.call_count == 3  # Retried before giving up

    @patch('BrowseRoyalCaribbeanPrice.time.sleep', return_value=None)
    @patch('BrowseRoyalCaribbeanPrice.requests.request')
    def test_execute_request_recovers_after_transient_server_error(self, mock_request, mock_sleep, mock_response_success):
        """A 503 blip mid-session recovers on the next attempt instead of aborting."""
        bad_response = MagicMock(spec=requests.Response)
        bad_response.status_code = 503
        mock_request.side_effect = [bad_response, mock_response_success]

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint")

        assert response is mock_response_success
        assert mock_request.call_count == 2

    @patch('BrowseRoyalCaribbeanPrice.requests.request')
    def test_execute_request_does_not_retry_definitive_http_errors(self, mock_request):
        """A 404 is a terminal pagination answer, not a blip - no retries, no backoff delay."""
        not_found = MagicMock(spec=requests.Response)
        not_found.status_code = 404
        not_found.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_request.return_value = not_found

        response = _execute_api_request("GET", "https://api.test.com/v1/endpoint", exit_on_fail=False)

        assert response is None
        assert mock_request.call_count == 1


class TestWebFleetAndSailingDiscovery:
    """Verifies core gateway discovery routes handle API variations."""

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_ships_web_handles_empty_payload_gracefully(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"payload": None}  # API variations!
        mock_execute.return_value = mock_resp

        ships = get_ships_web()
        assert ships == []

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_ships_web_injects_hero_of_the_seas(self, mock_execute):
        """Verifies custom staging code mapping rule logic works correctly."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {
            "payload": {
                "ships": [
                    {"shipCode": "AL", "name": "Allure of the Seas"},
                    {"shipCode": "HM", "name": "Icon Variant Staging"}
                ]
            }
        }
        mock_execute.return_value = mock_resp

        ships = get_ships_web()

        # Verify normal code parsing combined with the special 'HE' payload insertion
        assert len(ships) == 3
        assert ships[0] == {'code': 'AL', 'name': 'Allure of the Seas'}
        assert ships[2] == {'code': 'HE', 'name': 'Hero of the Seas'}

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_sailings_web_resilient_to_malformed_json(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.side_effect = ValueError("Corrupted JSON body text string")
        mock_execute.return_value = mock_resp

        sailings = get_sailings_web("AL")
        assert sailings == []


class TestCommerceCatalogGraphQL:
    """Tests the GraphQL extraction layers and pagination boundary mechanisms."""

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_web_categories_handles_empty_graphql_container(self, mock_execute):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.json.return_value = {"data": {"categories": None}}
        mock_execute.return_value = mock_resp

        categories = get_web_categories("AL", "20261115")
        assert categories == {}

    @patch('BrowseRoyalCaribbeanPrice._execute_api_request')
    def test_get_products_graph_pagination_exhaustion(self, mock_execute):
        """
        Scenario: Loop 1 returns products, subsequent checks encounter an empty array boundary.
        Expectation: Extends list cleanly, encounters terminal loop boundary conditions, and stops.
        """
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
                    "commerceProducts": []  # Empty container terminal loop condition
                }
            }
        }

        # A generator function that satisfies infinite execution requests safely
        def dynamic_response_generator():
            yield mock_page_1
            while True:
                yield mock_terminal_page

        mock_execute.side_effect = dynamic_response_generator()

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
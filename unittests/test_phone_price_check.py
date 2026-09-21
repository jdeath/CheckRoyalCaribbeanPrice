"""PhonePriceCheck.py: login encoding/decoding, order-parsing robustness,
brand-parameterized API paths, network helper resilience, and price auditing."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from PhonePriceCheck import (
    AuditItem,
    AuthSession,
    _execute_api_request,
    check_item_catalog_price,
    get_ship_dictionary,
    login,
    process_orders,
)


def _token_with(payload: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "hdr." + seg + ".sig"


# =============================================================================
# 1. Login & Auth Tests
# =============================================================================

def test_login_encodes_username_and_decodes_urlsafe_token():
    """A raw '+' in the username decodes server-side as a space (login fails
    as the wrong user), and standard b64decode silently corrupts base64URL
    JWT payloads containing -/_ bytes."""
    captured = {}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": _token_with({"sub": "1234567", "nickname": "J~m~s"})
    }

    def fake_post(url, headers=None, data=None, timeout=15):
        captured["data"] = data
        return resp

    with patch("PhonePriceCheck.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        session_instance.request.side_effect = lambda method, url, **kwargs: (
            fake_post(url, data=kwargs.get("data"))
        )
        mock_session_cls.return_value = session_instance

        auth = login("jim+cruise@example.com", "p&ss w+rd", "royalcaribbean")

    assert "username=jim%2Bcruise%40example.com" in captured["data"]
    assert auth.account_id == "1234567"


def test_login_exits_on_http_error_or_missing_token():
    """Login exits cleanly on HTTP errors without raising an unhandled exception."""
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {"error": "invalid_grant"}

    with patch("PhonePriceCheck.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        session_instance.request.return_value = resp
        mock_session_cls.return_value = session_instance

        with pytest.raises(SystemExit):
            login("user@example.com", "wrongpass", "royalcaribbean")


# =============================================================================
# 2. Network Helper (_execute_api_request) Tests
# =============================================================================

def test_execute_api_request_handles_success_and_errors():
    """Verify _execute_api_request unpacks payload on 200 OK and handles non-200/exceptions safely."""
    session = MagicMock()

    # Case A: Success 200 OK with payload dict
    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"payload": {"data": "test_val"}}
    session.request.return_value = resp_ok

    res = _execute_api_request(session, "GET", "https://api.example.com")
    assert res == {"data": "test_val"}

    # Case B: HTTP 502 / HTML Cloudflare error page
    resp_err = MagicMock()
    resp_err.status_code = 502
    session.request.return_value = resp_err

    assert _execute_api_request(session, "GET", "https://api.example.com") is None


# =============================================================================
# 3. Order Processing & Deduplication Tests
# =============================================================================

def test_process_orders_tolerates_missing_order_arrays():
    """Either order array can be null/absent (and an error body has no
    payload at all); the concat used to TypeError and kill the run."""
    auth = AuthSession(
        access_token="tok",
        account_id="acct",
        session=MagicMock(),
    )
    for payload in ({"myOrders": None}, {}, None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"payload": payload} if payload is not None else {}
        auth.session.request.return_value = resp

        process_orders(
            auth=auth,
            api_brand="royal",
            line_name="royalcaribbean",
            reservation_id="1234567",
            passenger_id="PAX1",
            ship="WN",
            start_date="20270510",
            number_of_nights=7,
            curr_override="",
            found_items=set(),
        )


def test_order_history_path_follows_brand():
    """API paths follow the passed api_brand parameter, matching the main
    checker's api_brand mapping."""
    auth = AuthSession(
        access_token="tok",
        account_id="acct",
        session=MagicMock(),
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"payload": {}}
    auth.session.request.return_value = resp

    # Test Royal brand path
    process_orders(
        auth=auth,
        api_brand="royal",
        line_name="royalcaribbean",
        reservation_id="1234567",
        passenger_id="PAX1",
        ship="WN",
        start_date="20270510",
        number_of_nights=7,
        curr_override="",
        found_items=set(),
    )
    called_url = auth.session.request.call_args.kwargs.get("url") or auth.session.request.call_args[1].get("url")
    assert "/en/royal/web/commerce-api/calendar/v1/WN/orderHistory" in called_url

    # Test Celebrity brand path
    process_orders(
        auth=auth,
        api_brand="celebrity",
        line_name="celebritycruises",
        reservation_id="1234567",
        passenger_id="PAX1",
        ship="EG",
        start_date="20270510",
        number_of_nights=7,
        curr_override="",
        found_items=set(),
    )
    called_url = auth.session.request.call_args.kwargs.get("url") or auth.session.request.call_args[1].get("url")
    assert "/en/celebrity/web/commerce-api/calendar/v1/EG/orderHistory" in called_url


# =============================================================================
# 4. Catalog Price Auditing Tests
# =============================================================================

def test_check_item_catalog_price_prints_rebook_alert_on_price_drop(capsys):
    """Verifies that a price drop triggers the RED rebook output banner."""
    auth = AuthSession(
        access_token="tok",
        account_id="acct",
        session=MagicMock(),
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "payload": {
            "title": "Deluxe Beverage Package",
            "startingFromPrice": {
                "adultPromotionalPrice": 65.00,
            },
            "promoDescription": {"displayName": "Flash Sale 30% Off"},
        }
    }
    auth.session.request.return_value = resp

    item = AuditItem(
        reservation_id="12345",
        passenger_id="PAX1",
        passenger_name="John",
        room="8100",
        ship="WN",
        start_date="20270510",
        prefix="BEV",
        product="DELUXE",
        paid_price=85.00,
        currency="USD",
        guest_age_string="adult",
        order_code="ORD123",
        order_date="2026-01-15",
        is_owner=True,
    )

    check_item_catalog_price(auth, "royal", "royalcaribbean", item)

    captured = capsys.readouterr().out
    assert "John: Rebook! Deluxe Beverage Package price dropped to $65.00 (Paid: $85.00)" in captured
    assert "Flash Sale 30% Off" in captured


def test_ship_dictionary_fallback_on_api_failure():
    """Verifies get_ship_dictionary falls back gracefully when API is unreachable."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    session.request.return_value = resp

    ships = get_ship_dictionary(session)
    assert ships == {"HE": "Hero of the Seas"}

"""PhonePriceCheck.py: login encoding/decoding, order-parsing robustness,
and brand-parameterized API paths."""
import base64
import json

from unittest.mock import patch, MagicMock

from PhonePriceCheck import (
    getOrders,
    login
)


def _token_with(payload: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "hdr." + seg + ".sig"


def test_login_encodes_username_and_decodes_urlsafe_token():
    """A raw '+' in the username decodes server-side as a space (login fails
    as the wrong user), and standard b64decode silently corrupts base64URL
    JWT payloads containing -/_ bytes."""
    captured = {}
    resp = MagicMock()
    resp.status_code = 200
    # payload deliberately chosen so its urlsafe encoding contains '-'
    resp.json.return_value = {
        "access_token": _token_with({"sub": "1234567", "nickname": "J~m~s"})}
    session = MagicMock()
    session.post.side_effect = lambda url, headers=None, data=None: (
        captured.__setitem__("data", data) or resp)

    token, account_id, _ = login(
        "jim+cruise@example.com", "p&ss w+rd", session, "royalcaribbean")

    assert "username=jim%2Bcruise%40example.com" in captured["data"]
    assert account_id == "1234567"


def test_get_orders_tolerates_missing_order_arrays():
    """Either order array can be null/absent (and an error body has no
    payload at all); the concat used to TypeError and kill the run."""
    for payload in ({"myOrders": None}, {}, None):
        resp = MagicMock()
        resp.json.return_value = {"payload": payload} if payload is not None else {}
        with patch("PhonePriceCheck.requests.get", return_value=resp):
            getOrders("tok", "acct", MagicMock(), "1234567", "PAX1",
                            "WN", "20270510", 7, None)


def test_order_history_path_follows_brand():
    """API paths were hardcoded to /en/royal/ while login/cancel links used
    cruiseLineName - now both follow the configured brand, matching the main
    checker's api_brand mapping."""
    resp = MagicMock()
    resp.json.return_value = {"payload": {}}
    with patch("PhonePriceCheck.requests.get", return_value=resp) as get:
        getOrders("tok", "acct", MagicMock(), "1234567", "PAX1",
                        "WN", "20270510", 7, None)
    assert "/en/royal/web/commerce-api/calendar/v1/WN/orderHistory" in get.call_args.args[0]

    with patch("PhonePriceCheck.apiBrand", "celebrity"), \
         patch("PhonePriceCheck.requests.get", return_value=resp) as get:
        getOrders("tok", "acct", MagicMock(), "1234567", "PAX1",
                        "EG", "20270510", 7, None)
    assert "/en/celebrity/web/commerce-api/calendar/v1/EG/orderHistory" in get.call_args.args[0]

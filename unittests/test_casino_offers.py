"""
Unit tests for CheckRoyalCaribbeanCasinoOffers.py (Club Royale casino offer tracker).

Covers the pure parsing/date logic and the network/report functions with the HTTP
session and logging mocked - no live API calls or credentials required.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import CheckRoyalCaribbeanCasinoOffers as casino
from CheckRoyalCaribbeanCasinoOffers import CasinoOffer


def _raw(code="26TOR309", otype="COMP", reserve="2030-01-15T00:00:00.000Z", perks=("FreePlay",)):
    """Build one element of the casino offers API 'offers' array."""
    return {
        "campaignCode": "CMP1",
        "campaignName": "January Casino Offer",
        "status": "ACTIVE",
        "campaignOffer": {
            "offerCode": code,
            "name": "Complimentary Cruise",
            "offerType": {"code": otype, "name": "Complimentary" if otype == "COMP" else "Get One, Buy One"},
            "reserveByDate": reserve,
            "perkCodes": [{"perkName": p} for p in perks],
            "status": "ACTIVE",
        },
    }


# --- CasinoOffer.from_api parsing ---
def test_from_api_parses_core_fields():
    o = CasinoOffer.from_api(_raw())
    assert o.offer_code == "26TOR309"
    assert o.offer_type_code == "COMP"
    assert o.reserve_by_date == "2030-01-15T00:00:00.000Z"
    assert o.perks == ["FreePlay"]
    assert o.campaign_name == "January Casino Offer"


def test_from_api_tolerates_missing_campaign_offer():
    o = CasinoOffer.from_api({"campaignName": "Fallback Name", "status": "ACTIVE"})
    assert o.offer_code == "?"          # default when offerCode absent
    assert o.name == "Fallback Name"    # falls back to campaignName
    assert o.perks == []
    assert o.offer_type_code == ""


# --- is_complimentary property (COMP vs GOBO) ---
def test_is_complimentary_true_for_comp():
    assert CasinoOffer.from_api(_raw(otype="COMP")).is_complimentary is True


def test_is_complimentary_false_for_gobo():
    assert CasinoOffer.from_api(_raw(otype="GOBO")).is_complimentary is False


# --- days_until_reserve_by date math ---
def test_days_until_reserve_by_future():
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    assert CasinoOffer.from_api(_raw(reserve=future)).days_until_reserve_by() in (9, 10)


def test_days_until_reserve_by_none_and_bad_input():
    assert CasinoOffer.from_api(_raw(reserve=None)).days_until_reserve_by() is None
    assert CasinoOffer.from_api(_raw(reserve="not-a-date")).days_until_reserve_by() is None


# --- fetch_casino_offers: parsing, pagination, error handling ---
def _account():
    account = MagicMock()
    account.access.token = "tok"
    account.access.id = "acct-id"
    account.access.loyalty_number = "390000000"
    return account


def test_fetch_casino_offers_parses_and_follows_pagination():
    account = _account()
    page1 = MagicMock(status_code=200)
    page1.json.return_value = {"offers": [_raw("A")], "totalPages": 2}
    page2 = MagicMock(status_code=200)
    page2.json.return_value = {"offers": [_raw("B")], "totalPages": 2}
    account.access.session.get.side_effect = [page1, page2]

    with patch.object(casino, "log", MagicMock()):
        offers = casino.fetch_casino_offers(account)

    assert [o.offer_code for o in offers] == ["A", "B"]
    assert account.access.session.get.call_count == 2


def test_fetch_casino_offers_returns_empty_on_http_error():
    account = _account()
    account.access.session.get.return_value = MagicMock(status_code=500)
    with patch.object(casino, "log", MagicMock()):
        assert casino.fetch_casino_offers(account) == []


def test_fetch_casino_offers_returns_empty_on_exception():
    account = _account()
    account.access.session.get.side_effect = RuntimeError("network down")
    with patch.object(casino, "log", MagicMock()):
        assert casino.fetch_casino_offers(account) == []


# --- report_offers: deadline alerting ---
def test_report_offers_alerts_when_reserve_by_is_near():
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    offers = [CasinoOffer.from_api(_raw(code="SOON1", reserve=soon))]
    apobj = MagicMock()
    with patch.object(casino, "log", MagicMock()):
        casino.report_offers(offers, warn_days=14, apobj=apobj)
    apobj.notify.assert_called_once()


def test_report_offers_no_alert_when_far_out():
    far = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    offers = [CasinoOffer.from_api(_raw(code="FAR1", reserve=far))]
    apobj = MagicMock()
    with patch.object(casino, "log", MagicMock()):
        casino.report_offers(offers, warn_days=14, apobj=apobj)
    apobj.notify.assert_not_called()


def test_report_offers_handles_no_offers():
    apobj = MagicMock()
    mock_log = MagicMock()
    with patch.object(casino, "log", mock_log):
        casino.report_offers([], warn_days=14, apobj=apobj)
    apobj.notify.assert_not_called()
    assert any("No active casino offers" in str(c[0][0]) for c in mock_log.call_args_list)

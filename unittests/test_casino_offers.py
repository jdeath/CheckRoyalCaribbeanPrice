"""
Unit tests for CheckRoyalCaribbeanCasinoOffers.py (Club Royale casino offer tracker).

Covers the pure parsing/date logic and the network/report functions with the HTTP
session and logging mocked - no live API calls or credentials required.
"""
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import CheckRoyalCaribbeanCasinoOffers as casino
import CheckRoyalCaribbeanPrice as crc
from CheckRoyalCaribbeanCasinoOffers import (
    CasinoOffer,
    fetch_casino_offers,
    load_config_file,
    report_offers
)


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

    with patch("CheckRoyalCaribbeanCasinoOffers.log", MagicMock()):
        offers = fetch_casino_offers(account)

    assert [o.offer_code for o in offers] == ["A", "B"]
    assert account.access.session.get.call_count == 2


def test_fetch_casino_offers_returns_empty_on_http_error():
    account = _account()
    account.access.session.get.return_value = MagicMock(status_code=500)
    with patch("CheckRoyalCaribbeanCasinoOffers.log", MagicMock()):
        assert fetch_casino_offers(account) == []


def test_fetch_casino_offers_returns_empty_on_exception():
    account = _account()
    account.access.session.get.side_effect = RuntimeError("network down")
    with patch("CheckRoyalCaribbeanCasinoOffers.log", MagicMock()):
        assert fetch_casino_offers(account) == []


# --- report_offers: deadline alerting ---
def test_report_offers_alerts_when_reserve_by_is_near():
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    offers = [CasinoOffer.from_api(_raw(code="SOON1", reserve=soon))]
    apobj = MagicMock()
    with patch("CheckRoyalCaribbeanCasinoOffers.log", MagicMock()):
        report_offers(offers, warn_days=14, apobj=apobj)
    apobj.notify.assert_called_once()


def test_report_offers_no_alert_when_far_out():
    far = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    offers = [CasinoOffer.from_api(_raw(code="FAR1", reserve=far))]
    apobj = MagicMock()
    with patch("CheckRoyalCaribbeanCasinoOffers.log", MagicMock()):
        report_offers(offers, warn_days=14, apobj=apobj)
    apobj.notify.assert_not_called()


def test_report_offers_handles_no_offers():
    apobj = MagicMock()
    mock_log = MagicMock()
    with patch("CheckRoyalCaribbeanCasinoOffers.log", mock_log):
        report_offers([], warn_days=14, apobj=apobj)
    apobj.notify.assert_not_called()
    assert any("No active casino offers" in str(c[0][0]) for c in mock_log.call_args_list)


# --- load_config_file ---
def test_load_config_file_expands_env_vars(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "accountInfo:\n  - username: a@b.c\n    password: ${RC_TEST_PW}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RC_TEST_PW", "secret")
    assert load_config_file(str(cfg))["accountInfo"][0]["password"] == "secret"


def test_load_config_file_empty_file_returns_empty_dict(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("", encoding="utf-8")
    assert load_config_file(str(cfg)) == {}


# --- main: config, logging and apprise wiring ---
def test_main_wires_loggers_and_apprise_list(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "logFile: run.log\napprise:\n  - url: json://localhost/\n",
        encoding="utf-8",
    )
    ready_log = MagicMock()

    def fake_setup(log_file):
        # Stands in for setup_hybrid_logging, which only rebinds the main module's loggers
        assert log_file == "run.log"
        crc.log = ready_log

    # Module-level loggers start as the main module's pre-setup None placeholders
    with patch.object(crc, "log", None), \
         patch.object(casino, "log", None), \
         patch.object(casino, "setup_hybrid_logging", fake_setup), \
         patch.object(casino, "build_apprise") as build_apprise, \
         patch.object(casino, "build_account"), \
         patch.object(casino, "fetch_casino_offers", return_value=[]), \
         patch.object(sys, "argv", ["prog", "-c", str(cfg)]):
        casino.main()
        assert casino.log is ready_log

    build_apprise.assert_called_once_with([{"url": "json://localhost/"}])

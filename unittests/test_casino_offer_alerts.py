"""
checkCasinoOffers: the Club Royale offer check folded into the main run.

Covers the offer model, the paginated fetch (and every way it can come back
partial), the report/alert, the config keys, and the per-account hook in
main(): the check runs on the session the price check already logged in,
uses that account's own notifier, is skipped for Celebrity, and can never
end a run.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from CheckRoyalCaribbeanPrice import (
    MAX_CASINO_OFFER_PAGES,
    AccountInfo,
    APIAccess,
    CasinoOffer,
    CruiseAppConfig,
    check_casino_offers,
    fetch_casino_offers,
    load_config_objects,
    main,
    report_casino_offers,
)


def _raw_offer(code="26ABC123", name="Sample Casino Offer", type_code="COMP", type_name="Complimentary",
               reserve_by="2099-01-01T00:00:00Z", perks=("Bonus FP $25",)):
    return {"campaignName": "Campaign", "status": "ACTIVE",
            "campaignOffer": {"offerCode": code, "name": name, "reserveByDate": reserve_by,
                              "offerType": {"code": type_code, "name": type_name},
                              "perkCodes": [{"perkName": p} for p in perks]}}


def _iso_in(days):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _account(is_royal=True):
    account = AccountInfo(username="user@example.com", password="pw",
                          cruise_line="royalcaribbean" if is_royal else "celebrity")
    account.access = APIAccess(token="tok", id="acct-1", session=MagicMock())
    return account


def _page(offers, total_pages=1, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"offers": offers, "totalPages": total_pages}
    return resp


class TestCasinoOffer:

    def test_from_api_flattens_the_nested_record(self):
        offer = CasinoOffer.from_api(_raw_offer())
        assert offer.offer_code == "26ABC123" and offer.name == "Sample Casino Offer"
        assert offer.offer_type_code == "COMP" and offer.is_complimentary
        assert offer.perks == ["Bonus FP $25"] and offer.status == "ACTIVE"
        # GOBO is keyed on the type CODE, never the templated description
        gobo = CasinoOffer.from_api(_raw_offer(type_code="GOBO", type_name="Complimentary cruise"))
        assert not gobo.is_complimentary
        # missing / malformed sub-objects never raise
        bare = CasinoOffer.from_api({"campaignName": "X", "campaignOffer": {"offerType": "junk",
                                                                            "perkCodes": ["junk"]}})
        assert bare.offer_code == "?" and bare.name == "X" and bare.perks == [] and bare.offer_type_code == ""

    @pytest.mark.parametrize("reserve_by, expected", [
        (_iso_in(10), 10),                                  # Z suffix
        ((datetime.now(timezone.utc) + timedelta(days=5, hours=2)).isoformat(), 5),   # +00:00 offset
        ((datetime.now(timezone.utc) + timedelta(days=3, hours=2)).strftime("%Y-%m-%dT%H:%M:%S"), 3),  # naive
        ((datetime.now(timezone.utc) + timedelta(days=2, hours=12)).strftime("%Y-%m-%d"), 2),          # date only
        ("not a date", None), (None, None), ("", None), (12345, None),
    ])
    def test_days_until_reserve_by(self, reserve_by, expected):
        """A timezone-less deadline once raised on the aware-minus-naive
        subtraction and silently made the offer un-alertable."""
        assert CasinoOffer.from_api(_raw_offer(reserve_by=reserve_by)).days_until_reserve_by() == expected


class TestFetchCasinoOffers:

    def test_follows_pagination_and_sends_the_identity_headers(self):
        account = _account()
        account.access.session.get.side_effect = [
            _page([_raw_offer(code="A")], total_pages="2"),      # string totalPages is coerced
            _page([_raw_offer(code="B")], total_pages=2)]
        offers, complete = fetch_casino_offers(account, "123456789")
        assert [o.offer_code for o in offers] == ["A", "B"] and complete is True
        calls = account.access.session.get.call_args_list
        assert [c.kwargs["params"]["page"] for c in calls] == ["1", "2"]
        headers = calls[0].kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["x-account-id"] == "acct-1" and headers["x-loyalty-id"] == "123456789"
        assert calls[0].kwargs["cookies"]["accessToken"] == "tok"
        assert calls[0].kwargs["timeout"] is not None

    def test_no_loyalty_number_sends_an_empty_header_not_None(self):
        account = _account()
        account.access.session.get.return_value = _page([])
        fetch_casino_offers(account, None)
        assert account.access.session.get.call_args.kwargs["headers"]["x-loyalty-id"] == ""

    @pytest.mark.parametrize("failure", ["http", "exception", "non_json", "not_dict", "bad_total"])
    def test_every_failure_mode_returns_the_pages_so_far_as_partial(self, failure):
        account = _account()
        second = _page([_raw_offer(code="B")])
        if failure == "http":
            second.status_code = 500
        elif failure == "exception":
            second = ConnectionError("reset")
        elif failure == "non_json":
            second.json.side_effect = ValueError("no json")
        elif failure == "not_dict":
            second.json.return_value = ["not", "a", "dict"]
        elif failure == "bad_total":
            second.json.return_value = {"offers": [_raw_offer(code="B")], "totalPages": "many"}
        first = _page([_raw_offer(code="A")], total_pages=2)
        account.access.session.get.side_effect = [first, second]
        with patch("CheckRoyalCaribbeanPrice.log"), patch("CheckRoyalCaribbeanPrice.log_warn"):
            offers, complete = fetch_casino_offers(account, "1")
        assert complete is False, failure
        assert [o.offer_code for o in offers][0] == "A", failure
        assert len(offers) <= 2

    def test_absurd_page_count_is_capped_and_reported_partial(self):
        account = _account()
        account.access.session.get.return_value = _page([_raw_offer()], total_pages=10_000)
        with patch("CheckRoyalCaribbeanPrice.log_warn") as warn:
            offers, complete = fetch_casino_offers(account, "1")
        assert account.access.session.get.call_count == MAX_CASINO_OFFER_PAGES
        assert complete is False and len(offers) == MAX_CASINO_OFFER_PAGES
        assert "claims 10000 pages" in warn.call_args.args[0]

    def test_non_dict_offer_entries_are_skipped(self):
        account = _account()
        account.access.session.get.return_value = _page(["junk", _raw_offer(code="OK"), None])
        offers, complete = fetch_casino_offers(account, "1")
        assert [o.offer_code for o in offers] == ["OK"] and complete is True


class TestReportCasinoOffers:

    def _report(self, offers, warn_days=14, apobj="default", complete=True):
        apobj = MagicMock() if apobj == "default" else apobj
        logged = []
        with patch("CheckRoyalCaribbeanPrice.log", side_effect=lambda m, *a, **k: logged.append(str(m))):
            report_casino_offers(offers, warn_days, apobj, complete)
        return "\n".join(logged), apobj

    def test_expiring_offers_alert_once_sorted_by_deadline(self):
        offers = [CasinoOffer.from_api(_raw_offer(code="LATER", reserve_by=_iso_in(10))),
                  CasinoOffer.from_api(_raw_offer(code="SOON", reserve_by=_iso_in(2), type_code="GOBO",
                                                  type_name="Get One, Buy One")),
                  CasinoOffer.from_api(_raw_offer(code="EDGE", reserve_by=_iso_in(14))),   # exactly warn_days
                  CasinoOffer.from_api(_raw_offer(code="FAR", reserve_by=_iso_in(60)))]
        out, apobj = self._report(offers)
        apobj.notify.assert_called_once()
        kwargs = apobj.notify.call_args.kwargs
        assert kwargs["title"] == "Club Royale Offer Expiring"
        body = kwargs["body"]
        assert body.startswith("3 Club Royale offer(s) expiring within 14 days:")
        assert body.index("SOON") < body.index("LATER") < body.index("EDGE") and "FAR" not in body
        assert "Get One, Buy One" in body and "+Bonus FP $25" in body
        assert "[EXPIRING]" in out and "[COMP: 2nd guest discounted]" in out   # FAR is a COMP

    def test_no_alert_when_nothing_is_close(self):
        out, apobj = self._report([CasinoOffer.from_api(_raw_offer(reserve_by=_iso_in(60)))])
        apobj.notify.assert_not_called()
        assert "No offers within 14 days" in out

    def test_no_notifier_is_console_only(self):
        out, _ = self._report([CasinoOffer.from_api(_raw_offer(reserve_by=_iso_in(1)))], apobj=None)
        assert "1 Club Royale offer(s) expiring" in out          # still logged

    def test_partial_and_empty_results_say_so(self):
        out, apobj = self._report([], complete=False)
        assert "No active Club Royale offers found." in out and "partial" in out
        apobj.notify.assert_not_called()
        out, _ = self._report([CasinoOffer.from_api(_raw_offer(reserve_by=_iso_in(60)))], complete=False)
        assert "1 active" in out and "list may be incomplete" in out

    def test_offer_without_deadline_never_alerts(self):
        out, apobj = self._report([CasinoOffer.from_api(_raw_offer(reserve_by=None))], warn_days=9999)
        apobj.notify.assert_not_called()
        assert "no deadline" in out


class TestCheckCasinoOffersHook:

    def test_check_uses_the_account_notifier_and_configured_days(self):
        account = _account()
        cfg = CruiseAppConfig(check_casino_offers=True, casino_offer_warn_days=3)
        notifier = MagicMock()
        with patch("CheckRoyalCaribbeanPrice.config", cfg), \
             patch("CheckRoyalCaribbeanPrice.log"), \
             patch("CheckRoyalCaribbeanPrice.notifier_for", return_value=notifier) as nf, \
             patch("CheckRoyalCaribbeanPrice.fetch_casino_offers",
                   return_value=([CasinoOffer.from_api(_raw_offer(reserve_by=_iso_in(2)))], True)) as fetch:
            check_casino_offers(account, "987654321")
        fetch.assert_called_once_with(account, "987654321")
        nf.assert_called_once_with(account)
        assert "within 3 days" in notifier.notify.call_args.kwargs["body"]

    def test_celebrity_account_is_skipped_without_a_request(self):
        account = _account(is_royal=False)
        logged = []
        with patch("CheckRoyalCaribbeanPrice.config", CruiseAppConfig(check_casino_offers=True)), \
             patch("CheckRoyalCaribbeanPrice.log", side_effect=lambda m, *a, **k: logged.append(str(m))), \
             patch("CheckRoyalCaribbeanPrice.fetch_casino_offers") as fetch:
            check_casino_offers(account, "1")
        fetch.assert_not_called()
        assert any("Blue Chip" in m for m in logged)

    def test_unexpected_failure_is_contained(self):
        logged = []
        with patch("CheckRoyalCaribbeanPrice.config", CruiseAppConfig(check_casino_offers=True)), \
             patch("CheckRoyalCaribbeanPrice.log", side_effect=lambda m, *a, **k: logged.append(str(m))), \
             patch("CheckRoyalCaribbeanPrice.fetch_casino_offers", side_effect=RuntimeError("boom")):
            check_casino_offers(_account(), "1")                     # must not raise
        assert any("offer check skipped" in m and "boom" in m for m in logged)


class TestMainHook:

    def _run_main(self, accounts, cfg):
        """Drive main() with login/profile/voyages mocked; return the casino calls."""
        cfg.accounts = accounts
        cfg.apprise_test = False
        with patch("CheckRoyalCaribbeanPrice.config", cfg), \
             patch("CheckRoyalCaribbeanPrice.history", MagicMock()), \
             patch("CheckRoyalCaribbeanPrice.log"), \
             patch("CheckRoyalCaribbeanPrice.get_ship_dictionary_web"), \
             patch("CheckRoyalCaribbeanPrice.CheckinPaymentTracker"), \
             patch("CheckRoyalCaribbeanPrice.login",
                   side_effect=lambda a: APIAccess(token="t", id="i", session=MagicMock())), \
             patch("CheckRoyalCaribbeanPrice.get_profile", return_value=("FL", "555000111", 10)), \
             patch("CheckRoyalCaribbeanPrice.get_voyages") as voyages, \
             patch("CheckRoyalCaribbeanPrice.time.sleep"), \
             patch("CheckRoyalCaribbeanPrice.check_casino_offers") as casino:
            main()
        return casino, voyages

    def test_flag_on_checks_each_account_after_its_bookings_with_its_loyalty_number(self):
        accounts = [_account(), _account()]
        accounts[1].username = "second@example.com"
        order = []
        cfg = CruiseAppConfig(check_casino_offers=True)

        def _login(account):
            session = MagicMock()
            session.close.side_effect = lambda: order.append(("close", account.username))
            return APIAccess(token="t", id="i", session=session)
        with patch("CheckRoyalCaribbeanPrice.config", cfg), \
             patch("CheckRoyalCaribbeanPrice.history", MagicMock()), \
             patch("CheckRoyalCaribbeanPrice.log"), \
             patch("CheckRoyalCaribbeanPrice.get_ship_dictionary_web"), \
             patch("CheckRoyalCaribbeanPrice.CheckinPaymentTracker"), \
             patch("CheckRoyalCaribbeanPrice.login", side_effect=_login), \
             patch("CheckRoyalCaribbeanPrice.get_profile", return_value=("FL", "555000111", 10)), \
             patch("CheckRoyalCaribbeanPrice.get_voyages",
                   side_effect=lambda a, *x, **k: order.append(("voyages", a.username))), \
             patch("CheckRoyalCaribbeanPrice.time.sleep"), \
             patch("CheckRoyalCaribbeanPrice.check_casino_offers",
                   side_effect=lambda a, loyalty: order.append(("casino", a.username, loyalty))):
            cfg.accounts = accounts
            cfg.apprise_test = False
            main()
        # the check runs after that account's bookings and BEFORE its session is
        # closed - it reuses the session the price check logged in
        assert order == [("voyages", "user@example.com"), ("casino", "user@example.com", "555000111"),
                         ("close", "user@example.com"),
                         ("voyages", "second@example.com"), ("casino", "second@example.com", "555000111"),
                         ("close", "second@example.com")]

    def test_flag_off_never_calls_the_check(self):
        casino, voyages = self._run_main([_account()], CruiseAppConfig())
        voyages.assert_called_once()
        casino.assert_not_called()

    def test_string_true_is_not_true(self):
        """A MagicMock or a stray string must not switch the feature on."""
        cfg = CruiseAppConfig(check_casino_offers="yes")
        casino, _ = self._run_main([_account()], cfg)
        casino.assert_not_called()


class TestConfigKeys:

    def _load(self, tmp_path, extra):
        f = tmp_path / "config.yaml"
        f.write_text('accountInfo:\n  - username: "u"\n    password: "p"\n' + extra)
        with patch("CheckRoyalCaribbeanPrice.setup_hybrid_logging"):
            return load_config_objects(str(f))

    def test_defaults_and_tolerant_parsing(self, tmp_path):
        cfg = self._load(tmp_path, "")
        assert cfg.check_casino_offers is False and cfg.casino_offer_warn_days == 14
        cfg = self._load(tmp_path, 'checkCasinoOffers: true\ncasinoOfferWarnDays: "30"\n')
        assert cfg.check_casino_offers is True and cfg.casino_offer_warn_days == 30
        assert self._load(tmp_path, 'checkCasinoOffers: "false"\n').check_casino_offers is False
        # present-but-null keys keep the defaults
        cfg = self._load(tmp_path, "checkCasinoOffers:\ncasinoOfferWarnDays:\n")
        assert cfg.check_casino_offers is False and cfg.casino_offer_warn_days == 14
        assert self._load(tmp_path, "casinoOfferWarnDays: 0\n").casino_offer_warn_days == 0

    def test_invalid_days_are_rejected_with_the_key_named(self, tmp_path):
        with pytest.raises(ValueError, match="casinoOfferWarnDays"):
            self._load(tmp_path, "casinoOfferWarnDays: soon\n")
        with pytest.raises(ValueError, match="negative"):
            self._load(tmp_path, "casinoOfferWarnDays: -1\n")

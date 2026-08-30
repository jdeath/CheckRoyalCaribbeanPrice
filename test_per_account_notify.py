"""
Per-account Apprise notification routing tests ("PR 1b").

Two people can share one config.yaml, each with their own accountInfo entry.
Alerts produced while processing an account should go to that account's own
Apprise object when one is configured, and fall back to the global
config.apobj otherwise. The apprise self-test and notifyOnError stay global
(plus, for the self-test, each per-account notifier also gets a test message).

These tests cover:
  - load_config_objects: per-account apobj construction, with/without apprise:
  - notifier_for: the three resolution cases
  - integration: get_cruise_price routes to the right notifier
  - main(): the apprise_test path notifies both the global and per-account objects
"""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from apprise import Apprise

from CheckRoyalCaribbeanPrice import (
    AccountInfo,
    CruiseAppConfig,
    _build_apprise,
    get_cruise_price,
    load_config_objects,
    notifier_for,
)

import CheckRoyalCaribbeanPrice as rcapp


# =====================================================================
# FIXTURES & SCENARIO HELPERS (mirrors test_alert_matrix.py's style)
# =====================================================================

def make_account(username: str = "test_user") -> AccountInfo:
    account = AccountInfo(username=username, password="password", cruise_line="royal")
    account.access = MagicMock()
    account.access.token = "fake_token"
    account.access.id = "fake_id"
    account.access.loyalty_number = None
    return account


def make_checkout_url(sail_days_out: int = 400) -> str:
    """A realistic cruise planner URL like the ones users paste into config.yaml."""
    sail = (date.today() + timedelta(days=sail_days_out)).isoformat()
    return (
        f"https://www.royalcaribbean.com/checkout/guest-info?sailDate={sail}"
        "&shipCode=WN&packageCode=WN07X123&selectedCurrencyCode=USD&country=USA"
        "&cabinClassType=BALCONY&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n"
        "&r0q=n&r0t=n&r0d=BALCONY&r0D=y&r0e=N&r0f=4D&r0g=BESTRATE"
    )


def fare(amount, grats=100.0, ins=50.0, obc=0.0):
    return {"fare": amount, "gratuities": grats, "insurance": ins, "obc": obc}


AVAILABLE = {"room_available": True, "sailing_nights": 7, "available_rooms": []}


def run_price_drop_scenario(*, account, config_apobj, other_accounts=None):
    """
    Drive get_cruise_price with a guaranteed price drop (fires exactly one
    notify), routing through whatever config/account apobj is wired up.

    other_accounts, if given, are unrelated AccountInfo objects also present
    in config.accounts (as in a real shared config), so a fallback test can
    prove the notification lands on the global notifier and not on them.
    """
    booking = {"url": make_checkout_url()}
    paid_price_struct = {"paidPrice": 3000.0}

    mock_cfg = MagicMock()
    mock_cfg.apobj = config_apobj
    mock_cfg.accounts = [account] + (other_accounts or [])
    mock_cfg.minimum_saving_alert = None
    mock_cfg.currency_override = None
    mock_cfg.date_display_format = "%m/%d/%Y"
    mock_cfg.format_date = lambda d: str(d)

    ship_dictionary = MagicMock()
    ship_dictionary.get_ship.return_value = "Wonder of the Seas"

    results = {**AVAILABLE, "base_fare": fare(2500.0)}  # 2500 < 3000 paid: guaranteed drop

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.log"), \
         patch("CheckRoyalCaribbeanPrice.get_room_price_via_API", return_value=results):
        get_cruise_price(account, booking, ship_dictionary, automatic_URL=True,
                          paid_price_struct=paid_price_struct)


# =====================================================================
# CONFIG LOADING: per-account apprise -> AccountInfo.apobj
# =====================================================================

class TestLoadConfigObjectsPerAccountApprise:

    def test_account_with_apprise_gets_its_own_apobj(self, tmp_path):
        yaml_content = """
    accountInfo:
      - username: "chris@example.com"
        password: "password123"
        apprise:
          - url: "json://chris-topic"
      - username: "friend@example.com"
        password: "password456"
    """
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        with patch('CheckRoyalCaribbeanPrice.setup_hybrid_logging'):
            config = load_config_objects(str(config_file))

        assert isinstance(config, CruiseAppConfig)
        chris, friend = config.accounts
        assert isinstance(chris.apobj, Apprise)
        assert len(chris.apobj) == 1

    def test_account_without_apprise_has_no_apobj(self, tmp_path):
        yaml_content = """
    accountInfo:
      - username: "chris@example.com"
        password: "password123"
        apprise:
          - url: "json://chris-topic"
      - username: "friend@example.com"
        password: "password456"
    """
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        with patch('CheckRoyalCaribbeanPrice.setup_hybrid_logging'):
            config = load_config_objects(str(config_file))

        chris, friend = config.accounts
        assert friend.apobj is None

    def test_no_per_account_apprise_anywhere_matches_old_behavior(self, tmp_path):
        """With no per-account apprise: at all, every account.apobj stays None."""
        yaml_content = """
    accountInfo:
      - username: "chris@example.com"
        password: "password123"
      - username: "friend@example.com"
        password: "password456"
    apprise:
      - url: "json://global-topic"
    """
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        with patch('CheckRoyalCaribbeanPrice.setup_hybrid_logging'):
            config = load_config_objects(str(config_file))

        assert all(a.apobj is None for a in config.accounts)
        assert isinstance(config.apobj, Apprise)
        assert len(config.apobj) == 1


class TestBuildApprise:

    def test_empty_list_returns_none(self):
        assert _build_apprise([]) is None

    def test_missing_url_key_is_skipped(self):
        assert _build_apprise([{"not_url": "x"}]) is None

    def test_urls_build_a_real_apprise_object(self):
        apobj = _build_apprise([{"url": "json://topic-a"}, {"url": "json://topic-b"}])
        assert isinstance(apobj, Apprise)
        assert len(apobj) == 2

    def test_apprise_not_installed_returns_none_and_warns(self):
        """Mirrors the pre-existing #85 'apprise optional' sentinel handling."""
        with patch.object(rcapp, "Apprise", None), \
             patch("CheckRoyalCaribbeanPrice.logging.warning") as mock_warn:
            result = _build_apprise([{"url": "json://topic-a"}])
        assert result is None
        mock_warn.assert_called_once()


# =====================================================================
# RESOLVER: notifier_for
# =====================================================================

class TestNotifierFor:

    def test_returns_account_apobj_when_present(self):
        global_apobj = MagicMock()
        account = make_account()
        account.apobj = MagicMock()

        mock_cfg = MagicMock()
        mock_cfg.apobj = global_apobj
        with patch("CheckRoyalCaribbeanPrice.config", mock_cfg):
            assert notifier_for(account) is account.apobj

    def test_falls_back_to_global_when_account_has_none(self):
        global_apobj = MagicMock()
        account = make_account()  # apobj stays None by default

        mock_cfg = MagicMock()
        mock_cfg.apobj = global_apobj
        with patch("CheckRoyalCaribbeanPrice.config", mock_cfg):
            assert notifier_for(account) is global_apobj

    def test_none_account_falls_back_to_global(self):
        global_apobj = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.apobj = global_apobj
        with patch("CheckRoyalCaribbeanPrice.config", mock_cfg):
            assert notifier_for(None) is global_apobj


# =====================================================================
# INTEGRATION: routing during an actual price-drop notification
# =====================================================================

class TestPerAccountRoutingIntegration:

    def test_price_drop_notifies_account_apobj_not_global(self):
        """account_info.apobj = B is configured: B fires, global A stays silent."""
        global_apobj = MagicMock(name="global_A")
        account = make_account()
        account.apobj = MagicMock(name="account_B")

        run_price_drop_scenario(account=account, config_apobj=global_apobj)

        account.apobj.notify.assert_called_once()
        global_apobj.notify.assert_not_called()

    def test_price_drop_falls_back_to_global_when_no_account_apobj(self):
        """
        Inverse: no per-account apobj configured -> global A fires instead.

        A second, unrelated account with its own apobj is present in the run
        (as it would be in a real shared config) to prove the fallback lands
        on the global notifier specifically, not on some other account's.
        """
        global_apobj = MagicMock(name="global_A")
        account = make_account()  # apobj stays None
        other_account = make_account(username="other_user")
        other_account.apobj = MagicMock(name="other_account_apobj")

        run_price_drop_scenario(account=account, config_apobj=global_apobj,
                                 other_accounts=[other_account])

        global_apobj.notify.assert_called_once()
        other_account.apobj.notify.assert_not_called()


# =====================================================================
# apprise_test PATH: both global and per-account notifiers get a test message
# =====================================================================

class TestAppriseTestNotifiesAllNotifiers:

    def test_main_apprise_test_notifies_global_and_each_account(self):
        recorder = []  # locks the ORDER: global notify -> account notify -> SystemExit

        global_apobj = MagicMock(name="global")
        global_apobj.notify.side_effect = lambda **kw: recorder.append("global_notify")

        account_with = make_account(username="chris@example.com")
        account_with.apobj = MagicMock(name="chris_apobj")
        account_with.apobj.notify.side_effect = lambda **kw: recorder.append("account_notify:chris@example.com")

        account_without = make_account(username="friend@example.com")
        # account_without.apobj stays None -> must NOT receive a direct notify

        mock_cfg = MagicMock()
        mock_cfg.apobj = global_apobj
        mock_cfg.apprise_test = True
        mock_cfg.log_file = None
        mock_cfg.accounts = [account_with, account_without]
        mock_cfg.format_date = lambda d: str(d)

        with patch.object(rcapp, "config", mock_cfg), \
             patch("CheckRoyalCaribbeanPrice.log", MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                rcapp.main()
            recorder.append("SystemExit")

        assert exc_info.value.code == 0

        # Global test notification (existing behavior)
        global_apobj.notify.assert_called_once()
        assert "This is only a test." in global_apobj.notify.call_args.kwargs["body"]

        # Per-account test notification, naming the account
        account_with.apobj.notify.assert_called_once()
        body = account_with.apobj.notify.call_args.kwargs["body"]
        assert "chris@example.com" in body
        assert "This is only a test for account" in body

        # Locked ORDER: global -> per-account -> SystemExit
        assert recorder == [
            "global_notify",
            "account_notify:chris@example.com",
            "SystemExit",
        ]

    def test_apprise_test_fires_with_only_per_account_notifiers_configured(self):
        """
        No global apprise: list at all, only a per-account one. The self-test
        must still fire (guard fix) instead of silently falling through into
        a real pricing pass.
        """
        account_with = make_account(username="chris@example.com")
        account_with.apobj = MagicMock(name="chris_apobj")

        mock_cfg = MagicMock()
        mock_cfg.apobj = None  # no global notifier configured
        mock_cfg.apprise_test = True
        mock_cfg.log_file = None
        mock_cfg.accounts = [account_with]
        mock_cfg.format_date = lambda d: str(d)

        with patch.object(rcapp, "config", mock_cfg), \
             patch("CheckRoyalCaribbeanPrice.log", MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                rcapp.main()

        assert exc_info.value.code == 0
        account_with.apobj.notify.assert_called_once()
        assert "chris@example.com" in account_with.apobj.notify.call_args.kwargs["body"]

    def test_apprise_test_does_not_fire_with_no_notifiers_anywhere(self):
        """
        apprise_test: true but neither a global nor any per-account apprise:
        list is configured -> the self-test block must not be entered at all
        (no notifiers to test, so the run falls through instead of exiting).
        """
        account_without = make_account(username="friend@example.com")
        # apobj stays None

        mock_cfg = MagicMock()
        mock_cfg.apobj = None
        mock_cfg.apprise_test = True
        mock_cfg.log_file = None
        mock_cfg.format_date = lambda d: str(d)
        mock_cfg.minimum_saving_alert = None
        mock_cfg.prospective_cruises = []
        mock_cfg.output_watch_as_json = False
        mock_cfg.accounts = []  # empty account list -> the login loop body never runs
        # keep account_without referenced so a stray any(...) over it can't
        # accidentally short-circuit the guard to True
        assert account_without.apobj is None

        with patch.object(rcapp, "config", mock_cfg), \
             patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
             patch("CheckRoyalCaribbeanPrice.get_ship_dictionary_web") as ship_dictionary_mock, \
             patch("CheckRoyalCaribbeanPrice.print_checkin_payment_table"):
            rcapp.main()  # must NOT raise SystemExit

        # The self-test block exits the process; reaching the ship-dictionary
        # fetch proves the run fell through past it instead.
        ship_dictionary_mock.assert_called_once()

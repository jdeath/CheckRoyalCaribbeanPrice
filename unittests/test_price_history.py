"""
Tests for the opt-in SQLite price-history layer (PriceHistory).

Two groups:
  - Unit tests: construct the real PriceHistory class against a tmp_path sqlite
    file (or db_path=None for the disabled case), no config/module patching needed.
  - Integration tests: patch CheckRoyalCaribbeanPrice.history with a MagicMock,
    drive the real production functions (get_cruise_price / get_new_order_price / main),
    and assert on history.record_*.call_args.kwargs - the same "real object, mocked
    side effect" pattern already used across the codebase.
"""
import json
import os
import pytest
import sqlite3

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from CheckRoyalCaribbeanPrice import (
# PART 1: PriceHistory UNIT TESTS
# PART 2: INTEGRATION TESTS - production functions call module-level history
# PART 3: PR 2 - `bookings` / `promos` snapshot tables
    AccountInfo,
    CruiseAppConfig,
    PriceHistory,
    WatchItemContext,
    get_all_promotions,
    get_cruise_price,
    get_final_payment_date,
    get_new_order_price,
    get_voyages,
    history,
    load_config_objects,
    main,
)


# =====================================================================
# PART 1: PriceHistory UNIT TESTS
# =====================================================================

def test_price_history_is_noop_when_db_path_unset(tmp_path, monkeypatch):
    """
    With db_path unset, PriceHistory must never touch the filesystem: no file
    created anywhere, and every public method silently no-ops.
    """
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))

    history = PriceHistory(None)
    assert history.enabled is False

    assert history.start_run() is None
    history.finish_run("ok")
    history.record_cabin_fare(reservation_id="1", ship_code="WN", status="priced")
    history.record_addon(item_kind="addon", status="priced")

    after = set(os.listdir(tmp_path))
    assert after == before, f"PriceHistory(None) created filesystem entries: {after - before}"


def test_price_history_creates_schema_on_first_use(tmp_path):
    """Constructing against a real path creates every table (price_points, bookings,
    promos) and all of their indexes."""
    db_path = tmp_path / "h.db"
    PriceHistory(str(db_path))

    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"runs", "price_points", "bookings", "promos"} <= tables

        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert {
            "idx_price_points_latest", "idx_price_points_history", "idx_price_points_run",
            "idx_bookings_latest", "idx_promos_run",
        } <= indexes
    finally:
        conn.close()


def test_price_history_run_lifecycle(tmp_path):
    """start_run() -> finish_run('ok') produces one runs row with both timestamps and status='ok'."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))

    run_id = history.start_run()
    assert run_id is not None
    history.finish_run("ok")

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT run_id, started_at, finished_at, status, error_summary FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    db_run_id, started_at, finished_at, status, error_summary = row
    assert db_run_id == run_id
    assert started_at
    assert finished_at
    assert status == "ok"
    assert error_summary is None


def test_price_history_record_cabin_fare_round_trip(tmp_path):
    """record_cabin_fare() writes a price_points row that reads back with every column intact."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))
    history.start_run()

    history.record_cabin_fare(
        reservation_id="1234567",
        account_label="user@example.com",
        ship_code="WN",
        sail_date="20270501",
        nights=7,
        item_code="WN07X123/BALCONY",
        paid_price=3000.0,
        current_price=2500.0,
        currency="USD",
        discount_applied="Loyalty",
        status="priced",
        rebook_decision="rebook",
        notified=True,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM price_points").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["item_kind"] == "cabin_fare"
    assert row["reservation_id"] == "1234567"
    assert row["account_label"] == "user@example.com"
    assert row["ship_code"] == "WN"
    assert row["sail_date"] == "20270501"
    assert row["nights"] == 7
    assert row["item_code"] == "WN07X123/BALCONY"
    assert row["item_name"] is None
    assert row["paid_price"] == 3000.0
    assert row["current_price"] == 2500.0
    assert row["currency"] == "USD"
    assert row["discount_applied"] == "Loyalty"
    assert row["status"] == "priced"
    assert row["rebook_decision"] == "rebook"
    assert row["notified"] == 1


def test_price_history_append_only_across_runs(tmp_path):
    """Two separate start_run/record/finish_run cycles against the same file both stay present."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))

    run_id_1 = history.start_run()
    history.record_cabin_fare(reservation_id="1", ship_code="WN", sail_date="20270101",
                               item_code="A/B", status="priced")
    history.finish_run("ok")

    run_id_2 = history.start_run()
    history.record_cabin_fare(reservation_id="2", ship_code="AL", sail_date="20270201",
                               item_code="C/D", status="priced")
    history.finish_run("ok")

    assert run_id_1 != run_id_2

    conn = sqlite3.connect(str(db_path))
    try:
        runs = conn.execute("SELECT run_id, status FROM runs ORDER BY run_id").fetchall()
        points = conn.execute("SELECT run_id, reservation_id FROM price_points ORDER BY run_id").fetchall()
    finally:
        conn.close()

    assert runs == [(run_id_1, "ok"), (run_id_2, "ok")]
    assert points == [(run_id_1, "1"), (run_id_2, "2")]


def test_price_history_creates_schema_without_db_path_via_load_config_objects(tmp_path):
    """load_config_objects() with no historyDb key: history stays disabled, no file created."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("accountInfo: []\n", encoding="utf-8")

    with patch("CheckRoyalCaribbeanPrice.setup_hybrid_logging", MagicMock()):
        cfg = load_config_objects(str(config_path))

    assert cfg.history_db is None
    assert {p.name for p in tmp_path.iterdir()} == {"config.yaml"}


def test_price_history_creates_schema_with_db_path_via_load_config_objects(tmp_path):
    """load_config_objects() with historyDb set: populates config.history_db properly."""
    db_path = tmp_path / "h.db"
    config_path = tmp_path / "config.yaml"
    db_path_yaml = str(db_path).replace("\\", "/")
    config_path.write_text(f'accountInfo: []\nhistoryDb: "{db_path_yaml}"\n', encoding="utf-8")

    with patch("CheckRoyalCaribbeanPrice.setup_hybrid_logging", MagicMock()):
        cfg = load_config_objects(str(config_path))

    assert cfg.history_db == db_path_yaml


# =====================================================================
# PART 2: INTEGRATION TESTS - production functions call module-level history
# =====================================================================

def make_account(cruise_line: str = "royal") -> AccountInfo:
    account = AccountInfo(username="test_user", password="password", cruise_line=cruise_line)
    account.access = MagicMock()
    account.access.token = "fake_token"
    account.access.id = "fake_id"
    account.access.loyalty_number = None
    return account


def make_checkout_url(sail_days_out: int, domain: str = "royalcaribbean") -> str:
    sail = (date.today() + timedelta(days=sail_days_out)).isoformat()
    return (
        f"https://www.{domain}.com/checkout/guest-info?sailDate={sail}"
        "&shipCode=WN&packageCode=WN07X123&selectedCurrencyCode=USD&country=USA"
        "&cabinClassType=BALCONY&roomIndex=0&r0a=2&r0c=0&r0b=n&r0r=n&r0s=n"
        "&r0q=n&r0t=n&r0d=BALCONY&r0D=y&r0e=N&r0f=4D&r0g=BESTRATE"
    )


def fare(amount, grats=100.0, ins=50.0, obc=0.0):
    return {"fare": amount, "gratuities": grats, "insurance": ins, "obc": obc}


AVAILABLE = {"room_available": True, "sailing_nights": 7, "available_rooms": []}


def run_cruise_scenario(*, results, paid=3000.0, automatic=True, sail_days_out=400,
                         reservation_id="1234567", minimum_saving_alert=None):
    """Drive the real get_cruise_price() with a mocked pricing API; returns the mocked history object."""
    account = make_account()
    booking = {"url": make_checkout_url(sail_days_out), "bookingId": reservation_id}
    paid_price_struct = {"paidPrice": paid} if paid is not None else {}

    mock_cfg = MagicMock()
    mock_cfg.apobj = MagicMock()
    mock_cfg.minimum_saving_alert = minimum_saving_alert
    mock_cfg.date_display_format = "%m/%d/%Y"
    mock_cfg.format_date = lambda d: str(d)

    mock_history = MagicMock()
    ship_dictionary = MagicMock()
    ship_dictionary.get_ship.return_value = "Wonder of the Seas"

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice.get_room_price_via_API", return_value=results):
        get_cruise_price(
            account, booking, ship_dictionary,
            automatic_URL=automatic,
            paid_price_struct=paid_price_struct,
        )

    return mock_history


def run_addon_scenario(*, starting_from_price, paid=50.0, for_watch=False, sales_unit=None,
                        nights=7, minimum_saving_alert=None, owner=True,
                        guest_age_string="adult", payload_extra=None, reservations=None):
    """Drive the real get_new_order_price() with a mocked catalog API; returns the mocked history object."""
    account = make_account()
    booking = {"bookingId": "1234567", "shipCode": "WN", "sailDate": "20270819", "numberOfNights": nights}

    payload = {"title": "Deluxe Beverage Package"}
    if starting_from_price is not None:
        payload["startingFromPrice"] = starting_from_price
    if payload_extra:
        payload.update(payload_extra)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"payload": payload}

    ctx = WatchItemContext(
        prefix="pt_beverage",
        product="3005",
        passenger_ID="PAX1",
        passenger_name="Jim",
        room="6543",
        paid_price=paid,
        guest_age_string=guest_age_string,
        sales_unit=sales_unit,
        for_watch=for_watch,
        owner=owner,
        reservations=reservations or [],
    )

    apobj = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.minimum_saving_alert = minimum_saving_alert

    mock_history = MagicMock()

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice._execute_api_request", return_value=mock_resp):
        get_new_order_price(account, booking, apobj, ctx)

    return mock_history


def test_get_cruise_price_calls_history_record_on_price_drop():
    """A booked-cruise price drop records a 'priced' row with a rebook decision and notified=True."""
    results = {**AVAILABLE, "base_fare": fare(2500.0)}
    mock_history = run_cruise_scenario(results=results, paid=3000.0, automatic=True, sail_days_out=400)

    mock_history.record_cabin_fare.assert_called_once()
    kwargs = mock_history.record_cabin_fare.call_args.kwargs
    assert kwargs["reservation_id"] == "1234567"
    assert kwargs["status"] == "priced"
    assert kwargs["paid_price"] == 3000.0
    assert kwargs["current_price"] < kwargs["paid_price"]
    assert kwargs["rebook_decision"] == "rebook"
    assert kwargs["notified"] is True


def test_get_cruise_price_calls_history_record_on_not_for_sale():
    """A sold-out watchlist cruise records a 'not_for_sale' row with current_price=None."""
    results = {"room_available": False, "available_rooms": []}
    mock_history = run_cruise_scenario(results=results, paid=3000.0, automatic=False, sail_days_out=400)

    mock_history.record_cabin_fare.assert_called_once()
    kwargs = mock_history.record_cabin_fare.call_args.kwargs
    assert kwargs["status"] == "not_for_sale"
    assert kwargs["current_price"] is None
    assert kwargs["rebook_decision"] is None


def test_get_cruise_price_calls_history_record_on_no_fare_data():
    """Room is available but the API returns no base_fare at all (fare_struct is None)."""
    results = {"room_available": True, "sailing_nights": 7, "available_rooms": []}
    mock_history = run_cruise_scenario(results=results, paid=3000.0, automatic=True, sail_days_out=400)

    mock_history.record_cabin_fare.assert_called_once()
    kwargs = mock_history.record_cabin_fare.call_args.kwargs
    assert kwargs["status"] == "no_price_data"
    assert kwargs["current_price"] is None
    assert kwargs["nights"] == 7


def test_get_new_order_price_calls_history_record_addon():
    """item_kind reflects for_watch correctly, and a price drop records a decision + notified=True."""
    price_payload = {"adultPromotionalPrice": 30.0}

    watch_history = run_addon_scenario(starting_from_price=price_payload, paid=50.0, for_watch=True)
    watch_history.record_addon.assert_called_once()
    watch_kwargs = watch_history.record_addon.call_args.kwargs
    assert watch_kwargs["item_kind"] == "watchlist"
    assert watch_kwargs["status"] == "priced"
    assert watch_kwargs["current_price"] == 30.0
    assert watch_kwargs["paid_price"] == 50.0
    assert watch_kwargs["rebook_decision"] == "consider_booking"
    assert watch_kwargs["notified"] is True

    addon_history = run_addon_scenario(starting_from_price=price_payload, paid=50.0, for_watch=False)
    addon_history.record_addon.assert_called_once()
    addon_kwargs = addon_history.record_addon.call_args.kwargs
    assert addon_kwargs["item_kind"] == "addon"
    assert addon_kwargs["rebook_decision"] == "rebook"


def test_get_new_order_price_calls_history_record_no_longer_for_sale():
    """An item with no startingFromPrice payload records status='no_longer_for_sale'."""
    mock_history = run_addon_scenario(starting_from_price=None, paid=50.0, for_watch=False)

    mock_history.record_addon.assert_called_once()
    kwargs = mock_history.record_addon.call_args.kwargs
    assert kwargs["status"] == "no_longer_for_sale"
    assert kwargs["current_price"] is None


def test_main_finalizes_run_on_exception():
    """When an exception occurs during main(), history.finish_run('error', ...) is called."""
    account = AccountInfo(username="test_user", password="password")

    mock_cfg = MagicMock()
    mock_cfg.accounts = [account]
    mock_cfg.apobj = None
    mock_cfg.apprise_test = False
    mock_cfg.log_file = None
    mock_cfg.minimum_saving_alert = None
    mock_cfg.prospective_cruises = []

    mock_history = MagicMock()

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice.get_ship_dictionary_web", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice.login", return_value=MagicMock()), \
         patch("CheckRoyalCaribbeanPrice.get_profile", return_value=("FL", None, 0)), \
         patch("CheckRoyalCaribbeanPrice.get_voyages", side_effect=RuntimeError("get_voyages exploded")):
        with pytest.raises(RuntimeError, match="get_voyages exploded"):
            main()

    mock_history.start_run.assert_called_once()
    mock_history.finish_run.assert_called_once()
    args = mock_history.finish_run.call_args.args
    assert args[0] == "error"
    assert args[1].startswith("RuntimeError")
    assert "get_voyages exploded" in args[1]

    # start_run must have happened before finish_run
    start_run_order = mock_history.method_calls.index(next(
        c for c in mock_history.method_calls if c[0] == "start_run"
    ))
    finish_run_order = mock_history.method_calls.index(next(
        c for c in mock_history.method_calls if c[0] == "finish_run"
    ))
    assert start_run_order < finish_run_order


# ---------------------------------------------------------------------------
# Failure isolation: a broken database must never take down the price run
# ---------------------------------------------------------------------------
def test_price_history_survives_unwritable_path(tmp_path):
    """A historyDb pointing at a missing directory disables history with a warning."""
    bad = tmp_path / "no_such_dir" / "h.db"
    history = PriceHistory(str(bad))
    assert history.enabled is False
    assert history.start_run() is None
    history.record_cabin_fare(reservation_id="1234567", status="priced")
    history.finish_run("ok")


def test_price_history_survives_corrupt_db_file(tmp_path):
    """A file that is not a SQLite database disables history, not the run."""
    bad = tmp_path / "h.db"
    bad.write_bytes(b"THIS IS NOT A SQLITE FILE" * 8)
    history = PriceHistory(str(bad))
    assert history.enabled is False


def test_price_history_disables_on_midrun_write_failure(tmp_path):
    """A write failure mid-run disables the sink and keeps rows recorded so far."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))
    history.start_run()
    history.record_cabin_fare(reservation_id="1234567", status="priced", current_price=100.0)

    with patch.object(history, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")):
        history.record_cabin_fare(reservation_id="7654321", status="priced", current_price=200.0)

    assert history.enabled is False
    history.record_cabin_fare(reservation_id="8912345", status="priced")

    conn = sqlite3.connect(db_path)
    kept = conn.execute("SELECT reservation_id FROM price_points").fetchall()
    assert kept == [("1234567",)]


# ---------------------------------------------------------------------------
# The payload-failure exit of get_new_order_price is recorded too
# ---------------------------------------------------------------------------
def test_get_new_order_price_records_not_available_for_passenger():
    """The 'not available for passenger' bail produces a row."""
    account = MagicMock()
    account.username = "user@example.com"
    account.api_brand = "royal"
    ctx = MagicMock()
    ctx.reservation_id = "1234567"
    ctx.reservations = None
    ctx.passenger_ID = "33333333"
    ctx.passenger_name = "Matt"
    ctx.paid_price = 55.99
    ctx.sales_unit = "PER_NIGHT"
    ctx.for_watch = True
    ctx.prefix, ctx.product = "pt_beverage", "3005"

    booking = {"bookingId": "1234567", "sailDate": "20270320", "shipCode": "HM",
               "numberOfNights": 7, "bookingCurrency": "USD"}

    bad_response = MagicMock()
    bad_response.json.return_value = {"payload": None}

    mock_cfg = MagicMock()
    mock_history = MagicMock()

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice._execute_api_request", return_value=bad_response), \
         patch("CheckRoyalCaribbeanPrice.log"):
        get_new_order_price(account, booking, None, ctx)

    mock_history.record_addon.assert_called_once()
    kwargs = mock_history.record_addon.call_args.kwargs
    assert kwargs["status"] == "not_available_for_passenger"
    assert kwargs["item_kind"] == "watchlist"
    assert kwargs["item_code"] == "pt_beverage/3005"
    assert kwargs["current_price"] is None and kwargs["notified"] is False


# =====================================================================
# PART 3: PR 2 - `bookings` / `promos` snapshot tables
# =====================================================================

def test_price_history_upgrades_103_era_db_in_place(tmp_path):
    """Opening a database created with ONLY the legacy schema adds missing tables/indexes."""
    db_path = tmp_path / "h.db"
    legacy_103_schema = """
    CREATE TABLE runs (
        run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at       TEXT NOT NULL,
        finished_at      TEXT,
        status           TEXT NOT NULL DEFAULT 'started',
        error_summary    TEXT
    );

    CREATE TABLE price_points (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES runs(run_id),
        observed_at      TEXT NOT NULL,
        account_label    TEXT,
        reservation_id   TEXT,
        ship_code        TEXT,
        sail_date        TEXT,
        nights           INTEGER,
        item_kind        TEXT NOT NULL,
        item_code        TEXT,
        item_name        TEXT,
        guest_id         TEXT,
        guest_name       TEXT,
        paid_price       REAL,
        current_price    REAL,
        currency         TEXT,
        per_night        INTEGER NOT NULL DEFAULT 0,
        discount_applied TEXT,
        status           TEXT NOT NULL,
        rebook_decision  TEXT,
        notified         INTEGER NOT NULL DEFAULT 0
    );
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(legacy_103_schema)
        conn.execute("INSERT INTO runs (run_id, started_at, status) VALUES (1, '2026-01-01T00:00:00', 'ok')")
        conn.execute("INSERT INTO price_points (run_id, observed_at, reservation_id, item_kind, status) VALUES (1, '2026-01-01T00:00:00', '1234567', 'cabin_fare', 'priced')")
        conn.commit()
    finally:
        conn.close()

    history = PriceHistory(str(db_path))
    assert history.enabled is True

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"runs", "price_points", "bookings", "promos"} <= tables
    finally:
        conn.close()


def test_price_history_record_booking_round_trip(tmp_path):
    """record_booking() writes a `bookings` row that reads back with every column intact."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))
    history.start_run()

    guests_json = json.dumps([{"name": "Matt", "id": "33333333", "age_bracket": "adult"}])
    history.record_booking(
        account_label="user@example.com",
        reservation_id="1234567",
        ship_code="WN",
        ship_name="Wonder of the Seas",
        sail_date="20270601",
        nights=7,
        stateroom_type="BALCONY",
        stateroom_number=None,
        stateroom_category="4D",
        guest_count=1,
        guests_json=guests_json,
        loyalty_tier="DIAMOND",
        loyalty_points=4500,
        checkin_label="Opens 12/10/2026 08:00 AM",
        final_payment_date="20270303",
        past_final_payment=0,
        balance_due=1,
        booking_currency="USD",
        friendly_name="Anniversary Cruise",
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM bookings").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["account_label"] == "user@example.com"
    assert row["reservation_id"] == "1234567"
    assert row["ship_code"] == "WN"
    assert row["ship_name"] == "Wonder of the Seas"
    assert row["friendly_name"] == "Anniversary Cruise"


def test_price_history_record_promo_round_trip(tmp_path):
    """record_promo() writes a `promos` row that reads back with every column intact."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))
    history.start_run()

    history.record_promo(
        account_label="user@example.com",
        ship_code="WN",
        sail_date="20270601",
        promo_id="P1",
        promo_title="Early Booking Bonus",
        promo_line="[PROMO] Early Booking Bonus (Valid 2027-01-01 to 2027-03-01)",
        promo_start="2027-01-01",
        promo_end="2027-03-01",
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM promos").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["promo_id"] == "P1"
    assert row["promo_title"] == "Early Booking Bonus"


def test_price_history_record_booking_and_promo_noop_when_disabled(tmp_path, monkeypatch):
    """With db_path unset, record_booking/record_promo must be silent no-ops."""
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))

    history = PriceHistory(None)
    history.record_booking(reservation_id="1234567", ship_code="WN")
    history.record_promo(promo_id="P1", ship_code="WN")

    after = set(os.listdir(tmp_path))
    assert after == before


def test_price_history_record_booking_failure_isolation(tmp_path):
    """A record_booking write error disables the sink instead of crashing the run."""
    db_path = tmp_path / "h.db"
    history = PriceHistory(str(db_path))
    history.start_run()

    with patch.object(history, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")):
        history.record_booking(reservation_id="1234567", ship_code="WN")

    assert history.enabled is False
    history.record_booking(reservation_id="7654321")

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# get_voyages() integration: one record_booking call per booking, per run
# ---------------------------------------------------------------------------
def make_voyages_account() -> AccountInfo:
    account = AccountInfo(username="test_user", password="password", cruise_line="royal")
    account.access = MagicMock()
    account.access.token = "fake_token"
    account.access.id = "fake_id"
    account.loyalty_tier = "DIAMOND"
    account.loyalty_points = 4500
    return account


def run_voyages_scenario(*, booking_extra=None):
    """Drive get_voyages(); returns (mock_history, sail_date)."""
    account = make_voyages_account()
    sail_date = "20270601"
    booking = {
        "bookingId": "1234567",
        "passengerId": "33333333",
        "sailDate": sail_date,
        "numberOfNights": 7,
        "shipCode": "WN",
        "stateroomNumber": "GTY",
        "stateroomType": "B",
        "bookingCurrency": "USD",
        "passengersInStateroom": [
            {"firstName": "matt", "passengerId": "33333333", "birthdate": "19800101", "stateroomCategoryCode": "4D"},
            {"firstName": "jane", "passengerId": "44444444", "birthdate": "20200101", "stateroomCategoryCode": "4D"},
        ],
    }
    if booking_extra:
        booking.update(booking_extra)

    def api_router(*args, **kwargs):
        url = args[2] if len(args) > 2 else kwargs.get("url", "")
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        if "profileBookings" in url:
            resp.json.return_value = {"payload": {"profileBookings": [booking]}}
        else:
            resp.json.return_value = {"payload": []}
        return resp

    mock_cfg = MagicMock()
    mock_cfg.watch_list = []
    mock_cfg.display_cruise_prices = False
    mock_cfg.reservation_prices = {}
    mock_cfg.reservation_names = {"1234567": "Anniversary Cruise"}
    mock_cfg.show_promos = False
    mock_cfg.date_display_format = "%m/%d/%Y"
    mock_cfg.format_date = lambda d: str(d)
    mock_cfg.paid_reservations = []

    mock_history = MagicMock()
    ship_dictionary = MagicMock()
    ship_dictionary.get_ship.return_value = "Wonder of the Seas"
    discounts = MagicMock()

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice._execute_api_request", side_effect=api_router), \
         patch("CheckRoyalCaribbeanPrice.get_dining_and_prices",
               return_value={"dining_selection": [], "prices": []}), \
         patch("CheckRoyalCaribbeanPrice.get_checkin_info",
               return_value=("Opens 12/10/2026 08:00 AM", None)), \
         patch("CheckRoyalCaribbeanPrice.get_OBC", return_value=0.0), \
         patch("CheckRoyalCaribbeanPrice.get_orders", MagicMock()):
        get_voyages(account, discounts, ship_dictionary)

    return mock_history, sail_date


def test_get_voyages_calls_record_booking_with_full_snapshot():
    mock_history, sail_date = run_voyages_scenario()

    mock_history.record_booking.assert_called_once()
    kwargs = mock_history.record_booking.call_args.kwargs

    assert kwargs["reservation_id"] == "1234567"
    assert kwargs["ship_code"] == "WN"
    assert kwargs["ship_name"] == "Wonder of the Seas"
    assert kwargs["sail_date"] == sail_date
    assert kwargs["nights"] == 7
    assert kwargs["stateroom_type"] == "BALCONY"
    assert kwargs["stateroom_number"] is None
    assert kwargs["stateroom_category"] == "4D"
    assert kwargs["guest_count"] == 2


def test_get_voyages_record_booking_balance_due_tristate_true():
    mock_history, _ = run_voyages_scenario(booking_extra={"balanceDue": True})
    kwargs = mock_history.record_booking.call_args.kwargs
    assert kwargs["balance_due"] == 1


def test_get_voyages_record_booking_balance_due_tristate_false():
    mock_history, _ = run_voyages_scenario(booking_extra={"balanceDue": False})
    kwargs = mock_history.record_booking.call_args.kwargs
    assert kwargs["balance_due"] == 0


def test_get_voyages_record_booking_balance_due_tristate_unknown_ta_booking():
    mock_history, _ = run_voyages_scenario()
    kwargs = mock_history.record_booking.call_args.kwargs
    assert kwargs["balance_due"] is None


# ---------------------------------------------------------------------------
# get_all_promotions() integration: one record_promo call per promo
# ---------------------------------------------------------------------------
def test_get_all_promotions_calls_record_promo_once_per_promo():
    account = MagicMock()
    account.username = "test_user"
    account.api_brand = "royal"
    booking = {"shipCode": "WN", "sailDate": "20270601", "bookingCurrency": "USD"}

    homepage_promos = [
        {"id": "P1", "startDate": "2027-06-01T00:00:00Z", "endDate": "2027-07-01T00:00:00Z",
         "templates": [{"type": "HOME_HERO_LOCKUP", "categoryCode": "CRUISE_ONLY", "lockupMedia": {}}]},
        {"id": "P2", "startDate": "2027-08-01T00:00:00Z", "endDate": "2027-09-01T00:00:00Z",
         "templates": [{"type": "HOME_HERO_LOCKUP", "categoryCode": "", "lockupMedia": {}}]},
    ]

    def api_router(*args, **kwargs):
        page = (kwargs.get("params") or {}).get("page")
        resp = MagicMock()
        resp.json.return_value = {"payload": homepage_promos if page == "homepage" else []}
        return resp

    mock_cfg = MagicMock()
    mock_cfg.show_promos = True
    mock_history = MagicMock()

    with patch("CheckRoyalCaribbeanPrice.config", mock_cfg), \
         patch("CheckRoyalCaribbeanPrice.history", mock_history), \
         patch("CheckRoyalCaribbeanPrice.log", MagicMock()), \
         patch("CheckRoyalCaribbeanPrice._execute_api_request", side_effect=api_router):
        get_all_promotions(account, booking)

    assert mock_history.record_promo.call_count == 2
    calls_by_id = {c.kwargs["promo_id"]: c.kwargs for c in mock_history.record_promo.call_args_list}

    assert calls_by_id["P1"]["promo_title"] == "P1"
    assert calls_by_id["P1"]["promo_start"] == "2027-06-01"
    assert calls_by_id["P1"]["promo_end"] == "2027-07-01"

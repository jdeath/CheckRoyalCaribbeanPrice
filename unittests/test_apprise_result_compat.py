from pathlib import Path
from unittest.mock import MagicMock, Mock

import CheckRoyalCaribbeanPrice as c


URL = (
    "https://www.royalcaribbean.com/checkout/guest-info?"
    "shipCode=ST&packageCode=ST07TEST&sailDate=2099-03-28"
    "&r0d=DELUXE&r0e=IL&r0f=IL&r0a=2"
)


def test_cabin_accepts_truthy_non_bool_apprise_result(tmp_path, monkeypatch):
    cfg = c.CruiseAppConfig(
        cabin_availability_state_file=str(tmp_path / "cabin.json")
    )
    cfg.apobj = MagicMock()
    cfg.apobj.__len__.return_value = 1
    cfg.apobj.notify.return_value = object()
    monkeypatch.setattr(c, "config", cfg)
    monkeypatch.setattr(c, "log", Mock())

    params = c.parse_provided_URL(URL)
    ships = c.ShipRegistry()
    for _ in range(2):
        c.notify_cabin_availability(
            params,
            {"room_available": True},
            URL,
            ships,
            cfg.apobj,
            "scope",
        )

    assert cfg.apobj.notify.call_count == 1
    assert c.read_cabin_state(Path(cfg.cabin_availability_state_file))["scope"]["notified"] is True


def test_reservation_accepts_truthy_non_bool_apprise_result(tmp_path, monkeypatch):
    cfg = c.CruiseAppConfig()
    cfg.apobj = MagicMock()
    cfg.apobj.notify.return_value = object()
    monkeypatch.setattr(c, "config", cfg)
    monkeypatch.setattr(c, "log", Mock())
    monkeypatch.setattr(c, "log_warn", Mock())
    monkeypatch.setattr(c, "availability_notification_error", lambda *args, **kwargs: None)

    account = c.AccountInfo("user@example.invalid", "pw")
    booking = {
        "bookingId": "1234567",
        "shipCode": "IC",
        "sailDate": "20991010",
    }
    category = c.AvailabilityCategory("show", None)
    settings = c.AvailabilitySettings(
        (c.AvailabilityReservation("1234567", (category,)),),
        False,
        str(tmp_path / "reservation.json"),
    )
    result = c.AvailabilityResult(
        "SHOW1",
        "Example Show",
        "available",
        "inventory",
        ("2099-10-10T20:00:00",),
    )

    assert c.deliver_availability(
        settings, account, booking, category, False, [result]
    )
    assert c.deliver_availability(
        settings, account, booking, category, False, [result]
    )
    assert cfg.apobj.notify.call_count == 1

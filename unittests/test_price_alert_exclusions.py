"""Scoped price alert mutes use synthetic bookings and mocked catalog responses."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
import yaml

import CheckRoyalCaribbeanPrice as c


@pytest.fixture
def addon(monkeypatch):
    config = c.CruiseAppConfig()
    monkeypatch.setattr(c, 'history', Mock())
    monkeypatch.setattr(c, 'config', config)
    monkeypatch.setattr(c, 'log', Mock())
    response = Mock()
    response.json.return_value = {'payload': {
        'title': 'Example treatment',
        'startingFromPrice': {'adultPromotionalPrice': 150},
        'promoDescription': {'displayName': 'Selected times'}}}
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=response))
    account = c.AccountInfo('example@example.invalid', 'fake')
    booking = dict(bookingId='1000001', shipCode='IC', sailDate='20991010', numberOfNights=7)
    ctx = c.WatchItemContext(prefix='pt_spa', product='TEST1', passenger_ID='2000001',
        passenger_name='Example guest', room='1000', paid_price=180, guest_age_string='adult',
        for_watch=False, order_code='EXAMPLE-ORDER')
    notifier = Mock()
    return config, account, booking, ctx, notifier


def run(addon, ctx=None):
    config, account, booking, original, notifier = addon
    return c.get_new_order_price(account, booking, notifier, ctx or original)


@pytest.mark.parametrize('for_watch', [False, True])
def test_exclusion_suppresses_delivery_but_preserves_report_and_history(addon, for_watch):
    config, _, _, ctx, notifier = addon
    config.ignored_price_alerts = [c.PriceAlertExclusion('1000001', 'pt_spa', 'TEST1')]
    record = run(addon, replace(ctx, for_watch=for_watch))
    notifier.notify.assert_not_called()
    assert record['CurrentPrice'] == 150
    assert any('Example treatment Price is lower: 150 USD than 180 USD' in str(call)
               and 'suppressed by ignoredPriceAlerts' in str(call) for call in c.log.call_args_list)
    history = c.history.record_addon.call_args.kwargs
    assert history['current_price'] == 150
    assert history['rebook_decision'] == 'suppressed_by_configuration'
    assert history['notified'] is False
    assert history['discount_applied'] == 'Selected times'


@pytest.mark.parametrize('rule', [
    c.PriceAlertExclusion('1000002', 'pt_spa', 'TEST1'),
    c.PriceAlertExclusion('1000001', 'pt_dining', 'TEST1'),
    c.PriceAlertExclusion('1000001', 'pt_spa', 'TEST2'),
    c.PriceAlertExclusion('1000001', 'pt_spa', 'TEST1', '2000002'),
])
def test_unrelated_reservation_category_product_or_guest_still_alerts(addon, rule):
    addon[0].ignored_price_alerts = [rule]
    run(addon)
    addon[4].notify.assert_called_once()


def test_guest_scope_and_context_reservation_take_precedence(addon):
    config, _, _, ctx, notifier = addon
    config.ignored_price_alerts = c.parse_price_alert_exclusions([
        dict(reservation=1000002, prefix='pt_spa', product='TEST1', guest=2000001)])
    run(addon, replace(ctx, reservation_id=1000002, passenger_ID=2000001))
    notifier.notify.assert_not_called()
    run(addon, replace(ctx, reservation_id=1000002, passenger_ID=2000002))
    notifier.notify.assert_called_once()


def test_other_products_in_same_order_are_not_muted_and_removal_restores_alert(addon):
    config, _, _, ctx, notifier = addon
    config.ignored_price_alerts = [c.PriceAlertExclusion('1000001', 'pt_spa', 'TEST1')]
    run(addon)
    run(addon, replace(ctx, product='TEST2'))
    assert notifier.notify.call_count == 1
    config.ignored_price_alerts = []
    run(addon)
    assert notifier.notify.call_count == 2
    body = notifier.notify.call_args.kwargs['body']
    assert 'Rebook! Example treatment Price is lower: 150 USD than 180 USD' in body
    assert 'Promotion:Selected times' in body
    assert 'ignoredPriceAlerts' not in body


@pytest.mark.parametrize('raw', [False, 0, '', {}, 'TEST1', [None], [{}],
    [dict(reservation=1, prefix='pt_spa')],
    [dict(reservation=True, prefix='pt_spa', product='TEST1')],
    [dict(reservation=1, prefix=' ', product='TEST1')],
    [dict(reservation=1, prefix='pt_spa', product='TEST1', guest=None)],
    [dict(reservation=1, prefix='pt_spa', product='TEST1', guests=['2000001'])],
])
def test_malformed_rules_fail_instead_of_broadening_scope(raw):
    with pytest.raises(ValueError, match='ignoredPriceAlerts'):
        c.parse_price_alert_exclusions(raw)


def test_config_loads_optional_rules_and_normalizes_numeric_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'setup_hybrid_logging', Mock())
    path = tmp_path/'config.yaml'
    path.write_text('{}')
    assert c.load_config_objects(str(path)).ignored_price_alerts == []
    path.write_text(yaml.safe_dump({'ignoredPriceAlerts': [
        dict(reservation=1000001, prefix='pt_internet', product=1234, guest=2000001)]}))
    assert c.load_config_objects(str(path)).ignored_price_alerts == [
        c.PriceAlertExclusion('1000001', 'pt_internet', '1234', '2000001')]


@pytest.mark.parametrize('section', ['', 'ignoredPriceAlerts:\n', 'ignoredPriceAlerts: []\n'])
def test_empty_exclusion_section_disables_exclusions(tmp_path, monkeypatch, section):
    monkeypatch.setattr(c, 'setup_hybrid_logging', Mock())
    path = tmp_path/'config.yaml'
    path.write_text(section)
    assert c.load_config_objects(str(path)).ignored_price_alerts == []


@pytest.mark.parametrize('value', [False, 0, '', {}, 'invalid'])
def test_invalid_section_types_still_fail_config_load(tmp_path, monkeypatch, value):
    monkeypatch.setattr(c, 'setup_hybrid_logging', Mock())
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump({'ignoredPriceAlerts': value}))
    with pytest.raises(ValueError, match='ignoredPriceAlerts must be a list'):
        c.load_config_objects(str(path))

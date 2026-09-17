"""Cabin transition alerts use synthetic searches and mocked transports."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
import CheckRoyalCaribbeanPrice as c

URL = ('https://www.royalcaribbean.com/checkout/guest-info?shipCode=ST&packageCode=ST07TEST'
       '&sailDate=2099-03-28&r0d=DELUXE&r0e=IL&r0f=IL&r0a=2')


@pytest.fixture
def cabin(tmp_path, monkeypatch):
    config = c.CruiseAppConfig(cabin_availability_state_file=str(tmp_path/'state.sqlite3'))
    config.apobj = Mock()
    config.apobj.notify.return_value = True
    monkeypatch.setattr(c, 'config', config)
    monkeypatch.setattr(c, 'log', Mock())
    monkeypatch.setattr(c, '_execute_api_request', Mock(side_effect=AssertionError('Unmocked request')))
    return c.parse_provided_URL(URL), config, c.ShipRegistry()


def deliver(cabin, available, **values):
    params, config, ships = cabin
    c.notify_cabin_availability(params, dict(room_available=available, **values), URL,
                                ships, config.apobj, 'test-scope')


def test_open_close_reopen_and_unknown_transitions_survive_runs(cabin):
    _, config, _ = cabin
    for state in [False, False]:
        deliver(cabin, state)
    config.apobj.notify.assert_not_called()
    for state in [True, True, None, True]:
        deliver(cabin, state)
    assert config.apobj.notify.call_count == 1
    for state in [False, None, False, True, True]:
        deliver(cabin, state)
    assert config.apobj.notify.call_count == 2
    with sqlite3.connect(config.cabin_availability_state_file) as db:
        assert db.execute('SELECT COUNT(*) FROM cabin_availability_v1').fetchone()[0] == 1


def test_first_available_alert_is_not_a_price_threshold_check(cabin, monkeypatch):
    _, config, ships = cabin
    account = c.AccountInfo('example@example.invalid', 'fake', access=c.APIAccess(token=None, id=None, session=Mock()))
    config.minimum_saving_alert = 1000000
    monkeypatch.setattr(c, 'get_room_price_via_API', Mock(return_value={
        'room_available': True, 'base_fare': {'fare': 99999}}))
    for _ in range(2):
        c.get_cruise_price(account, {'url':URL}, ships, automatic_URL=False,
                           paid_price_struct={'paid_price':1}, notification_mode='availability')
    config.apobj.notify.assert_called_once()
    message = config.apobj.notify.call_args.kwargs
    assert message['title'] == 'Cruise Room Available'
    assert '99999.00' in message['body'] and URL in message['body']


@pytest.mark.parametrize('failure', [False, None, RuntimeError('transport')])
def test_failed_delivery_retries_without_acknowledging(cabin, failure):
    _, config, _ = cabin
    if isinstance(failure, Exception):
        config.apobj.notify.side_effect = failure
    else:
        config.apobj.notify.return_value = failure
    with pytest.raises(c.CabinAvailabilityError):
        deliver(cabin, True)
    config.apobj.notify.side_effect = None
    config.apobj.notify.return_value = True
    deliver(cabin, True)
    deliver(cabin, True)
    assert config.apobj.notify.call_count == 2


def test_unknown_first_result_does_not_create_state(cabin):
    _, config, _ = cabin
    deliver(cabin, None)
    assert not Path(config.cabin_availability_state_file).exists()
    config.apobj.notify.assert_not_called()


def test_missing_notifier_and_storage_failure_are_visible(cabin):
    _, config, _ = cabin
    config.apobj = None
    with pytest.raises(c.CabinAvailabilityError, match='not confirmed'):
        deliver(cabin, True)
    Path(config.cabin_availability_state_file).unlink()
    Path(config.cabin_availability_state_file).mkdir()
    with pytest.raises(c.CabinAvailabilityError, match='state'):
        deliver(cabin, False)


@pytest.mark.parametrize('text', ['', 'not json', '0:{"rooms":[]}', '0:{"rooms":[{}]}',
    '0:{"rooms":[{"options":{"stateroomTypes":null}}]}'])
def test_unreadable_inventory_is_unknown(cabin, monkeypatch, text):
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200,text=text)))
    assert c.check_if_room_is_available(cabin[0]) == (None, [])


@pytest.mark.parametrize('count, expected', [(0,False),(2,True),('bad',None),(-1,None),
                                          (True,None),(float('nan'),None),(float('inf'),None)])
def test_explicit_inventory_counts_are_respected(cabin, monkeypatch, count, expected):
    text = '0:' + json.dumps({'rooms':[{'options':{'stateroomTypes':[{'stateroomSubtypes':[
        {'code':'IL','roomsLeft':count}]}]}}]})
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200,text=text)))
    assert c.check_if_room_is_available(cabin[0])[0] is expected


def test_missing_price_does_not_mark_confirmed_inventory_closed(cabin, monkeypatch):
    monkeypatch.setattr(c, 'check_if_room_is_available', Mock(return_value=(True, [])))
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=None))
    result = c.get_room_price_via_API(cabin[0])
    assert result['inventory_available'] is True
    assert result['price_check_failed'] is True
    assert result['room_available'] is False
    deliver(cabin, True)
    assert 'Current price unavailable' in cabin[1].apobj.notify.call_args.kwargs['body']


def test_config_mode_is_opt_in_and_availability_needs_no_paid_price(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setattr(c, 'setup_hybrid_logging', Mock())
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump({'cruises':[{'cruiseURL':URL,'notificationMode':'availability'},
                                            {'cruiseURL':URL,'paidPrice':100}]}))
    config = c.load_config_objects(str(path))
    assert [w.notification_mode for w in config.prospective_cruises] == ['availability','price']
    path.write_text(yaml.safe_dump({'cruises':[{'cruiseURL':URL,'notificationMode':'bad'}]}))
    with pytest.raises(ValueError, match='notificationMode'):
        c.load_config_objects(str(path))


def test_guarantee_bypass_without_price_cannot_trigger_alert(cabin, monkeypatch):
    _, config, ships = cabin
    account = c.AccountInfo('example@example.invalid', 'fake', access=c.APIAccess(token=None, id=None, session=Mock()))
    monkeypatch.setattr(c, 'get_room_price_via_API', Mock(return_value={'room_available': True}))
    c.get_cruise_price(account, {'url': URL.replace('=IL', '=GTY')}, ships,
                       automatic_URL=False, notification_mode='availability')
    config.apobj.notify.assert_not_called()
    assert not Path(config.cabin_availability_state_file).exists()


def test_distinct_searches_keep_separate_state(cabin, monkeypatch):
    _, config, ships = cabin
    account = c.AccountInfo('example@example.invalid', 'fake', access=c.APIAccess(token=None, id=None, session=Mock()))
    monkeypatch.setattr(c, 'get_room_price_via_API', Mock(return_value={'room_available': True}))
    for url in (URL, URL.replace('2099-03-28', '2099-04-04'), URL):
        c.get_cruise_price(account, {'url': url}, ships, automatic_URL=False, notification_mode='availability')
    assert config.apobj.notify.call_count == 2


@pytest.mark.parametrize('count, expected', [(0, False), (3, True), ('bad', None), (float('nan'), None)])
def test_renamed_subtype_checks_stock_before_alerting(cabin, monkeypatch, count, expected):
    text = '0:' + json.dumps({'rooms': [{'options': {'stateroomTypes': [{'stateroomSubtypes': [
        {'code': 'NEW', 'categoryCode': '1IL', 'roomsLeft': count}]}]}}]})
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200, text=text)))
    assert c.check_if_room_is_available(cabin[0])[0] is expected
    assert cabin[0].stateroom_subtype == ('NEW' if expected is True else 'IL')


def test_confirmed_inventory_can_alert_after_checkout_failure_without_coupon_retry(cabin, monkeypatch):
    _, config, ships = cabin
    account = c.AccountInfo('example@example.invalid', 'fake', access=c.APIAccess(token=None, id=None, session=Mock()))
    inventory = Mock(return_value=(True, []))
    monkeypatch.setattr(c, 'check_if_room_is_available', inventory)
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=None))
    for _ in range(2):
        c.get_cruise_price(account, {'url': URL + '&r0i=EXAMPLE'}, ships,
                           automatic_URL=False, notification_mode='availability')
    assert inventory.call_count == 2
    config.apobj.notify.assert_called_once()
    assert 'Current price unavailable' in config.apobj.notify.call_args.kwargs['body']


def test_incomplete_inventory_does_not_rearm_confirmed_availability(cabin, monkeypatch):
    _, config, ships = cabin
    account = c.AccountInfo('example@example.invalid', 'fake', access=c.APIAccess(token=None, id=None, session=Mock()))
    result = {'room_available': True, 'inventory_available': True}
    monkeypatch.setattr(c, 'get_room_price_via_API', Mock(side_effect=[
        result, {'room_available': None, 'inventory_available': None}, result]))
    for _ in range(3):
        c.get_cruise_price(account, {'url': URL}, ships,
                           automatic_URL=False, notification_mode='availability')
    config.apobj.notify.assert_called_once()

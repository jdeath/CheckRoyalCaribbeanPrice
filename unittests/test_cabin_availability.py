"""Cabin transition alerts use synthetic searches and mocked transports."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
import CheckRoyalCaribbeanPrice as c

URL = ('https://www.royalcaribbean.com/checkout/guest-info?shipCode=ST&packageCode=ST07TEST'
       '&sailDate=2099-03-28&r0d=DELUXE&r0e=IL&r0f=IL&r0a=2')


@pytest.fixture
def cabin(tmp_path, monkeypatch):
    config = c.CruiseAppConfig(cabin_availability_state_file=str(tmp_path/'state.sqlite3'))
    config.apobj = MagicMock()
    config.apobj.__len__.return_value = 1
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


@pytest.mark.parametrize('empty_notifier', [False, True])
def test_console_only_does_not_acknowledge_alert_before_notifier_is_added(cabin, empty_notifier):
    _, config, _ = cabin
    notifier = config.apobj
    notifier.__len__.return_value = 0 if empty_notifier else 1
    config.apobj = notifier if empty_notifier else None
    deliver(cabin, True)
    deliver(cabin, True)
    notifier.notify.assert_not_called()
    with sqlite3.connect(config.cabin_availability_state_file) as db:
        assert db.execute('SELECT available, notified FROM cabin_availability_v1').fetchone() == (1, 0)
    config.apobj = notifier
    notifier.__len__.return_value = 1
    deliver(cabin, True)
    deliver(cabin, True)
    notifier.notify.assert_called_once()


def test_storage_failure_is_visible(cabin):
    _, config, _ = cabin
    Path(config.cabin_availability_state_file).mkdir()
    with pytest.raises(c.CabinAvailabilityError, match='state'):
        deliver(cabin, False)


@pytest.mark.parametrize('text', ['', 'not json', '0:{"rooms":[{}]}',
    '0:{"rooms":[{"options":{"stateroomTypes":null}}]}'])
def test_unreadable_inventory_is_unknown(cabin, monkeypatch, text):
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200,text=text)))
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True) == (None, [])


@pytest.mark.parametrize('count, expected', [(0,False),(2,True),('bad',None),(-1,None),
                                          (True,None),(float('nan'),None),(float('inf'),None)])
def test_explicit_inventory_counts_are_respected(cabin, monkeypatch, count, expected):
    text = '0:' + json.dumps({'rooms':[{'options':{'stateroomTypes':[{'stateroomSubtypes':[
        {'code':'IL','categoryCode':'IL','roomsLeft':count}]}]}}]})
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200,text=text)))
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True)[0] is expected


def test_missing_price_does_not_mark_confirmed_inventory_closed(cabin, monkeypatch):
    monkeypatch.setattr(c, 'check_if_room_is_available', Mock(return_value=(True, [])))
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=None))
    result = c.get_room_price_via_API(cabin[0], inventory_mode=True)
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
    cabin[0].stateroom_category_code = '1IL'
    text = '0:' + json.dumps({'rooms': [{'options': {'stateroomTypes': [{'stateroomSubtypes': [
        {'code': 'NEW', 'categoryCode': '1IL', 'roomsLeft': count}]}]}}]})
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200, text=text)))
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True)[0] is expected
    assert cabin[0].stateroom_subtype == ('IL' if expected is False else 'NEW')


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


def mock_inventory(monkeypatch, types, checkout=None):
    response = Mock(status_code=200, text='0:' + json.dumps(
        {'rooms': [{'options': {'stateroomTypes': types}}]}))
    request = Mock(side_effect=lambda **kw: response if kw['method'] == 'GET' else checkout)
    monkeypatch.setattr(c, '_execute_api_request', request)
    return request


@pytest.mark.parametrize('code', ['IL', 'NEW'])
@pytest.mark.parametrize('stock', [0, 'bad', None])
def test_price_mode_still_prices_matching_and_renamed_subtypes(cabin, monkeypatch, code, stock):
    params, _, _ = cabin
    params.stateroom_category_code = '2IL'
    checkout = Mock()
    checkout.json.return_value = {'rooms': [{'baseFare': {'pricing': {'amount': 900}}}]}
    request = mock_inventory(monkeypatch, [{'stateroomSubtypes': [
        {'code': code, 'categoryCode': '4IL', 'roomsLeft': stock}]}], checkout)
    result = c.get_room_price_via_API(params)
    assert result['room_available'] is True
    assert result['base_fare']['fare'] == 900
    post = next(call for call in request.call_args_list if call.kwargs['method'] == 'POST')
    room = json.loads(post.kwargs['data'])['rooms'][0]
    assert room['categoryCode'] == '2IL'
    assert room['stateroomSubtypeCode'] == code


@pytest.mark.parametrize('code', ['IL', 'NEW'])
@pytest.mark.parametrize('priced', [False, True])
def test_sister_category_stock_requires_checkout_confirmation(cabin, monkeypatch, code, priced):
    params, _, _ = cabin
    params.stateroom_category_code = '2IL'
    checkout = Mock() if priced else None
    if checkout is not None:
        checkout.json.return_value = {'rooms': [{'baseFare': {'pricing': {'amount': 900}}}]}
    request = mock_inventory(monkeypatch, [{'stateroomSubtypes': [
        {'code': code, 'categoryCode': '4IL', 'roomsLeft': 0}]}], checkout)
    result = c.get_room_price_via_API(params, inventory_mode=True)
    assert result['inventory_available'] is (True if priced else None)
    assert [call.kwargs['method'] for call in request.call_args_list] == ['GET', 'POST']


@pytest.mark.parametrize('bad_row', [{}, None, {'code': 123}, {'code': ''}])
@pytest.mark.parametrize('matching', [False, True])
def test_malformed_rows_do_not_hide_valid_match_or_confirm_absence(cabin, monkeypatch, bad_row, matching):
    rows = [bad_row, {'code': 'IL' if matching else 'OS',
                     'categoryCode': 'IL' if matching else 'OS', 'roomsLeft': 2}]
    mock_inventory(monkeypatch, [{'stateroomSubtypes': rows}])
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True)[0] is (True if matching else None)


@pytest.mark.parametrize('types', [[None], [{'stateroomSubtypes': [{}]}],
    [{'stateroomSubtypes': None}], [{}]])
def test_no_usable_inventory_rows_are_unknown(cabin, monkeypatch, types):
    mock_inventory(monkeypatch, types)
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True)[0] is None


@pytest.mark.parametrize('bad_type', [None, {}, {'stateroomSubtypes': None}])
def test_bad_room_type_does_not_hide_matching_type(cabin, monkeypatch, bad_type):
    mock_inventory(monkeypatch, [bad_type, {'stateroomSubtypes': [
        {'code': 'IL', 'categoryCode': 'IL', 'roomsLeft': 2}]}])
    assert c.check_if_room_is_available(cabin[0], inventory_mode=True)[0] is True


def test_raw_inventory_responses_rearm_only_on_confirmed_closure(cabin, monkeypatch):
    _, config, _ = cabin
    matching = {'rooms': [{'options': {'stateroomTypes': [{'stateroomSubtypes': [
        {'code': 'IL', 'categoryCode': 'IL', 'roomsLeft': 2}]}]}}]}
    incomplete = {'rooms': [{'options': {'stateroomTypes': [{'stateroomSubtypes': [
        {}, {'code': 'OS', 'roomsLeft': 2}]}]}}]}
    for body in [matching, {}, incomplete, matching]:
        monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200, text='0:' + json.dumps(body))))
        deliver(cabin, c.check_if_room_is_available(cabin[0], inventory_mode=True)[0])
    assert config.apobj.notify.call_count == 1
    for body in [{'rooms': []}, matching]:
        monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200, text='0:' + json.dumps(body))))
        deliver(cabin, c.check_if_room_is_available(cabin[0], inventory_mode=True)[0])
    assert config.apobj.notify.call_count == 2


@pytest.mark.parametrize('body', [{}, {'rooms': []}, {'rooms': [{}]}])
def test_price_mode_retains_parent_missing_inventory_behavior(cabin, monkeypatch, body):
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=200, text='0:' + json.dumps(body))))
    assert c.check_if_room_is_available(cabin[0])[0] is False


@pytest.mark.parametrize('section', ['', 'cruises:\n', 'cruises: []\n'])
def test_empty_cruises_section_is_disabled(tmp_path, monkeypatch, section):
    monkeypatch.setattr(c, 'setup_hybrid_logging', Mock())
    path = tmp_path/'config.yaml'
    path.write_text(section)
    assert c.load_config_objects(str(path)).prospective_cruises == []


@pytest.mark.parametrize('value', ['false', '0', '{}', '""'])
def test_invalid_cruises_section_is_rejected(tmp_path, value):
    path = tmp_path/'config.yaml'
    path.write_text('cruises: ' + value)
    with pytest.raises(ValueError, match='cruises must be a list'):
        c.load_config_objects(str(path))


def mock_main(monkeypatch, config):
    config.prospective_cruises = [c.ProspectiveCruise(url, 0, notification_mode='availability')
        for url in (URL, URL.replace('2099-03-28', '2099-04-04'))]
    monkeypatch.setattr(c, 'history', Mock())
    monkeypatch.setattr(c, 'get_ship_dictionary_web', Mock())
    session = Mock()
    monkeypatch.setattr(c, 'new_api_session', Mock(return_value=session))
    tracker = Mock()
    monkeypatch.setattr(c, 'CheckinPaymentTracker', Mock(return_value=tracker))
    return session, tracker


def test_delivery_failure_continues_remaining_watches_and_retries(cabin, monkeypatch):
    _, config, _ = cabin
    session, tracker = mock_main(monkeypatch, config)
    price = Mock(return_value={'room_available': True, 'inventory_available': True})
    monkeypatch.setattr(c, 'get_room_price_via_API', price)
    config.apobj.notify.side_effect = [False, True, True]
    with pytest.raises(SystemExit) as exc:
        c.main()
    assert exc.value.code == c.EXIT_PARTIAL_FAILURE
    assert price.call_count == 2
    session.close.assert_called_once()
    tracker.print_table.assert_called_once()
    assert c.history.finish_run.call_args.args[0] == 'partial_failure'
    with sqlite3.connect(config.cabin_availability_state_file) as db:
        assert db.execute('SELECT available, notified FROM cabin_availability_v1 ORDER BY notified').fetchall() == [(1, 0), (1, 1)]
    c.main()
    assert config.apobj.notify.call_count == 3
    assert session.close.call_count == 2
    c.history.finish_run.assert_called_with('ok')


def test_storage_failure_is_local_to_watch(cabin, monkeypatch):
    _, config, _ = cabin
    session, tracker = mock_main(monkeypatch, config)
    price = Mock(side_effect=[c.CabinAvailabilityError('Cannot update cabin availability state'), None])
    monkeypatch.setattr(c, 'get_cruise_price', price)
    with pytest.raises(SystemExit) as exc:
        c.main()
    assert exc.value.code == c.EXIT_PARTIAL_FAILURE
    assert price.call_count == 2
    session.close.assert_called_once()
    tracker.print_table.assert_called_once()


def test_console_only_main_completes_successfully(cabin, monkeypatch):
    _, config, _ = cabin
    session, tracker = mock_main(monkeypatch, config)
    config.apobj = None
    monkeypatch.setattr(c, 'get_room_price_via_API', Mock(return_value={'room_available': True}))
    c.main()
    session.close.assert_called_once()
    tracker.print_table.assert_called_once()
    c.history.finish_run.assert_called_with('ok')


def test_unexpected_error_still_closes_anonymous_session(cabin, monkeypatch):
    _, config, _ = cabin
    session, _ = mock_main(monkeypatch, config)
    monkeypatch.setattr(c, 'get_cruise_price', Mock(side_effect=RuntimeError('unexpected')))
    with pytest.raises(RuntimeError, match='unexpected'):
        c.main()
    session.close.assert_called_once()

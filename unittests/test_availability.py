"""Captured response contracts plus failure, persistence, and orchestration tests.

All fixtures are allowlisted reductions; guest/booking identifiers are synthetic.
No tests contact Royal Caribbean or send real notifications.
"""
import copy
import json
import logging
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
import CheckRoyalCaribbeanPrice as c

FIXTURES = Path(__file__).parent / 'fixtures' / 'availability'


def capture(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def party_for(data):
    return tuple((g['id'], g['reservationId']) for g in data['payload']['guests'])


def category_for(name='headliner', *, discover=False):
    data = capture(name)
    category = 'dining' if name == 'railway' else 'show'
    products = None if discover else (data['payload']['productCode'],)
    return c.AvailabilityCategory(category, products)


def evaluate(name='headliner', data=None):
    original = capture(name)
    category = category_for(name)
    return c.evaluate_availability(data if data is not None else original,
        category.category, original['payload']['productCode'], name)


def split_notifier():
    apprise = pytest.importorskip('apprise')
    real = apprise.Apprise()
    assert real.add('pover://' + 'a'*30 + '@' + 'b'*30 + '/?overflow=split')
    notifier = MagicMock(wraps=real)
    notifier.__len__.return_value = 1
    notifier.__iter__.side_effect = lambda: iter(real)
    notifier.notify.return_value = True
    return notifier


@pytest.fixture(autouse=True)
def globals_without_network(monkeypatch):
    conf = c.CruiseAppConfig()
    conf.apobj = split_notifier()
    conf.apobj.notify.return_value = True
    monkeypatch.setattr(c, 'config', conf)
    monkeypatch.setattr(c, 'history', Mock())
    monkeypatch.setattr(c, 'log', Mock())
    monkeypatch.setattr(c, 'log_warn', Mock())
    monkeypatch.setattr(c, '_execute_api_request', Mock(side_effect=AssertionError('Unmocked network call')))
    monkeypatch.setattr(c.time, 'sleep', Mock())
    return conf


@pytest.fixture
def context(tmp_path):
    account = c.AccountInfo('example@example.invalid', 'not-a-password')
    account.access = c.APIAccess('fake-token', 'fake-account', Mock())
    booking = {'bookingId': 'booking-1', 'passengerId': 'guest-1', 'shipCode': 'IC',
               'sailDate': '20991010', 'numberOfNights': 7, 'bookingCurrency': 'USD',
               'passengersInStateroom': [{'passengerId': 'guest-1'}]}
    category = category_for()
    reservation = c.AvailabilityReservation('booking-1', (category,))
    settings = c.AvailabilitySettings((reservation,), False, str(tmp_path/'state.json'))
    return account, booking, category, settings, (('guest-1', 'booking-1'),)


def test_release_ignores_personal_conflicts():
    r = evaluate()
    assert r.times == ('2098-04-06T19:15:00', '2098-04-06T21:30:00')


def test_royal_railway_contract_is_dining_and_active_flag_does_not_hide_inventory():
    data = capture('railway')
    assert data['payload']['productCode'] == 'UT_RAILDINNER'
    assert data['payload']['categoryId'] == 'pt_dining'
    assert all(offering['active'] is False for offering in data['payload']['offerings'])
    result = evaluate('railway')
    assert result.state == 'available'
    assert result.times == ('2098-07-02T18:00:00', '2098-07-02T18:10:00')


@pytest.mark.parametrize('status', ['inStock', 'outOfStock', 'OUT_OF_STOCK'])
def test_zero_inventory_does_not_alert(status):
    data = capture('headliner')
    for o in data['payload']['offerings']:
        o.update(stockLevel=0, stockLevelStatus=status)
    assert evaluate(data=data).state == 'unavailable'


def test_no_offerings_is_known_unavailable():
    data = capture('headliner')
    data['payload']['offerings'] = []
    assert evaluate(data=data).state == 'unavailable'


def test_wonder_typed_absence_and_complete_icon_catalog(context, monkeypatch):
    a,b,*_ = context
    fetch = Mock(side_effect=[capture('wonder_catalog'),capture('icon_catalog')])
    monkeypatch.setattr(c, 'availability_json', fetch)
    assert c.availability_products(a,b,'show') is None
    assert len(c.availability_products(a,b,'show')) == 6
    assert fetch.call_args.kwargs['json_data']['variables']['category'] == 'show'


def test_partial_pagination_is_unknown(context, monkeypatch):
    a,b,*_ = context
    first = capture('icon_catalog')
    first['data']['products']['pageInfo'].update(totalPages=2,totalResults=12)
    monkeypatch.setattr(c, 'availability_json', Mock(side_effect=[first,capture('wonder_catalog')]))
    with pytest.raises(c.AvailabilityUnknown):
        c.availability_products(a,b,'show')


def test_successful_multiple_pages(context, monkeypatch):
    a,b,*_ = context
    first = capture('icon_catalog')
    first['data']['products']['pageInfo'].update(totalPages=2,totalResults=12)
    second = copy.deepcopy(first)
    for p in second['data']['products']['commerceProducts']:p['id'] += '-next'
    monkeypatch.setattr(c, 'availability_json', Mock(side_effect=[first,second]))
    assert len(c.availability_products(a,b,'show')) == 12


@pytest.mark.parametrize('data', [
    {}, {'data':{'products':None}}, {'errors':[{'message':'unauthorized'}]},
    {'data':{'products':{'__typename':'CommerceProductExceptions','exceptions':[{'__typename':'InvalidCategoryException'}]}}},
])
def test_catalog_failure_is_not_absence(context, monkeypatch, data):
    a,b,*_ = context
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=data))
    with pytest.raises(c.AvailabilityUnknown):c.availability_products(a,b,'show')


def test_transport_and_graphql_errors(context, monkeypatch):
    a,*_ = context
    for response in [None, Mock(status_code=403), Mock(status_code=200, json=Mock(return_value={'errors':[{}]})),
                     Mock(status_code=200,json=Mock(side_effect=ValueError()))]:
        monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=response))
        with pytest.raises(c.AvailabilityUnknown):c.availability_json(a,'POST','https://example.invalid')


def test_eligibility_request_uses_booking_context_without_cart_mutation(context, monkeypatch):
    a,b,w,_,party = context
    response = capture('headliner')
    for o in response['payload']['offerings']:o['dateTime'] = o['dateTime'].replace('2098-04-06', '2099-10-10')
    fetch = Mock(return_value=response)
    monkeypatch.setattr(c, 'availability_json', fetch)
    c.availability_eligibility(a,b,w.category,w.products[0],party)
    assert fetch.call_args.args[1] == 'POST'
    assert fetch.call_args.args[2].endswith('/eligibility/v1/eligibility')
    body = fetch.call_args.kwargs['json_data']
    assert body['cartId'] == ''
    assert body['startDate'] == '20991010' and body['endDate'] == '20991017'
    assert body['guests'] == [{'id':'guest-1','reservationId':'booking-1'}]
    assert body['email'] == a.username


def state_result(state='available', product='Y7QG'):
    return c.AvailabilityResult(product,'Headliner',state,'test',('2099-10-10T21:30:00',) if state=='available' else ())


def deliver(context, results=None, *, notify_on_reopen=False):
    a,b,category,s,p = context
    # Generic persistence tests exercise arbitrary product IDs. Use category-wide
    # discovery here so selective filtering does not intentionally prune them.
    category = replace(category, products=None)
    return c.deliver_availability(
        s, a, b, category, notify_on_reopen,
        results if results is not None else [state_result()])


def saved_rows(context):
    a, b, category, s, p = context
    state = json.loads(Path(s.state_file).read_text())
    assert state['version'] == 2
    return state['scopes'][c.availability_scope(a, b, category.category)]


def test_first_available_notifies_once_across_state_reloads(context):
    assert deliver(context)
    assert deliver(context)
    assert c.config.apobj.notify.call_count == 1
    assert 'category/pt_show?bookingId=booking-1&shipCode=IC&sailDate=20991010' in c.config.apobj.notify.call_args.kwargs['body']


def test_dry_run_does_not_swallow_first_live_alert(context):
    a,b,w,s,p = context
    assert deliver((a,b,w,replace(s,dry_run=True),p))
    assert not Path(s.state_file).exists()
    c.config.apobj.notify.assert_not_called()
    assert deliver(context)
    c.config.apobj.notify.assert_called_once()


@pytest.mark.parametrize('failure', [False, None, RuntimeError('secret must not be logged')])
def test_delivery_failure_retries(context, failure):
    if isinstance(failure,Exception):c.config.apobj.notify.side_effect = failure
    else:c.config.apobj.notify.return_value = failure
    assert not deliver(context)
    c.config.apobj.notify.side_effect = None
    c.config.apobj.notify.return_value = True
    assert deliver(context)
    assert c.config.apobj.notify.call_count == 2


def test_no_notifier_does_not_acknowledge(context):
    saved = c.config.apobj
    c.config.apobj = None
    assert not deliver(context)
    c.config.apobj = saved
    assert deliver(context)
    saved.notify.assert_called_once()


@pytest.mark.parametrize('reopen,expected', [(False,1),(True,2)])

def test_known_closed_reopens_only_when_requested(context,reopen,expected):
    assert deliver(context, notify_on_reopen=reopen)
    assert deliver(context, [state_result('unavailable')], notify_on_reopen=reopen)
    assert deliver(context, notify_on_reopen=reopen)
    assert c.config.apobj.notify.call_count == expected


def test_unknown_never_rearms_or_changes_state(context):
    assert deliver(context, notify_on_reopen=True)
    assert not deliver(context, [state_result('unknown')], notify_on_reopen=True)
    assert saved_rows(context) == {'Y7QG': {'last_state': 'available', 'notified': True}}
    assert deliver(context, notify_on_reopen=True)
    assert c.config.apobj.notify.call_count == 1

def test_aggregate_new_products_and_skip_acknowledged_ones(context):
    first = replace(state_result(product='first'), title='First show')
    second = replace(state_result(product='second'), title='Second show')
    third = replace(state_result(product='third'), title='Third show')
    deliver(context, [first, second])
    assert c.config.apobj.notify.call_count == 1
    deliver(context, [first, second, third])
    assert c.config.apobj.notify.call_count == 2
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert 'Third show:' in body and 'First show:' not in body and 'Second show:' not in body



def test_scope_separates_accounts_sailings_and_categories(context):
    a,b,category,s,p = context
    base = c.availability_scope(a,b,category.category)
    assert base != c.availability_scope(replace(a,username='another'),b,category.category)
    assert base != c.availability_scope(a,dict(b,sailDate='20991017'),category.category)
    assert base != c.availability_scope(a,b,'dining')
    assert base == c.availability_scope(a,b,category.category)


def test_individual_product_failure_does_not_block_other_shows(context,monkeypatch):
    a,b,category,s,p = context
    discovery = replace(category, products=None)
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,)),))
    monkeypatch.setattr(c,'availability_products',Mock(return_value=[
        {'id':'first','title':'First','type':{'id':'pt_show'}},
        {'id':'Y7QG','title':'Headliner','type':{'id':'pt_show'}}]))
    monkeypatch.setattr(c,'availability_eligibility',Mock(
        side_effect=[c.AvailabilityUnknown('failed'),capture('headliner')]))
    assert c.process_availability_bookings(a,[b],settings)
    c.config.apobj.notify.assert_called_once()
    assert 'Headliner:' in c.config.apobj.notify.call_args.kwargs['body']


def test_empty_departed_and_other_bookings_make_no_product_requests(context,monkeypatch):
    a,b,category,s,p = context
    lookup = Mock(side_effect=AssertionError('must not query'))
    monkeypatch.setattr(c,'availability_products',lookup)
    assert c.process_availability_bookings(a,[b],replace(s,reservations=()))
    assert c.process_availability_bookings(a,[dict(b,sailDate='20000101')],s)
    assert c.process_availability_bookings(a,[dict(b,bookingId='another')],s)
    lookup.assert_not_called()

def valid_config():
    return {'dryRun': True, 'reservations': [{'reservation': 123, 'shows': True}]}


def test_reservation_config_models_categories_and_selective_products():
    settings = c.parse_availability_config({
        'dryRun': True,
        'reservations': [{
            'reservation': 123,
            'dining': {'products': ['UT_RAILDINNER', 'UT_CHOPDINNER']},
            'shows': True,
            'notifyOnReopen': True,
        }],
    })
    assert settings.dry_run
    assert len(settings.reservations) == 1
    reservation = settings.reservations[0]
    assert reservation.reservation == '123'
    assert reservation.notify_on_reopen is True
    assert reservation.categories == (
        c.AvailabilityCategory('dining', ('UT_RAILDINNER', 'UT_CHOPDINNER')),
        c.AvailabilityCategory('show', None),
    )


@pytest.mark.parametrize('raw, expected', [
    ({'reservations': []}, 'reservationAlerts.reservations must be a nonempty list'),
    ({'reservations': [{'reservation': '1'}]}, 'at least one of dining or shows must be enabled'),
    ({'reservations': [{'reservation': '1', 'dining': 'true'}]},
     'dining must be true, false, or a mapping'),
    ({'reservations': [{'reservation': '1', 'dining': {}}]},
     'products must be a nonempty list'),
    ({'reservations': [{'reservation': '1', 'dining': {'products': ['A', 'A']}}]},
     'products must not contain duplicates'),
    ({'reservations': [
        {'reservation': '1', 'dining': True},
        {'reservation': 1, 'shows': True},
    ]}, 'duplicate reservation'),
    ({'reservations': [{'reservation': '1', 'dining': True}], 'watches': []},
     'unrecognized configuration key'),
])
def test_invalid_reservation_config_rejected(raw, expected):
    with pytest.raises(ValueError, match=expected):
        c.parse_availability_config(raw)


def test_dining_discovery_checks_every_matching_dining_product(context, monkeypatch):
    a, b, _, s, p = context
    settings = c.parse_availability_config({
        'dryRun': True,
        'reservations': [{'reservation': b['bookingId'], 'dining': True}]
    })
    products = [
        {'id': 'dining-1', 'title': 'First restaurant', 'type': {'id': 'pt_dining'}},
        {'id': 'activity-1', 'title': 'Other activity', 'type': {'id': 'pt_onboardActivities'}},
        {'id': 'package-1', 'title': 'Dining package', 'type': {'id': 'pt_packages'}},
        {'id': 'dining-2', 'title': 'Second restaurant', 'type': {'id': 'pt_dining'}},
    ]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=products))

    def eligibility(_account, _booking, category, product, _party):
        assert category == 'dining'
        data = capture('railway')
        data['payload']['productCode'] = product
        return data

    check = Mock(side_effect=eligibility)
    monkeypatch.setattr(c, 'availability_eligibility', check)
    assert c.process_availability_bookings(a, [b], settings)
    assert [call.args[3] for call in check.call_args_list] == ['dining-1', 'dining-2']
    output = "\n".join(call.args[0] for call in c.log.call_args_list)
    assert 'Other activity' not in output
    assert 'Dining package' not in output
    assert 'skipped' not in output


def test_selective_dining_only_checks_configured_products(context, monkeypatch):
    a, b, _, _, p = context
    settings = c.parse_availability_config({
        'dryRun': True,
        'reservations': [{
            'reservation': b['bookingId'],
            'dining': {'products': ['UT_RAILDINNER']},
        }],
    })
    products = [
        {'id': 'UT_RAILDINNER', 'title': 'Royal Railway — Utopia Station',
         'type': {'id': 'pt_dining'}},
        {'id': 'UT_CHOPDINNER', 'title': 'Chops Grille', 'type': {'id': 'pt_dining'}},
    ]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=products))
    railway = capture('railway')
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=railway))
    assert c.process_availability_bookings(a, [b], settings)
    c.availability_eligibility.assert_called_once_with(
        a, b, 'dining', 'UT_RAILDINNER', p)


def test_selective_product_absence_can_rearm_after_complete_catalog(context, monkeypatch):
    a, b, _, s, p = context
    category = c.AvailabilityCategory('dining', ('UT_RAILDINNER',))
    settings = c.AvailabilitySettings((
        c.AvailabilityReservation(str(b['bookingId']), (category,), True),
    ), False, s.state_file)
    ctx = (a, b, category, settings, p)
    available = c.AvailabilityResult(
        'UT_RAILDINNER', 'Royal Railway — Utopia Station', 'available',
        'inventory', ('2099-10-10T20:30:00',))
    assert deliver(ctx, [available], notify_on_reopen=True)
    assert saved_rows(ctx)['UT_RAILDINNER']['notified'] is True

    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    assert c.process_availability_bookings(a, [b], settings)
    assert saved_rows(ctx)['UT_RAILDINNER'] == {
        'last_state': 'unavailable', 'notified': False}

    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': 'UT_RAILDINNER', 'title': 'Royal Railway — Utopia Station',
         'type': {'id': 'pt_dining'}}]))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('railway')))
    assert c.process_availability_bookings(a, [b], settings)
    assert c.config.apobj.notify.call_count == 2


def test_narrowing_category_prunes_unselected_state_without_false_closure(context):
    a, b, category, s, p = context
    discovery = replace(category, products=None)
    discovery_settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,)),))
    discovery_ctx = (a, b, discovery, discovery_settings, p)
    assert deliver(discovery_ctx, [
        state_result(product='Y7QG'),
        state_result(product='second'),
    ])
    assert set(saved_rows(discovery_ctx)) == {'Y7QG', 'second'}

    selected = replace(category, products=('Y7QG',))
    selected_settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (selected,)),))
    assert c.deliver_availability(
        selected_settings, a, b, selected, False, [state_result()],
        catalog_products={'Y7QG'})
    selected_ctx = (a, b, selected, selected_settings, p)
    assert set(saved_rows(selected_ctx)) == {'Y7QG'}
    assert c.config.apobj.notify.call_count == 1



def test_config_defaults_and_normalization():
    assert c.parse_availability_config(None) is None
    settings = c.parse_availability_config(valid_config())
    assert settings.dry_run
    assert settings.reservations[0].reservation == '123'
    assert settings.reservations[0].categories == (c.AvailabilityCategory('show'),)
    assert settings.state_file == 'data/reservation-availability.json'
    assert c.AvailabilitySettings(settings.reservations).state_file == settings.state_file

@pytest.mark.parametrize('mutation',[
    lambda d:d.update(only='false'),
    lambda d:d.update(dryRun='true'),
    lambda d:d.update(dryrun=False),
    lambda d:d.update(stateFile=':memory:'),
    lambda d:d.update(watches=[]),
    lambda d:d.update(reservations=[]),
    lambda d:d['reservations'].append(copy.deepcopy(d['reservations'][0])),
    lambda d:d['reservations'][0].update(mod='available'),
    lambda d:d['reservations'][0].update(reservation=None),
    lambda d:d['reservations'][0].update(shows='true'),
    lambda d:d['reservations'][0].update(shows={'products': []}),
    lambda d:d['reservations'][0].update(shows={'products': ['A', 'A']}),
])
def test_invalid_config_rejected(mutation):
    data = valid_config()
    mutation(data)
    with pytest.raises(ValueError):c.parse_availability_config(data)


def test_eligibility_rejects_other_sailing(context,monkeypatch):
    a,b,w,s,p = context
    monkeypatch.setattr(c,'availability_json',Mock(return_value=capture('headliner')))
    with pytest.raises(c.AvailabilityUnknown,match='outside requested sailing'):
        c.availability_eligibility(a,b,w.category,w.products[0],p)


def test_api_body_failure_status(context,monkeypatch):
    a,*_ = context
    monkeypatch.setattr(c,'_execute_api_request',Mock(return_value=Mock(status_code=200,
        json=Mock(return_value={'status':500,'payload':{}}))))
    with pytest.raises(c.AvailabilityUnknown):c.availability_json(a,'GET','https://example.invalid')



def test_catalog_failure_cannot_rearm_previously_notified_product(context,monkeypatch):
    a,b,category,s,p = context
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (category,), True),))
    assert deliver((a,b,category,settings,p), notify_on_reopen=True)
    monkeypatch.setattr(c,'availability_products',Mock(side_effect=c.AvailabilityUnknown('outage')))
    assert not c.process_availability_bookings(a,[b],settings)
    assert deliver((a,b,category,settings,p), notify_on_reopen=True)
    assert c.config.apobj.notify.call_count == 1


def test_disappeared_show_can_rearm_after_complete_empty_catalog(context,monkeypatch):
    a,b,category,s,p = context
    discovery = replace(category, products=None)
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,), True),))
    ctx = a,b,discovery,settings,p
    assert deliver(ctx, notify_on_reopen=True)
    monkeypatch.setattr(c,'availability_products',Mock(return_value=[]))
    assert c.process_availability_bookings(a,[b],settings)
    assert deliver(ctx, notify_on_reopen=True)
    assert c.config.apobj.notify.call_count == 2


def test_unwritable_state_does_not_send(context,monkeypatch):
    a,b,category,s,p = context
    monkeypatch.setattr(c,'availability_products',Mock(return_value=[
        {'id':category.products[0],'title':'Headliner','type':{'id':'pt_show'}}]))
    monkeypatch.setattr(c,'availability_eligibility',Mock(return_value=capture('headliner')))
    directory = str(Path(s.state_file).parent)
    assert not c.process_availability_bookings(a,[b],replace(s,state_file=directory))
    c.config.apobj.notify.assert_not_called()


def test_concurrent_process_cannot_send_during_read_notify_or_replace(context, monkeypatch):
    a, b, category, s, p = context
    marker = Path(s.state_file).with_suffix('.sent')
    script = '''
import json, sys
from pathlib import Path
from unittest.mock import MagicMock, Mock
import CheckRoyalCaribbeanPrice as c
data = json.loads(sys.argv[2])
a = c.AccountInfo(data['username'], 'fictional')
category_data = data['category']
category = c.AvailabilityCategory(
    category_data['category'],
    tuple(category_data['products']) if category_data['products'] is not None else None)
reservation = c.AvailabilityReservation(str(data['booking']['bookingId']), (category,))
s = c.AvailabilitySettings((reservation,), dry_run=False, state_file=sys.argv[1])
c.config = c.CruiseAppConfig()
import apprise
real = apprise.Apprise()
real.add('pover://' + 'a'*30 + '@' + 'b'*30 + '/?overflow=split')
c.config.apobj = MagicMock(wraps=real)
c.config.apobj.__len__.return_value = 1
c.log = Mock()
c.log_warn = Mock()
def send(**kwargs):
    Path(sys.argv[3]).write_text('unexpected duplicate')
    return True
c.config.apobj.notify.side_effect = send
try:
    c.deliver_availability(s, a, data['booking'], category, False,
        [c.AvailabilityResult('Y7QG', 'Headliner', 'available', 'test')])
except OSError:
    sys.exit(23)
'''
    command = [sys.executable, '-c', script, s.state_file,
               json.dumps({'username': a.username, 'booking': b, 'category': asdict(category)}), str(marker)]
    def competing_run():
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 23, result.stdout + result.stderr
        assert not marker.exists()
    read = c.read_reservation_state
    write = c.write_cabin_state
    def checked_read(path):
        competing_run()
        return read(path)
    def checked_send(**kwargs):
        competing_run()
        return True
    def checked_write(path, state):
        competing_run()
        write(path, state)
        competing_run()
    monkeypatch.setattr(c, 'read_reservation_state', checked_read)
    monkeypatch.setattr(c, 'write_cabin_state', checked_write)
    c.config.apobj.notify.side_effect = checked_send
    assert deliver(context)
    retry = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not marker.exists()
    c.config.apobj.notify.assert_called_once()

def test_booking_path_returns_snapshot_without_running_availability_early(context,monkeypatch):
    a,b,w,s,p = context
    c.config.availability=s
    monkeypatch.setattr(c,'_execute_api_request',Mock(return_value=Mock(
        json=Mock(return_value={'payload':{'profileBookings':[]}}))))
    process=Mock(return_value=True)
    monkeypatch.setattr(c,'process_availability_bookings',process)
    bookings = c.get_voyages(a,c.DiscountProfile('',None,False,False,False,False,False),c.ShipRegistry())
    assert bookings == []
    process.assert_not_called()


@pytest.mark.parametrize('dry_run', [True, False])

def test_discovery_skips_other_category_without_error_or_notification(context, monkeypatch, dry_run):
    a, b, category, s, p = context
    category = replace(category, products=None)
    reservation = c.AvailabilityReservation(str(b['bookingId']), (category,))
    settings = replace(s, reservations=(reservation,), dry_run=dry_run)
    products = [
        {'id': 'escape-1', 'title': 'Escape room', 'type': {'id': 'pt_onboardActivities'}},
        {'id': 'dinner-1', 'title': 'Experience dinner', 'type': {'id': 'pt_dining'}},
    ]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=products))
    eligibility = Mock(side_effect=AssertionError('must not query another product type'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    assert c.process_availability_bookings(a, [b], settings)
    eligibility.assert_not_called()
    c.config.apobj.notify.assert_not_called()
    output = "\n".join(call.args[0] for call in c.log.call_args_list)
    assert 'Escape room' not in output
    assert 'Experience dinner' not in output
    assert 'skipped' not in output
    if dry_run:
        assert not Path(s.state_file).exists()


def test_mixed_catalog_still_checks_and_notifies_matching_show(context, monkeypatch):
    a, b, category, s, p = context
    product = category.products[0]
    products = [
        {'id': 'escape-1', 'title': 'Escape room', 'type': {'id': 'pt_onboardActivities'}},
        {'id': product, 'title': 'Headliner', 'type': {'id': 'pt_show'}},
    ]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=products))
    eligibility = Mock(return_value=capture('headliner'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    discovery = replace(category, products=None)
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,)),))
    assert c.process_availability_bookings(a, [b], settings)
    eligibility.assert_called_once_with(a, b, 'show', product, p)
    c.config.apobj.notify.assert_called_once()
    assert 'Headliner:' in c.config.apobj.notify.call_args.kwargs['body']
    assert 'Escape room' not in c.config.apobj.notify.call_args.kwargs['body']


def test_explicit_product_type_mismatch_stays_unknown(context, monkeypatch):
    a, b, category, s, p = context
    product = category.products[0]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': product, 'title': 'Other type', 'type': {'id': 'pt_activity'}}]))
    eligibility = Mock(side_effect=AssertionError('must not query another product type'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    assert not c.process_availability_bookings(a, [b], s)
    eligibility.assert_not_called()
    c.config.apobj.notify.assert_not_called()

@pytest.mark.parametrize('product_type', [None, {}, [], 'pt_show', {'id': None}, {'id': 'pt_'}, {'id': 'invalid'}])

def test_malformed_type_does_not_block_valid_show_or_hide_error(context, monkeypatch, product_type):
    a, b, category, s, p = context
    product = category.products[0]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': 'broken', 'title': 'Malformed', 'type': product_type},
        {'id': product, 'title': 'Headliner', 'type': {'id': 'pt_show'}}]))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('headliner')))
    discovery = replace(category, products=None)
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,)),))
    assert c.process_availability_bookings(a, [b], settings)
    c.config.apobj.notify.assert_called_once()
    assert 'Headliner:' in c.config.apobj.notify.call_args.kwargs['body']


def test_skipped_type_change_preserves_previous_notification(context, monkeypatch):
    a, b, category, s, p = context
    discovery = replace(category, products=None)
    settings = replace(s, reservations=(
        c.AvailabilityReservation(str(b['bookingId']), (discovery,), True),))
    ctx = a, b, discovery, settings, p
    assert deliver(ctx, notify_on_reopen=True)
    original = Path(s.state_file).read_bytes()
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': 'Y7QG', 'title': 'Changed type', 'type': {'id': 'pt_activity'}}]))
    assert c.process_availability_bookings(a, [b], settings)
    assert Path(s.state_file).read_bytes() == original
    assert deliver(ctx, notify_on_reopen=True)
    assert c.config.apobj.notify.call_count == 1

def setup_combined_console(context, monkeypatch):
    from types import SimpleNamespace
    a, b, category, s, p = context
    c.config.availability = s
    c.config.accounts = [a]
    c.config.prospective_cruises = [
        SimpleNamespace(cruise_URL='https://example.invalid', paid_price=100)]
    monkeypatch.setattr(c, 'login', Mock(side_effect=lambda account: account.access))
    monkeypatch.setattr(c, 'get_profile', Mock(return_value=('OH', '', 0)))
    monkeypatch.setattr(c, 'get_ship_dictionary_web', Mock())
    monkeypatch.setattr(c, 'new_api_session', Mock(return_value=Mock()))
    monkeypatch.setattr(c.time, 'sleep', Mock())
    return a, b


def test_availability_output_groups_results_under_sailing(context, monkeypatch):
    a, b, category, s, p = context
    c.config.date_display_format = "%m/%d/%Y"
    booking = dict(b, shipName="Icon of the Seas")
    product = category.products[0]
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': product, 'title': 'Headliner', 'type': {'id': 'pt_show'}},
    ]))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('headliner')))
    assert c.process_availability_bookings(a, [booking], replace(s, dry_run=True))

    lines = [call.args[0] for call in c.log.call_args_list]
    account_line = next(i for i, line in enumerate(lines) if 'Royal Caribbean for user' in line)
    sailing_line = next(i for i, line in enumerate(lines) if 'ICON OF THE SEAS (10/10/2099)' in line)
    category_line = next(i for i, line in enumerate(lines) if 'Shows' in line)
    product_line = next(i for i, line in enumerate(lines) if 'Headliner: Available' in line)
    assert account_line < sailing_line < category_line < product_line
    assert lines[sailing_line].startswith('    ')
    assert lines[category_line].startswith('      ')
    assert lines[product_line].startswith('        ')

def test_availability_sailing_label_falls_back_to_ship_code(context):
    _, booking, *_ = context
    c.config.date_display_format = "%m/%d/%Y"
    assert c.availability_sailing_label(booking) == "IC (10/10/2099)"

def test_availability_notification_hides_apprise_info_chatter_but_keeps_warnings(context, caplog):
    chatter = "Sent Pushover notification to ALL_DEVICES."
    warning = "Pushover delivery warning"
    notifier = split_notifier()

    def notify(**kwargs):
        logging.getLogger("apprise").info(chatter)
        logging.getLogger("apprise").warning(warning)
        return True

    notifier.notify.side_effect = notify
    c.config.apobj = notifier
    with caplog.at_level(logging.INFO):
        assert deliver(context)

    assert chatter not in caplog.text
    assert warning in caplog.text

def test_availability_console_uses_status_colors_and_groups_times(context):
    a, b, w, s, p = context
    results = [c.AvailabilityResult('show', 'Show', 'available', 'inventory',
               ('2099-10-10T19:15:00', '2099-10-10T21:30:00')),
               c.AvailabilityResult('closed', 'Closed', 'unavailable', 'no offerings'),
               c.AvailabilityResult('error', 'Error', 'unknown', 'request failed')]
    assert c.deliver_availability(replace(s, dry_run=True), a, b, w, False, results)
    lines = [call.args[0] for call in c.log.call_args_list]
    assert any(c.GREEN + 'Show: Available' in line for line in lines)
    assert any(c.YELLOW + 'Closed: Unavailable' in line for line in lines)
    assert any(c.RED + 'Error: Unknown' in line for line in lines)
    assert any('19:15, 21:30' in line and line.startswith('      ') for line in lines)
    assert not any('2099-10-10T' in line for line in lines)



def test_catalog_fetched_once_per_category_per_run_and_later_runs_are_fresh(context, monkeypatch):
    a, b, category, s, p = context
    settings = replace(s, dry_run=True)
    catalog = Mock(return_value=[
        {'id': category.products[0], 'title': 'Show', 'type': {'id': 'pt_show'}}])
    monkeypatch.setattr(c, 'availability_products', catalog)
    eligibility = Mock(return_value=capture('headliner'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    for _ in range(2):
        assert c.process_availability_bookings(a, [b], settings)
    assert catalog.call_count == 2
    assert eligibility.call_count == 2


def test_catalog_failure_is_retried_next_run(context, monkeypatch):
    a, b, category, s, p = context
    assert deliver(context)
    catalog = Mock(side_effect=[c.AvailabilityUnknown('temporary error'), []])
    monkeypatch.setattr(c, 'availability_products', catalog)
    assert not c.process_availability_bookings(a, [b], s)
    assert catalog.call_count == 1
    assert deliver(context)
    c.config.apobj.notify.assert_called_once()
    assert c.process_availability_bookings(a, [b], s)
    assert catalog.call_count == 2


def test_reservations_and_categories_fetch_independently(context, monkeypatch):
    a, b, category, s, p = context
    dining = c.AvailabilityCategory('dining')
    settings = c.AvailabilitySettings((
        c.AvailabilityReservation('booking-1', (replace(category, products=None), dining)),
        c.AvailabilityReservation('booking-2', (replace(category, products=None),)),
    ), True, s.state_file)
    other_booking = dict(b, bookingId='booking-2')
    catalog = Mock(return_value=[])
    monkeypatch.setattr(c, 'availability_products', catalog)
    for account in (a, replace(a, username='other@example.invalid')):
        assert c.process_availability_bookings(account, [b, other_booking], settings)
    assert catalog.call_count == 6

@pytest.mark.parametrize('change, expected', [
    (lambda d:d.update(dryrun=True), 'reservationAlerts: unrecognized configuration key(s): dryrun'),
    (lambda d:d['reservations'][0].update(mod='release'),
     'reservationAlerts.reservations[0]: unrecognized configuration key(s): mod'),
    (lambda d:d['reservations'][0].update(shows='false'),
     'reservationAlerts.reservations[0]: shows must be true, false, or a mapping'),
    (lambda d:d['reservations'][0].update(shows={'guests':[{'id':'SECRET_VALUE'}]}),
     'reservationAlerts.reservations[0].shows: unrecognized configuration key(s): guests'),
    (lambda d:d['reservations'][0].update(reservation=None),
     'reservationAlerts.reservations[0]: reservation must be a nonempty identifier'),
])
def test_config_diagnostics_identify_location_without_echoing_values(change, expected):
    raw = valid_config()
    change(raw)
    with pytest.raises(ValueError) as exc:
        c.parse_availability_config(raw)
    assert str(exc.value) == expected
    assert 'SECRET_VALUE' not in str(exc.value)


def test_http_status_diagnostic_does_not_expose_response_body(context, monkeypatch):
    a, *_ = context
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=Mock(status_code=401,
        json=Mock(return_value={'token':'DO_NOT_LOG'}))))
    with pytest.raises(c.AvailabilityUnknown) as exc:
        c.availability_json(a, 'GET', 'https://example.invalid')
    assert str(exc.value) == 'Royal API returned HTTP 401'


def test_missing_notifier_diagnostic_remains_retryable(context):
    c.config.apobj = None
    assert not deliver(context)
    assert any('No notification service configured' in call.args[0] for call in c.log_warn.call_args_list)
    c.config.apobj = split_notifier()
    c.config.apobj.notify.return_value = True
    assert deliver(context)
    c.config.apobj.notify.assert_called_once()


def test_state_storage_diagnostic_is_actionable_and_does_not_expose_exception(context, monkeypatch):
    a, b, w, s, p = context
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    monkeypatch.setattr(c, 'deliver_availability', Mock(side_effect=PermissionError('PRIVATE_PATH')))
    assert not c.process_availability_bookings(a, [b], s)
    messages = '\n'.join(call.args[0] for call in c.log_warn.call_args_list)
    assert 'check reservationAlerts.stateFile and directory permissions' in messages
    assert 'PRIVATE_PATH' not in messages


def test_compact_alert_groups_dates_separates_shows_and_keeps_one_booking_link(context):
    a, b, w, s, p = context
    c.config.date_display_format = '%Y-%m-%d'
    results = [c.AvailabilityResult('one', 'Comedy', 'available', 'inventory',
               ('2099-10-10T20:30:00-04:00', '2099-10-10T22:30:00-04:00', '2099-10-11T19:00:00-04:00')),
               c.AvailabilityResult('two', 'Ice Show', 'available', 'inventory', ('2099-10-12T21:15:00',))]
    assert c.deliver_availability(s, a, b, w, False, results)
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert '\n\nComedy:\n2099-10-10: 20:30, 22:30\n2099-10-11: 19:00' in body
    assert '\n\nIce Show:\n2099-10-12: 21:15' in body
    assert body.count('https://') == 1 and '/product/' not in body
    assert 'pt_show?bookingId=booking-1&shipCode=IC&sailDate=20991010' in body
    assert 'Inventory released; personal conflicts not checked.' in body
    assert 'Times as returned by Royal.' in body
    assert 'T20:30' not in body and '20:30:00' not in body


def test_console_and_alert_limit_preview_without_modifying_inventory(context):
    a, b, w, s, p = context
    times = tuple(f'2099-10-10T{hour:02d}:00:00' for hour in range(10, 18))
    assert c.deliver_availability(s, a, b, w, False,
        [c.AvailabilityResult('one', 'Show', 'available', 'inventory', times)])
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert '(+2 more times in Cruise Planner)' in body
    assert '15:00' in body and '16:00' not in body
    output = '\n'.join(call.args[0] for call in c.log.call_args_list)
    assert '16:00' not in output and '17:00' not in output
    assert '8 available times across 1 day(s); showing the first 6' in output
    assert len(times) == 8


def test_compact_dining_alert_retains_party_and_table_caveats(context):
    a, b, w, s, p = context
    w = replace(w, category='dining')
    assert c.deliver_availability(s, a, b, w, False, [state_result()])
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert 'personal conflicts not checked' in body
    assert 'pt_dining?' in body and 'pt_show' not in body
    assert 'Reported stock does not guarantee a table for the full party.' in body


def test_native_apprise_split_preserves_all_shows_and_retries_failed_delivery(context):
    apprise = pytest.importorskip('apprise')
    a, b, w, s, p = context
    w = replace(w, products=None)
    notifier = apprise.Apprise()
    assert notifier.add('pover://' + 'a'*30 + '@' + 'b'*30 + '/?overflow=split')
    service = next(iter(notifier))
    # Replace the transport, so no real notifications or network calls are possible.
    service.send = Mock(return_value=True)
    c.config.apobj = notifier
    results = [c.AvailabilityResult(str(i), f'Show {i:02d} with an example title', 'available', 'inventory',
                                   ('2099-10-10T20:30:00', '2099-10-11T22:30:00')) for i in range(30)]
    service.send.side_effect = lambda **kwargs: 'Show 00' not in kwargs['body']
    assert not c.deliver_availability(s, a, b, w, False, results)
    assert all(row['notified'] is False for row in saved_rows(context).values())
    service.send.reset_mock()
    service.send.side_effect = None
    assert c.deliver_availability(s, a, b, w, False, results)
    chunks = [call.kwargs['body'] for call in service.send.call_args_list]
    assert len(chunks) > 1
    assert all(len(chunk) <= service.body_maxlen for chunk in chunks)
    delivered = '\n'.join(chunks)
    assert all(f'Show {i:02d}' in delivered for i in range(30))
    assert 'category/pt_show?' in delivered
    service.send.reset_mock()
    assert c.deliver_availability(s, a, b, w, False, results)
    service.send.assert_not_called()


@pytest.mark.parametrize('contents', [
    b'', b'not json', b'null', b'[]', b'{}', b'{', b'\xff', b'SQLite format 3\x00',
    b'{"version":2,"watches":{}}',
    b'{"version":1,"scopes":{}}', b'{"version":true,"scopes":{}}',
    b'{"version":2,"scopes":[]}', b'{"version":2,"scopes":{},"unexpected":0}',
    b'{"version":2,"scopes":{"scope":null}}',
    b'{"version":2,"scopes":{"":{"product":{"last_state":"available","notified":true}}}}',
    b'{"version":2,"scopes":{"scope":{"":{}}}}',
    b'{"version":2,"scopes":{"scope":{"product":null}}}',
    b'{"version":2,"scopes":{"scope":{"product":{"last_state":"unknown","notified":true}}}}',
    b'{"version":2,"scopes":{"scope":{"product":{"last_state":"available","notified":1}}}}',
    b'{"version":2,"scopes":{"scope":{"product":{"last_state":"available","notified":"false"}}}}',
    b'{"version":2,"scopes":{"scope":{"product":{"last_state":"available"}}}}',
])
def test_invalid_json_state_is_preserved_without_sending(context, contents):
    path = Path(context[3].state_file)
    path.write_bytes(contents)
    with pytest.raises(c.AvailabilityUnknown, match='JSON state'):
        deliver(context)
    assert path.read_bytes() == contents
    c.config.apobj.notify.assert_not_called()


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
def test_failed_json_save_preserves_previous_acknowledgements_and_retries(context, monkeypatch, operation):
    assert deliver(context)
    path = Path(context[3].state_file)
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(c.os, operation, Mock(side_effect=OSError('disk error')))
        with pytest.raises(OSError):
            deliver(context, [state_result(product='second')])
    assert path.read_bytes() == before
    assert not list(path.parent.glob('*.tmp'))
    # Delivery before a failed save may repeat, but previously saved alerts do not.
    assert deliver(context, [state_result(), state_result(product='second')])
    assert c.config.apobj.notify.call_count == 3
    assert saved_rows(context)['second']['notified'] is True
    assert deliver(context, [state_result(), state_result(product='second')])
    assert c.config.apobj.notify.call_count == 3


def test_unknown_and_unchanged_json_state_are_not_rewritten(context, monkeypatch):
    assert deliver(context)
    write = Mock(side_effect=AssertionError('unexpected write'))
    monkeypatch.setattr(c, 'write_cabin_state', write)
    assert not deliver(context, [state_result('unknown')])
    assert deliver(context)
    write.assert_not_called()
    c.config.apobj.notify.assert_called_once()



def test_json_keeps_independent_category_scopes(context):
    a, b, category, s, p = context
    dining = replace(category, category='dining')
    contexts = [
        context,
        (replace(a, username='second@example.invalid'), b, category, s, p),
        (a, dict(b, sailDate='20991017'), category, s, p),
        (a, b, dining, s, p),
        (a, dict(b, bookingId='different'), category, s, p),
    ]
    for ctx in contexts:
        assert deliver(ctx)
    for ctx in contexts:
        assert deliver(ctx)
        assert saved_rows(ctx)['Y7QG']['notified'] is True
    assert c.config.apobj.notify.call_count == len(contexts)
    state = json.loads(Path(s.state_file).read_text())
    assert len(state['scopes']) == len(contexts)
    assert a.username in Path(s.state_file).read_text()
    assert a.password not in Path(s.state_file).read_text()
    assert a.access.token not in Path(s.state_file).read_text()
    assert b['bookingId'] in Path(s.state_file).read_text()


def test_explicit_reset_rearms_only_selected_product(context):
    assert deliver(context, [state_result(), state_result(product='second')])
    a, b, category, s, p = context
    path = Path(s.state_file)
    state = json.loads(path.read_text())
    state['scopes'][c.availability_scope(a, b, category.category)]['Y7QG']['notified'] = False
    path.write_text(json.dumps(state))
    assert deliver(context, [state_result(), state_result(product='second')])
    assert c.config.apobj.notify.call_count == 2
    assert saved_rows(context)['second']['notified'] is True

def test_lock_contention_reports_failure_without_changing_state(context, monkeypatch):
    a, b, w, s, p = context
    assert deliver(context)
    before = Path(s.state_file).read_bytes()
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    with c.cabin_state_lock(Path(s.state_file)):
        assert not c.process_availability_bookings(a, [b], s)
    assert Path(s.state_file).read_bytes() == before
    c.config.apobj.notify.assert_called_once()
    messages = '\n'.join(call.args[0] for call in c.log_warn.call_args_list)
    assert 'overlapping checks' in messages


@pytest.mark.parametrize('name', ['elemental', 'railway'])
def test_existing_reservations_and_conflicts_do_not_hide_released_inventory(name):
    assert evaluate(name).state == 'available'


@pytest.mark.parametrize('field,value', [
    ('stockLevel', True), ('stockLevel', -1), ('stockLevel', float('nan')),
    ('stockLevel', float('inf')), ('stockLevel', None), ('stockLevel', 10000),
    ('stockLevelStatus', 'NEW_STATUS'), ('dateTime', 'tomorrow'),
    ('id', None),
])
def test_malformed_inventory_cannot_become_closed_or_available(field, value):
    data = capture('headliner')
    for offering in data['payload']['offerings']:
        offering[field] = value
    assert evaluate(data=data).state == 'unknown'


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(error={'message':'failure'}),
    lambda d: d.update(warnings=['incomplete']),
    lambda d: d['payload'].update(productCode='wrong'),
    lambda d: d['payload'].update(categoryId='pt_dining'),
    lambda d: d['payload'].update(offerings=None),
    lambda d: d['payload']['offerings'].append(copy.deepcopy(d['payload']['offerings'][0])),
])
def test_mismatched_or_incomplete_eligibility_is_unknown(mutation):
    data = capture('headliner')
    mutation(data)
    assert evaluate(data=data).state == 'unknown'


@pytest.mark.parametrize('failure', ['unknown', 'missing', 'lookup', 'state'])
def test_release_failure_finishes_price_outputs_closes_sessions_and_sets_partial_failure(context, monkeypatch, failure):
    a, b = setup_combined_console(context, monkeypatch)
    second = replace(a, username='second@example.invalid', access=c.APIAccess('fake', 'second', Mock()))
    c.config.accounts.append(second)
    bookings = Mock(side_effect=[None if failure == 'lookup' else ([] if failure == 'missing' else [b]), []])
    monkeypatch.setattr(c, 'get_voyages', bookings)
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    if failure == 'state':
        monkeypatch.setattr(c, 'read_reservation_state', Mock(side_effect=OSError('disk')))
    elif failure == 'unknown':
        monkeypatch.setattr(c, 'availability_products', Mock(side_effect=c.AvailabilityUnknown('unavailable API')))
    prices = Mock()
    monkeypatch.setattr(c, 'get_cruise_price', prices)
    tracker = Mock()
    monkeypatch.setattr(c, 'CheckinPaymentTracker', Mock(return_value=tracker))
    c.config.output_watch_as_json = True
    output = Mock()
    monkeypatch.setattr(c, 'write_watch_price_json', output)
    with pytest.raises(SystemExit) as error:
        c.main()
    assert error.value.code == c.EXIT_PARTIAL_FAILURE
    assert bookings.call_count == 2
    prices.assert_called_once()
    tracker.print_table.assert_called_once()
    output.assert_called_once()
    a.access.session.close.assert_called_once()
    second.access.session.close.assert_called_once()
    assert c.history.finish_run.call_args.args[0] == 'partial_failure'


@pytest.mark.parametrize('enabled', [False, True])
def test_disabled_release_checks_make_no_extra_requests(context, monkeypatch, enabled):
    a, b = setup_combined_console(context, monkeypatch)
    c.config.availability = replace(context[3], reservations=()) if enabled else None
    monkeypatch.setattr(c, 'get_voyages', Mock(return_value=[b]))
    monkeypatch.setattr(c, 'get_cruise_price', Mock())
    release = Mock(side_effect=AssertionError('disabled feature called'))
    monkeypatch.setattr(c, 'process_availability_bookings', release)
    c.main()
    release.assert_not_called()
    c.history.finish_run.assert_called_once_with('ok')

def test_booking_watch_resolves_across_accounts(context, monkeypatch):
    a, b = setup_combined_console(context, monkeypatch)
    second = replace(a, username='second@example.invalid', access=c.APIAccess('fake', 'second', Mock()))
    c.config.accounts.append(second)
    monkeypatch.setattr(c, 'get_voyages', Mock(side_effect=[[], [b]]))
    monkeypatch.setattr(c, 'get_cruise_price', Mock())
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    c.main()
    c.history.finish_run.assert_called_once_with('ok')


def test_main_release_flow_reuses_session_and_bookings_then_suppresses_duplicate(context, monkeypatch):
    a, b = setup_combined_console(context, monkeypatch)
    b = dict(b, sailDate='20980406')
    c.config.prospective_cruises = []
    monkeypatch.setattr(c, 'get_voyages', Mock(return_value=[b]))
    catalog = capture('icon_catalog')
    products = catalog['data']['products']
    products['commerceProducts'] = [p for p in products['commerceProducts'] if p['id'] == context[2].products[0]]
    products['pageInfo'].update(totalPages=1, totalResults=1)
    calls = []
    def transport(account, method, url, **kwargs):
        assert account is a and method == 'POST'
        calls.append(url)
        if url.endswith('/graphql'):
            assert kwargs['json_data']['variables']['reservationId'] == b['bookingId']
            answer = catalog
        elif url.endswith('/eligibility/v1/eligibility'):
            assert kwargs['json_data']['cartId'] == ''
            assert kwargs['json_data']['guests'] == [{'id':'guest-1', 'reservationId':'booking-1'}]
            answer = capture('headliner')
        else:
            raise AssertionError('Unexpected endpoint')
        return Mock(status_code=200, json=Mock(return_value=answer))
    monkeypatch.setattr(c, '_execute_api_request', transport)
    c.main()
    c.main()
    assert len(calls) == 4
    assert c.login.call_count == 2 and c.get_voyages.call_count == 2
    assert a.access.session.close.call_count == 2
    c.config.apobj.notify.assert_called_once()
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert '19:15' in body and '21:30' in body


@pytest.mark.parametrize('selected', [False, True])
def test_incomplete_catalog_checks_returned_restaurants_without_rearming_absent_products(context, monkeypatch, selected):
    a, b, _, s, p = context
    category = c.AvailabilityCategory('dining', ('DINE00', 'missing') if selected else None)
    s = replace(s, reservations=(c.AvailabilityReservation(b['bookingId'], (category,), True),))
    ctx = (a, b, category, s, p)
    assert deliver(ctx, [state_result(product='missing')])
    c.config.apobj.notify.reset_mock()
    products = [{'id': f'DINE{i:02d}', 'title': f'Restaurant {i:02d}',
                 'type': {'id': 'pt_dining'}} for i in range(27)]
    pages = [{'data': {'products': {'__typename': 'CommerceProductResultSuccess',
              'commerceProducts': products[i:i+12],
              'pageInfo': {'totalPages': 3, 'totalResults': 28}}}} for i in range(0, 27, 12)]
    catalog = Mock(side_effect=pages * 2)
    monkeypatch.setattr(c, 'availability_json', catalog)
    def eligibility(account, booking, category_name, product, party):
        return {'status': 200, 'payload': {'productCode': product, 'categoryId': 'pt_dining',
                'offerings': [{'id': product + '-offering', 'dateTime': '2099-10-10T18:00:00',
                               'stockLevelStatus': 'inStock', 'stockLevel': 10}]}}
    eligibility_mock = Mock(side_effect=eligibility)
    monkeypatch.setattr(c, 'availability_eligibility', eligibility_mock)
    for _ in range(2):
        assert c.process_availability_bookings(a, [b], s)  # coverage warning, returned products succeed
        assert saved_rows(ctx)['missing'] == {'last_state': 'available', 'notified': True}
        assert saved_rows(ctx)['DINE00']['notified'] is True
    assert catalog.call_count == 6
    assert eligibility_mock.call_count == (2 if selected else 54)
    c.config.apobj.notify.assert_called_once()  # second incomplete read does not repeat
    assert 'Restaurant 00' in c.config.apobj.notify.call_args.kwargs['body']
    assert 'incomplete catalog' in '\n'.join(call.args[0].lower() for call in c.log_warn.call_args_list)


@pytest.mark.parametrize('later_page', [
    c.AvailabilityUnknown('request failed'),
    {'data': {'products': []}},
    {'data': {'products': {'__typename': 'CommerceProductResultSuccess',
        'commerceProducts': [{'id': 'first', 'type': {'id': 'pt_show'}}],
        'pageInfo': {'totalPages': 2, 'totalResults': 2}}}},
])
def test_later_catalog_failure_retains_verified_products(context, monkeypatch, later_page):
    a, b, *_ = context
    first = {'id': 'first', 'type': {'id': 'pt_show'}}
    page = {'data': {'products': {'__typename': 'CommerceProductResultSuccess',
            'commerceProducts': [first], 'pageInfo': {'totalPages': 2, 'totalResults': 2}}}}
    monkeypatch.setattr(c, 'availability_json', Mock(side_effect=[page, later_page]))
    with pytest.raises(c.AvailabilityCatalogIncomplete) as failure:
        c.availability_products(a, b, 'show')
    assert failure.value.products == [first]


@pytest.mark.parametrize('selected', [False, True])
def test_failed_first_catalog_page_preserves_acknowledgements(context, monkeypatch, selected):
    a, b, category, s, p = context
    category = replace(category, products=category.products if selected else None)
    s = replace(s, reservations=(c.AvailabilityReservation(b['bookingId'], (category,), True),))
    ctx = (a, b, category, s, p)
    assert deliver(ctx)
    before = Path(s.state_file).read_bytes()
    monkeypatch.setattr(c, 'availability_json', Mock(side_effect=c.AvailabilityUnknown('request failed')))
    eligibility = Mock(side_effect=AssertionError('No products returned'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    assert not c.process_availability_bookings(a, [b], s)
    eligibility.assert_not_called()
    assert Path(s.state_file).read_bytes() == before
    c.config.apobj.notify.assert_called_once()


def real_pushover_notifier(overflow=None):
    apprise = pytest.importorskip('apprise')
    notifier = apprise.Apprise()
    url = 'pover://' + 'a'*30 + '@' + 'b'*30 + '/'
    assert notifier.add(url + ('?overflow=' + overflow if overflow else ''))
    service = next(iter(notifier))
    service.send = Mock(side_effect=AssertionError('Unmocked notification delivery'))
    return notifier, service


@pytest.mark.parametrize('overflow', [None, 'upstream', 'truncate'])
def test_unsafe_overflow_never_sends_or_acknowledges_and_recovers_after_config_fix(context, overflow):
    notifier, service = real_pushover_notifier(overflow)
    c.config.apobj = notifier
    results = [replace(state_result(product=str(i)), title=f'Restaurant {i:02d}',
                       times=('2099-10-10T18:00:00', '2099-10-10T18:30:00',
                              '2099-10-11T18:00:00')) for i in range(20)]
    assert not deliver(context, results)
    service.send.assert_not_called()
    assert all(not row['notified'] for row in saved_rows(context).values())
    warnings = '\n'.join(call.args[0] for call in c.log_warn.call_args_list)
    assert 'overflow=split' in warnings and 'Pushover' in warnings and context[0].username in warnings
    assert 'a'*30 not in warnings and 'b'*30 not in warnings
    corrected, split_service = real_pushover_notifier('split')
    split_service.send = Mock(return_value=True)
    c.config.apobj = corrected
    assert deliver(context, results)
    assert split_service.send.call_count > 1
    chunks = [call.kwargs['body'] for call in split_service.send.call_args_list]
    assert all(len(chunk) <= split_service.body_maxlen for chunk in chunks)
    assert all(f'Restaurant {i:02d}' in '\n'.join(chunks) for i in range(20))
    assert 'https://www.royalcaribbean.com/' in '\n'.join(chunks)
    assert all(row['notified'] for row in saved_rows(context).values())
    split_service.send.reset_mock()
    assert deliver(context, results)
    split_service.send.assert_not_called()


def test_overflow_validation_uses_account_override_and_leaves_price_notifier_unchanged(context):
    a, b, category, s, p = context
    unsafe, unsafe_service = real_pushover_notifier()
    unsafe_service.body_maxlen = 200
    safe, safe_service = real_pushover_notifier('split')
    safe_service.send = Mock(return_value=True)
    c.config.apobj = unsafe
    a.apobj = safe
    assert deliver((a, b, category, s, p))
    unsafe_service.send.assert_not_called()
    assert unsafe_service.overflow_mode == 'upstream'
    # An unsafe account override must not silently fall back to a safe global notifier.
    a.apobj = unsafe
    c.config.apobj = safe
    assert not deliver((a, b, category, s, p), [state_result(product='second')])
    unsafe_service.send.assert_not_called()
    assert saved_rows((a, b, category, s, p))['second']['notified'] is False


def test_every_destination_must_allow_complete_message_delivery(context):
    notifier, good = real_pushover_notifier('split')
    assert notifier.add('pover://' + 'c'*30 + '@' + 'd'*30 + '/?overflow=truncate')
    bad = list(notifier)[1]
    bad.body_maxlen = 200
    bad.send = Mock(side_effect=AssertionError('Unexpected notification'))
    c.config.apobj = notifier
    assert not deliver(context)
    good.send.assert_not_called()
    bad.send.assert_not_called()
    assert saved_rows(context)['Y7QG']['notified'] is False


def test_dry_run_warns_about_overflow_without_state_or_notifications(context, monkeypatch):
    a, b, category, s, p = context
    notifier, service = real_pushover_notifier()
    service.body_maxlen = 200
    c.config.apobj = notifier
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': 'Y7QG', 'type': {'id': 'pt_show'}, 'title': 'Headliner'}]))
    eligibility = Mock(return_value=capture('headliner'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    read = Mock(side_effect=AssertionError('Dry run read state'))
    monkeypatch.setattr(c, 'read_reservation_state', read)
    assert c.process_availability_bookings(a, [b], replace(s, dry_run=True))
    eligibility.assert_called_once()
    read.assert_not_called()
    service.send.assert_not_called()
    assert not Path(s.state_file).exists()
    assert 'overflow=split' in '\n'.join(call.args[0] for call in c.log_warn.call_args_list)


def test_invalid_overflow_still_finishes_normal_price_outputs(context, monkeypatch):
    a, b = setup_combined_console(context, monkeypatch)
    notifier, service = real_pushover_notifier()
    service.body_maxlen = 200
    c.config.apobj = notifier
    monkeypatch.setattr(c, 'get_voyages', Mock(return_value=[b]))
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[
        {'id': 'Y7QG', 'type': {'id': 'pt_show'}}]))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('headliner')))
    prices = Mock()
    monkeypatch.setattr(c, 'get_cruise_price', prices)
    tracker = Mock()
    monkeypatch.setattr(c, 'CheckinPaymentTracker', Mock(return_value=tracker))
    with pytest.raises(SystemExit) as error:
        c.main()
    assert error.value.code == c.EXIT_PARTIAL_FAILURE
    prices.assert_called_once()
    tracker.print_table.assert_called_once()
    a.access.session.close.assert_called_once()
    service.send.assert_not_called()
    assert c.history.finish_run.call_args.args[0] == 'partial_failure'


@pytest.mark.parametrize('selected', [False, True])
def test_notfound_catalog_cannot_rearm_after_an_alert(context, monkeypatch, selected):
    a, b, category, s, party = context
    category = replace(category, products=category.products if selected else None)
    s = replace(s, reservations=(c.AvailabilityReservation(b['bookingId'], (category,), True),))
    ctx = a, b, category, s, party
    assert deliver(ctx, notify_on_reopen=True)
    before = Path(s.state_file).read_bytes()
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=capture('wonder_catalog')))
    warnings = []
    assert c.process_availability_bookings(a, [b], s, warnings)
    assert warnings == []
    c.finish_availability_run(s, {b['bookingId']}, True, warnings)
    assert Path(s.state_file).read_bytes() == before
    assert deliver(ctx, notify_on_reopen=True)
    assert c.config.apobj.notify.call_count == 1


@pytest.mark.parametrize('dates, stock, expected', [
    (['2099-10-10T18:00:00', '2099-10-17T00:30:00'], 10, 'available'),
    (['2099-10-09T23:59:00', '2099-10-16T23:59:00'], 10, 'available'),
    (['2099-10-17T00:00:00'], 10, 'unknown'),
    (['2099-10-09T23:59:00'], 10, 'unknown'),
    (['invalid', '2099-10-17T00:00:00'], 10, 'unknown'),
    (['invalid', '2099-10-10T18:00:00'], 0, 'unknown'),
    (['2099-10-10T18:00:00', '2099-10-17T00:00:00'], 0, 'unavailable'),
    ([], 0, 'unavailable'),
])
def test_sailing_date_filter_preserves_valid_evidence(context, monkeypatch, dates, stock, expected):
    a, b, cat, s, party = context
    response = capture('headliner')
    response['payload']['offerings'] = [
        {'id': str(i), 'dateTime': day, 'stockLevelStatus': 'inStock', 'stockLevel': stock}
        for i, day in enumerate(dates)]
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=response))
    try:
        data = c.availability_eligibility(a, b, cat.category, cat.products[0], party)
        result = c.evaluate_availability(data, cat.category, cat.products[0], 'Show')
        assert result.state == expected
        assert all('2099-10-17' not in stamp and '2099-10-09' not in stamp for stamp in result.times)
    except c.AvailabilityUnknown:
        assert expected == 'unknown'
    assert len(response['payload']['offerings']) == len(dates)  # Caller data is not mutated.


def test_readable_scope_identifies_booking_and_avoids_delimiter_collisions(context):
    a, b, category, s, party = context
    key = c.availability_scope(a, b, 'dining')
    assert json.loads(key) == [a.username.lower(), 'IC', '2099-10-10', 'booking-1', 'dining']
    other = replace(a, username='someone | IC@example.invalid')
    assert c.availability_scope(other, b, 'dining') != key
    assert deliver(context)
    assert key.replace('dining', 'show') in c.read_reservation_state(Path(s.state_file))['scopes']


@pytest.mark.parametrize('old_value', [None, {}, {'reservations': []}])
def test_config_rejects_old_top_level_name(tmp_path, old_value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'availability': old_value}))
    with pytest.raises(ValueError, match='Rename availability to reservationAlerts'):
        c.load_config_objects(str(path))


def test_new_config_name_loads_and_keeps_other_features(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "setup_hybrid_logging", Mock())
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'reservationAlerts': valid_config(),
                               'cabinAvailabilityStateFile': 'other.json'}))
    config = c.load_config_objects(str(path))
    assert config.availability == c.parse_availability_config(valid_config())
    assert config.cabin_availability_state_file == 'other.json'


def test_coverage_warning_finishes_successfully_and_is_recorded(context, monkeypatch):
    a, b = setup_combined_console(context, monkeypatch)
    monkeypatch.setattr(c, 'get_voyages', Mock(return_value=[b]))
    monkeypatch.setattr(c, 'get_cruise_price', Mock())
    monkeypatch.setattr(c, 'availability_products', Mock(side_effect=c.AvailabilityCatalogIncomplete(
        'count mismatch', [{'id': 'Y7QG', 'type': {'id': 'pt_show'}}])))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('headliner')))
    c.main()
    assert c.history.finish_run.call_args.args[0] == 'ok'
    assert 'incomplete coverage' in c.history.finish_run.call_args.args[1]
    assert any('completed with coverage warnings' in x.args[0] for x in c.log_warn.call_args_list)
    assert not any('Availability checks completed successfully' in x.args[0] for x in c.log.call_args_list)
    c.get_cruise_price.assert_called_once()


@pytest.mark.parametrize('failure', ['catalog', 'eligibility'])
def test_transport_failure_stays_failure_even_with_other_valid_products(context, monkeypatch, failure):
    a, b, category, s, p = context
    category = replace(category, products=None)
    s = replace(s, reservations=(c.AvailabilityReservation(b['bookingId'], (category,)),))
    products = [{'id': x, 'type': {'id': 'pt_show'}} for x in ['Y7QG', 'failed']]
    catalog = Mock(return_value=products)
    if failure == 'catalog':
        catalog.side_effect = c.AvailabilityCatalogIncomplete('request failed', products[:1], True)
    monkeypatch.setattr(c, 'availability_products', catalog)
    monkeypatch.setattr(c, 'availability_eligibility', Mock(side_effect=[
        capture('headliner'), c.AvailabilityRequestFailed('HTTP 401')]))
    assert not c.process_availability_bookings(a, [b], s)
    c.config.apobj.notify.assert_called_once()


def test_no_reservation_header_for_unmatched_account(context, monkeypatch):
    a, b = setup_combined_console(context, monkeypatch)
    monkeypatch.setattr(c, 'get_voyages', Mock(return_value=[]))
    monkeypatch.setattr(c, 'get_cruise_price', Mock())
    with pytest.raises(SystemExit):  # Globally missing configured booking remains actionable.
        c.main()
    assert not any('Reservation Alerts' in x.args[0] for x in c.log.call_args_list)


@pytest.mark.parametrize('selected, expected_requests', [(False, 33), (True, 3)])
def test_pacing_and_selected_products_reduce_requests(context, monkeypatch, selected, expected_requests):
    a, b, _, s, _ = context
    categories = (c.AvailabilityCategory('dining', ('dining0',)),) if selected else (
        c.AvailabilityCategory('dining'), c.AvailabilityCategory('show'))
    s = replace(s, dry_run=True, reservations=(c.AvailabilityReservation(b['bookingId'], categories),))
    now = [0.0]
    starts, ends = [], []
    monkeypatch.setattr(c.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(c.time, 'sleep', lambda delay: now.__setitem__(0, now[0] + delay))
    def request(account, method, url, **kwargs):
        starts.append(now[0])
        query = kwargs['json_data']
        if 'graphql' in url:
            v = query['variables']; category = v['category']
            count = 20 if category == 'dining' else 10
            entries = [{'id': category + str(i), 'type': {'id': 'pt_' + category}}
                       for i in range(count)]
            page = v['currentPage']
            result = {'data': {'products': {'__typename': 'CommerceProductResultSuccess',
                'commerceProducts': entries[page*12:(page+1)*12],
                'pageInfo': {'totalPages': (count+11)//12, 'totalResults': count}}}}
            assert 'productStatus' not in query['query']
        else:
            result = {'status': 200, 'payload': {'productCode': query['productCode'],
                'categoryId': query['categoryId'], 'offerings': []}}
        now[0] += 0.2
        ends.append(now[0])
        return Mock(status_code=200, json=Mock(return_value=result))
    network = Mock(side_effect=request)
    monkeypatch.setattr(c, '_execute_api_request', network)
    assert c.process_availability_bookings(a, [b], s)
    assert network.call_count == expected_requests
    assert starts[0] == 0
    assert all(start - end == pytest.approx(1) for start, end in zip(starts[1:], ends))


def test_pacing_counts_failed_requests_and_existing_cooldown(context, monkeypatch):
    a = context[0]
    now = [0.0]
    monkeypatch.setattr(c.time, 'monotonic', lambda: now[0])
    sleeps = Mock(side_effect=lambda delay: now.__setitem__(0, now[0] + delay))
    monkeypatch.setattr(c.time, 'sleep', sleeps)
    monkeypatch.setattr(c, '_execute_api_request', Mock(return_value=None))
    for _ in range(2):
        with pytest.raises(c.AvailabilityRequestFailed):
            c.availability_json(a, 'POST', 'https://example.invalid')
    sleeps.assert_called_once_with(1)
    now[0] += 5  # Existing account cooldown already covers the gap.
    with pytest.raises(c.AvailabilityRequestFailed):
        c.availability_json(a, 'POST', 'https://example.invalid')
    assert sleeps.call_count == 1


@pytest.mark.parametrize('mode', [None, 'truncate', 'split'])
def test_small_pushover_message_needs_no_overflow_change(context, mode):
    notifier, service = real_pushover_notifier(mode)
    service.send = Mock(return_value=True)
    original = service.overflow_mode
    c.config.apobj = notifier
    assert deliver(context)
    service.send.assert_called_once()
    body = service.send.call_args.kwargs['body']
    assert 'Headliner' in body and 'Cruise Planner:' in body
    assert service.overflow_mode == original
    assert saved_rows(context)['Y7QG']['notified']


def test_html_email_fits_without_split_and_keeps_all_products(context):
    apprise = pytest.importorskip('apprise')
    notifier = apprise.Apprise()
    assert notifier.add('mailto://user:password@example.invalid?format=html')
    service = next(iter(notifier))
    service.send = Mock(return_value=True)
    c.config.apobj = notifier
    results = [replace(state_result(product=str(i)), title=f'Restaurant {i:02d} < & >') for i in range(30)]
    assert deliver(context, results)
    service.send.assert_called_once()
    body = service.send.call_args.kwargs['body']
    assert '&lt;' in body and '&amp;' in body
    assert all(f'Restaurant&nbsp;{i:02d}' in body for i in range(30))
    assert service.overflow_mode == 'upstream'


@pytest.mark.parametrize('mode', [None, 'split'])
@pytest.mark.parametrize('restriction', ['lines', 'title'])
def test_split_does_not_bypass_other_destructive_limits(context, mode, restriction):
    notifier, service = real_pushover_notifier(mode)
    if restriction == 'lines':
        service.body_max_line_count = 2
    else:
        service.title_maxlen = 10
    c.config.apobj = notifier
    assert not deliver(context)
    service.send.assert_not_called()
    assert not saved_rows(context)['Y7QG']['notified']
    assert any(('line limit' if restriction == 'lines' else 'message/title') in x.args[0]
               for x in c.log_warn.call_args_list)


@pytest.mark.parametrize('restriction', ['combined', 'no_title', 'formatting'])
def test_overflow_validation_counts_rendered_message_not_raw_body(context, restriction):
    notifier, service = real_pushover_notifier()
    c.config.apobj = notifier
    body = 'x' * 990
    if restriction == 'combined':
        service.overflow_amalgamate_title = True
        body = 'x' * 1000
    elif restriction == 'no_title':
        service.title_maxlen = 0
        body = 'x' * 1000
    else:
        service.notify_format = c.NotifyFormat.HTML
        body = '<&' * 150  # 300 input characters expand beyond 1024 in HTML.
    error = c.availability_notification_error(context[0], body)
    assert error and 'overflow=split' in error
    service.send.assert_not_called()
    service.overflow_mode = 'split'
    assert c.availability_notification_error(context[0], body) is None


def test_notification_preview_failure_does_not_send_or_expose_details(context):
    c.config.apobj._create_notify_gen.side_effect = RuntimeError('PRIVATE_TOKEN')
    assert not deliver(context)
    c.config.apobj.notify.assert_not_called()
    assert not saved_rows(context)['Y7QG']['notified']
    assert 'PRIVATE_TOKEN' not in '\n'.join(x.args[0] for x in c.log_warn.call_args_list)


@pytest.mark.parametrize('category_name', ['show', 'dining'])
@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.parametrize('dry_run', [False, True])
def test_no_catalog_is_healthy_without_creating_state(context, monkeypatch, category_name, selected, dry_run):
    a, b, category, settings, _ = context
    category = replace(category, category=category_name, products=('chosen',) if selected else None)
    settings = replace(settings, dry_run=dry_run,
        reservations=(c.AvailabilityReservation(b['bookingId'], (category,)),))
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=capture('wonder_catalog')))
    eligibility = Mock(side_effect=AssertionError('No eligibility request without catalog'))
    monkeypatch.setattr(c, 'availability_eligibility', eligibility)
    warnings = []
    assert c.process_availability_bookings(a, [b], settings, warnings)
    assert not warnings
    assert not Path(settings.state_file).exists()
    eligibility.assert_not_called()
    c.config.apobj.notify.assert_not_called()
    assert any('No ' + category_name + ' catalog currently available' in call.args[0]
               for call in c.log.call_args_list)


@pytest.mark.parametrize('exceptions', [
    [], None, {}, [None],
    [{'__typename': 'CommerceProductNotFound'}, {'__typename': 'Unauthorized'}],
])
def test_only_explicit_notfound_is_a_missing_catalog(context, monkeypatch, exceptions):
    data = capture('wonder_catalog')
    data['data']['products']['exceptions'] = exceptions
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=data))
    with pytest.raises(c.AvailabilityCatalogIncomplete):
        c.availability_products(context[0], context[1], 'show')


def test_missing_catalog_does_not_prune_unselected_state(context, monkeypatch):
    a, b, category, settings, _ = context
    assert deliver(context, [state_result(), state_result(product='other')], notify_on_reopen=True)
    before = Path(settings.state_file).read_bytes()
    monkeypatch.setattr(c, 'availability_json', Mock(return_value=capture('wonder_catalog')))
    assert c.process_availability_bookings(a, [b], settings)
    assert Path(settings.state_file).read_bytes() == before


def test_missing_show_catalog_does_not_skip_dining(context, monkeypatch):
    a, b, category, settings, _ = context
    dining = c.AvailabilityCategory('dining', None)
    settings = replace(settings, reservations=(c.AvailabilityReservation(b['bookingId'], (category, dining)),))
    monkeypatch.setattr(c, 'availability_products', Mock(side_effect=[None,
        [{'id': 'UT_RAILDINNER', 'title': 'Railway', 'type': {'id': 'pt_dining'}}]]))
    monkeypatch.setattr(c, 'availability_eligibility', Mock(return_value=capture('railway')))
    assert c.process_availability_bookings(a, [b], settings)
    c.config.apobj.notify.assert_called_once()
    assert 'Railway' in c.config.apobj.notify.call_args.kwargs['body']


def test_missing_catalog_still_reports_missing_notifier(context, monkeypatch):
    a, b, _, settings, _ = context
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=None))
    c.config.apobj = None
    assert not c.process_availability_bookings(a, [b], settings)
    assert not Path(settings.state_file).exists()


@pytest.mark.parametrize('method', ['_create_notify_gen', '_apply_overflow'])
def test_incompatible_preview_has_actionable_version_error_and_retries(context, monkeypatch, method):
    notifier, service = real_pushover_notifier('split')
    service.send = Mock(return_value=True)
    c.config.apobj = notifier
    target = notifier if method == '_create_notify_gen' else service
    original = getattr(target, method)
    monkeypatch.setattr(target, method, Mock(side_effect=AttributeError('PRIVATE_TOKEN')))
    assert not deliver(context)
    service.send.assert_not_called()
    assert not saved_rows(context)['Y7QG']['notified']
    output = '\n'.join(call.args[0] for call in c.log_warn.call_args_list)
    assert c.apprise_version in output and 'Apprise==1.13.1' in output
    assert 'partial failure' in output and 'PRIVATE_TOKEN' not in output
    monkeypatch.setattr(target, method, original)
    assert deliver(context)
    service.send.assert_called_once()
    assert saved_rows(context)['Y7QG']['notified']


def test_console_large_inventory_summarizes_all_days(context):
    a, b, category, settings, _ = context
    times = tuple(f'2099-10-{day:02d}T{hour:02d}:00:00'
                  for day in range(10, 17) for hour in range(6, 23))
    result = replace(state_result(), times=times)
    assert c.deliver_availability(settings, a, b, category, False, [result])
    output = '\n'.join(call.args[0] for call in c.log.call_args_list)
    assert '119 available times across 7 day(s); showing the first 6' in output
    assert output.count(':00') == 6
    assert len(result.times) == 119

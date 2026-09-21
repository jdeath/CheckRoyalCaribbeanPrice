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
from unittest.mock import Mock

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


@pytest.fixture(autouse=True)
def globals_without_network(monkeypatch):
    conf = c.CruiseAppConfig()
    conf.apobj = Mock()
    conf.apobj.notify.return_value = True
    monkeypatch.setattr(c, 'config', conf)
    monkeypatch.setattr(c, 'history', Mock())
    monkeypatch.setattr(c, 'log', Mock())
    monkeypatch.setattr(c, 'log_warn', Mock())
    monkeypatch.setattr(c, '_execute_api_request', Mock(side_effect=AssertionError('Unmocked network call')))
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
    assert c.availability_products(a,b,'show') == []
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
    assert state['version'] == 1
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
    assert not c.process_availability_bookings(a,[b],settings)
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
    ({'reservations': []}, 'availability.reservations must be a nonempty list'),
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
from unittest.mock import Mock
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
c.config.apobj = Mock()
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
    assert not c.process_availability_bookings(a, [b], settings)
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
    sailing_line = next(i for i, line in enumerate(lines) if 'ICON OF THE SEAS (2099-10-10)' in line)
    category_line = next(i for i, line in enumerate(lines) if 'Shows' in line)
    product_line = next(i for i, line in enumerate(lines) if 'Headliner: Available' in line)
    assert account_line < sailing_line < category_line < product_line
    assert lines[sailing_line].startswith('    ')
    assert lines[category_line].startswith('      ')
    assert lines[product_line].startswith('        ')

def test_availability_sailing_label_falls_back_to_ship_code(context):
    _, booking, *_ = context
    c.config.date_display_format = "%m/%d/%Y"
    assert c.availability_sailing_label(booking) == "IC (2099-10-10)"

def test_availability_notification_hides_apprise_info_chatter_but_keeps_warnings(context, caplog):
    chatter = "Sent Pushover notification to ALL_DEVICES."
    warning = "Pushover delivery warning"
    notifier = Mock()

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
    assert not c.deliver_availability(replace(s, dry_run=True), a, b, w, False, results)
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
    (lambda d:d.update(dryrun=True), 'availability: unrecognized configuration key(s): dryrun'),
    (lambda d:d['reservations'][0].update(mod='release'),
     'availability.reservations[0]: unrecognized configuration key(s): mod'),
    (lambda d:d['reservations'][0].update(shows='false'),
     'availability.reservations[0]: shows must be true, false, or a mapping'),
    (lambda d:d['reservations'][0].update(shows={'guests':[{'id':'SECRET_VALUE'}]}),
     'availability.reservations[0].shows: unrecognized configuration key(s): guests'),
    (lambda d:d['reservations'][0].update(reservation=None),
     'availability.reservations[0]: reservation must be a nonempty identifier'),
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
    c.config.apobj = Mock()
    c.config.apobj.notify.return_value = True
    assert deliver(context)
    c.config.apobj.notify.assert_called_once()


def test_state_storage_diagnostic_is_actionable_and_does_not_expose_exception(context, monkeypatch):
    a, b, w, s, p = context
    monkeypatch.setattr(c, 'availability_products', Mock(return_value=[]))
    monkeypatch.setattr(c, 'deliver_availability', Mock(side_effect=PermissionError('PRIVATE_PATH')))
    assert not c.process_availability_bookings(a, [b], s)
    messages = '\n'.join(call.args[0] for call in c.log_warn.call_args_list)
    assert 'check availability.stateFile and directory permissions' in messages
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


def test_compact_alert_retains_preview_limit_but_console_has_all_times(context):
    a, b, w, s, p = context
    times = tuple(f'2099-10-10T{hour:02d}:00:00' for hour in range(10, 18))
    assert c.deliver_availability(s, a, b, w, False,
        [c.AvailabilityResult('one', 'Show', 'available', 'inventory', times)])
    body = c.config.apobj.notify.call_args.kwargs['body']
    assert '(+2 more times in Cruise Planner)' in body
    assert '15:00' in body and '16:00' not in body
    assert any('16:00, 17:00' in call.args[0] for call in c.log.call_args_list)


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
    b'{"version":1,"watches":{}}',
    b'{"version":2,"scopes":{}}', b'{"version":true,"scopes":{}}',
    b'{"version":1,"scopes":[]}', b'{"version":1,"scopes":{},"unexpected":0}',
    b'{"version":1,"scopes":{"scope":null}}',
    b'{"version":1,"scopes":{"":{"product":{"last_state":"available","notified":true}}}}',
    b'{"version":1,"scopes":{"scope":{"":{}}}}',
    b'{"version":1,"scopes":{"scope":{"product":null}}}',
    b'{"version":1,"scopes":{"scope":{"product":{"last_state":"unknown","notified":true}}}}',
    b'{"version":1,"scopes":{"scope":{"product":{"last_state":"available","notified":1}}}}',
    b'{"version":1,"scopes":{"scope":{"product":{"last_state":"available","notified":"false"}}}}',
    b'{"version":1,"scopes":{"scope":{"product":{"last_state":"available"}}}}',
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
    assert a.username not in Path(s.state_file).read_text()
    assert b['bookingId'] not in Path(s.state_file).read_text()


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

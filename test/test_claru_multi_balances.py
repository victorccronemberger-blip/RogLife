"""Claru multiaccount Minute registration and authenticated balance isolation."""
import json
import time
from unittest.mock import Mock
import pytest
from moneymin import claru, claru_registration, config, credential_store, token_store
from moneymin.web import server, wallet
from moneymin.web.runner import BalancesRunner
from test_accounts_creation_e2e import setup, await_terminal

PEOPLE = {
    'first@example.invalid': ('first@supply.claru.ai', 'claru-first-secret'),
    'second@example.invalid': ('second@supply.claru.ai', 'claru-second-secret'),
}

@pytest.fixture
def sessions(monkeypatch):
    calls, instances = [], []
    class Session:
        def __init__(self):
            self.email = None
            instances.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            calls.append((self, method, url, kwargs))
            assert kwargs['allow_redirects'] is False
            if url.endswith('/auth/app-sign-in'):
                self.email = kwargs['json']['email']
                assert kwargs['json']['password'] == PEOPLE[self.email][1]
                value = {}
            elif url.endswith('/users/me'):
                value = {'email': self.email, 'enabled': True, 'inboxAddress': PEOPLE[self.email][0]}
            elif url.endswith('/inbox/address'):
                value = {'address': PEOPLE[self.email][0]}
            elif url.endswith('/payments/summary/me'):
                value = {'amount': 12.34 if self.email.startswith('first') else 56.78,
                         'amountsHidden': False, 'fixedRewards': {'rewards': []}, 'withdrawableAmount': 10}
            else:
                pytest.fail('Unapproved endpoint: ' + url)
            return Mock(status_code=200, json=Mock(return_value=value))
    monkeypatch.setattr(claru.requests, 'Session', Session)
    return calls, instances


def manifest():
    return [{'login_email': email, 'claru_password': password, 'minute_password': 'minute-' + email}
            for email, (_, password) in PEOPLE.items()]


def test_two_people_register_only_their_generated_minutes_and_protect_all_passwords(setup, sessions):
    app, _, crowtado, register, root = setup
    server.Session.from_email.return_value.ensure_auth.return_value = {
        'organizations': [{'resourceKey': config.CLARU_ORG_KEY, 'disabled': False}]}
    client = app.test_client()
    body = {'provider': 'claru', 'accounts': manifest(), 'request_id': 'two-people'}
    assert client.post('/api/accounts/bulk-register', json=body).status_code == 200
    state = await_terminal(client)
    assert state['created'] == 2 and state['failed'] == 0
    assert [(c.args[0], c.args[1], c.args[2]) for c in register.call_args_list] == [
        (minute, 'minute-' + email, config.CLARU_INVITE_CODE) for email, (minute, _) in PEOPLE.items()]
    for row in state['results']:
        assert row['email'] == PEOPLE[row['claru_login']][0] == row['minute_email']
        assert credential_store.lookup(config.SECRETS_DIR, row['minute_email']) == 'minute-' + row['claru_login']
    assert not claru_registration.public_pending()
    for remote in crowtado.values(): remote.assert_not_called()
    assert all('/auth/signup' not in call[2] for call in sessions[0])
    for path in config.DATA_DIR.glob('*.json'):
        text = path.read_text(encoding='utf-8')
        assert all(item['claru_password'] not in text and item['minute_password'] not in text for item in manifest())
    for path in config.SECRETS_DIR.glob('*.dat'):
        assert all(item['minute_password'].encode() not in path.read_bytes() for item in manifest())
    # The same submitted request id cannot start another worker.
    assert client.post('/api/accounts/bulk-register', json=body).get_json()['existing']
    assert register.call_count == 2


def test_invalid_second_identity_rejects_whole_manifest_before_remote_work(setup, sessions):
    app, _, _, register, _ = setup
    entries = manifest(); entries[1]['minute_password'] = ''
    response = app.test_client().post('/api/accounts/bulk-register', json={'provider': 'claru', 'accounts': entries})
    assert response.status_code == 400 and not sessions[0]
    register.assert_not_called()
    assert not (config.SECRETS_DIR / 'claru_minute_registrations.dat').exists()


def test_resume_preserves_first_minutes_access_without_duplicate_creation(setup, sessions, monkeypatch):
    app, _, _, register, _ = setup
    auth = server.Session.from_email.return_value.ensure_auth
    auth.side_effect = [RuntimeError('temporary profile failure'),
                       {'organizations': [{'resourceKey': config.CLARU_ORG_KEY, 'disabled': False}]}]
    client = app.test_client(); item = manifest()[0]
    server.Session.from_email.return_value.data["email"] = PEOPLE[item["login_email"]][0]
    assert client.post('/api/accounts/bulk-register', json={'provider': 'claru', 'accounts': [item]}).status_code == 200
    assert await_terminal(client)['failed'] == 1
    auth.side_effect = None
    auth.return_value = {'organizations': [{'resourceKey': config.CLARU_ORG_KEY, 'disabled': False}]}
    pending = client.get('/api/accounts/claru').get_json()['pending']
    assert len(pending) == 1 and 'password' not in json.dumps(pending)
    assert client.post('/api/accounts/bulk-register', json={'provider': 'claru', 'accounts': [{'login_email': item['login_email']}]}).status_code == 200
    assert await_terminal(client)['created'] == 1
    assert register.call_count == 1
    assert not claru_registration.public_pending()


def test_claru_balances_available_before_minute_and_never_use_minute_password(setup, sessions):
    app, _, crowtado, _, _ = setup
    server.BALANCES_RUNNER.snapshot.return_value = {"state": "idle"}
    for email, (_, password) in PEOPLE.items(): claru.connect(email, password)
    client = app.test_client()
    accounts = client.get('/api/balances').get_json()
    assert set(accounts['accounts']) == {p[0] for p in PEOPLE.values()}
    assert set(accounts['with_password']) == set(accounts['accounts'])
    rows = []
    runner = BalancesRunner()
    runner.start({minute: 'unused-marker' for minute, _ in PEOPLE.values()},
                 lambda email, summary, error: (rows.append((email, summary, error)), server._on_balance_result(email, summary, error)))
    runner._thread.join(timeout=5)
    assert runner.state == 'done' and runner.failed == 0 and len(rows) == 2
    assert all(r[2] is None for r in rows)
    result = client.get('/api/balances').get_json()
    assert result['wallet']['totals']['availableCents'] == 6912
    assert result['wallet']['counts']['confirmed'] == 2
    assert result['balances']['first@supply.claru.ai']['availableCents'] == 1234
    assert result['balances']['second@supply.claru.ai']['availableCents'] == 5678
    assert all(not row['payout']['eligible'] for row in result['wallet']['accounts'].values())
    assert all('pendingCents' not in row for row in result['balances'].values())
    assert all('/payments/' not in call[2] or call[1] == 'GET' for call in sessions[0])
    assert len({id(call[0]) for call in sessions[0] if call[2].endswith('/payments/summary/me')}) == 2
    assert all('unused-marker' not in json.dumps(call[3]) for call in sessions[0])
    for remote in crowtado.values(): remote.assert_not_called()


def test_exact_dollar_mapping_matches_official_breakdown_without_fabricating_pending():
    value = claru.balance_from_payload({'amount': 120.25, 'amountsHidden': False,
        'withdrawableAmount': 100, 'fixedRewards': {'rewards': [
            {'source': 'REFERRAL', 'amount': 5.5}, {'source': 'MILESTONE', 'amount': 2.25}]}})
    assert value['availableCents'] == 12025 and value['submissionCents'] == 11250
    assert value['referralCents'] == 550 and value['bonusCents'] == 225 and value['withdrawableCents'] == 10000
    assert 'pendingCents' not in value

@pytest.mark.parametrize('amount', [None, True, -1, float('nan'), float('inf'), 0.001])
def test_invalid_amount_never_becomes_confirmed_zero(amount):
    with pytest.raises(claru.ClaruError): claru.balance_from_payload({'amount': amount, 'amountsHidden': False})


def test_failed_refresh_keeps_last_valid_balance_as_stale(setup):
    app, _, _, _, _ = setup
    email = PEOPLE['first@example.invalid'][0]
    summary = claru.balance_from_payload({'amount': 12.34, 'amountsHidden': False})
    server._on_balance_result(email, summary, None)
    server._on_balance_result(email, None, RuntimeError('private-secret-never-expose'))
    record = server._load_balances()[email]
    assert record['availableCents'] == 1234 and record['stale']
    assert 'private-secret' not in json.dumps(record)
    assert not wallet.reading(record)['confirmed']

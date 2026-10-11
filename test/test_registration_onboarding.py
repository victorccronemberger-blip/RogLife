"""Profile data and task-link checkpoints use explicit titular inputs."""
from unittest.mock import Mock

import pytest

from moneymin import crowtado
from moneymin.web import registration_state, server
from test_accounts_creation_e2e import setup, await_terminal, EMAIL, PASSWORD


def test_missing_profile_can_resume_with_supplied_data_without_new_identity(setup):
    app, body, remote, minute_register, _ = setup
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json={'email': EMAIL, 'password': PASSWORD})
    assert await_terminal(client)['failed'] == 1
    remote['criar_conta'].assert_not_called()
    remote['login'].assert_not_called()
    minute_register.assert_not_called()
    remote['preencher_demografia'].assert_not_called()
    response = client.post(f'/api/accounts/{EMAIL}/resume', json={
        'birth_month': 5, 'birth_year': 1999, 'gender': 'male'})
    assert response.status_code == 200
    assert await_terminal(client)['created'] == 1
    remote['criar_conta'].assert_called_once()
    remote['preencher_demografia'].assert_called_once_with(EMAIL, PASSWORD, 5, 1999, 'male', session=remote['login'].return_value)
    remote['vincular_minute'].assert_called_once_with(EMAIL, PASSWORD, session=remote['login'].return_value)
    minute_register.assert_called_once()


def test_legacy_complete_record_with_manual_steps_is_resumable(setup):
    app, body, remote, minute_register, _ = setup
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json=body)
    assert await_terminal(client)['created'] == 1
    row = registration_state.load()[EMAIL]
    for step in ('demographics', 'link_minute'):
        row['steps'][step] = {'status': 'manual', 'detail': 'legacy manual step'}
    registration_state.update(EMAIL, steps=row['steps'], state='complete')
    assert client.post(f'/api/accounts/{EMAIL}/resume', json={}).status_code == 200
    assert await_terminal(client)['created'] == 1
    assert remote['preencher_demografia'].call_count == 2
    assert remote['vincular_minute'].call_count == 2
    remote['criar_conta'].assert_called_once()
    minute_register.assert_called_once()


def test_link_failure_resume_preserves_confirmed_profile_and_accounts(setup):
    app, body, remote, minute_register, _ = setup
    remote['vincular_minute'].side_effect = crowtado.CrowtadoError('Temporary failure', code='service')
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json=body)
    assert await_terminal(client)['failed'] == 1
    row = registration_state.load()[EMAIL]
    assert row['steps']['link_minute']['remote_effect_possible'] is True
    assert row['steps']['demographics']['status'] == 'ok'
    remote['vincular_minute'].side_effect = None
    assert client.post(f'/api/accounts/{EMAIL}/resume', json={}).status_code == 200
    assert await_terminal(client)['created'] == 1
    assert remote['vincular_minute'].call_count == 2
    remote['preencher_demografia'].assert_called_once()
    remote['criar_conta'].assert_called_once()
    minute_register.assert_called_once()


def test_batch_uses_supplied_profiles_not_generated_demographics(setup, monkeypatch):
    app, _, remote, _, _ = setup
    monkeypatch.setattr(server.identity, 'gerar_identidade', Mock(side_effect=[
        {'email': f'fixture{i}@example.invalid', 'senha': PASSWORD, 'nome': 'Fixture',
         'sobrenome': 'Test', 'birth_month': 1, 'birth_year': 1900, 'gender': 'female'}
        for i in range(2)]))
    profiles = [{'birth_month': 5, 'birth_year': 1999, 'gender': 'male'},
                {'birth_month': 6, 'birth_year': 1992, 'gender': 'prefer_not_to_say'}]
    client = app.test_client()
    assert client.post('/api/accounts/bulk-register', json={
        'count': 2, 'domain': 'example.invalid', 'profiles': profiles}).status_code == 200
    assert await_terminal(client)['created'] == 2
    assert [call.args[2:] for call in remote['preencher_demografia'].call_args_list] == [
        (5, 1999, 'male'), (6, 1992, 'prefer_not_to_say')]


@pytest.mark.parametrize('profiles', [[], [{}], [{'birth_month': True, 'birth_year': 1999, 'gender': 'male'}],
                                    [{'birth_month': 5, 'birth_year': 1999, 'gender': 'unknown'}]])
def test_invalid_profile_batch_does_not_start_remote_work(setup, profiles):
    app, _, remote, minute_register, _ = setup
    response = app.test_client().post('/api/accounts/bulk-register', json={
        'count': 1, 'domain': 'example.invalid', 'profiles': profiles})
    assert response.status_code == 400
    remote['criar_conta'].assert_not_called()
    minute_register.assert_not_called()


def test_signup_keeps_its_authenticated_session_for_all_following_steps(setup):
    app, body, remote, _, _ = setup
    session = object()
    events = []
    def signup(email, password, **kwargs):
        events.append('browser-open')
        kwargs['on_authenticated'](session)
        events.append('browser-close')
    remote['criar_conta'].side_effect = signup
    remote['preencher_demografia'].side_effect = lambda *a, **kw: events.append('profile')
    remote['vincular_minute'].side_effect = lambda *a, **kw: events.append('task')
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json=body)
    assert await_terminal(client)['created'] == 1
    assert events == ['browser-open', 'profile', 'task', 'browser-close']
    remote['login'].assert_not_called()
    assert remote['preencher_demografia'].call_args.kwargs['session'] is session
    assert remote['vincular_minute'].call_args.kwargs['session'] is session
    assert registration_state.load()[EMAIL]['state'] == 'complete'


def test_authenticated_continuation_failure_remains_incomplete_after_browser_closes(setup):
    app, body, remote, _, _ = setup
    session = object()
    remote['criar_conta'].side_effect = lambda *args, **kwargs: kwargs['on_authenticated'](session)
    remote['vincular_minute'].side_effect = crowtado.CrowtadoError('Task temporarily unavailable', code='minute_task_unavailable')
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json=body)
    assert await_terminal(client)['failed'] == 1
    row = registration_state.load()[EMAIL]
    assert row['state'] == 'incomplete'
    assert row['steps']['crowtado_signup']['status'] == 'ok'
    assert row['steps']['link_minute']['code'] == 'minute_task_unavailable'
    remote['criar_conta'].assert_called_once()
    remote['login'].assert_not_called()


def test_resume_authenticates_once_and_passes_the_same_session(setup):
    app, body, remote, _, _ = setup
    client = app.test_client()
    client.post('/api/accounts/register?async=1', json=body)
    assert await_terminal(client)['created'] == 1
    row = registration_state.load()[EMAIL]
    row['steps']['demographics'] = {'status': 'manual', 'detail': 'legacy'}
    row['steps']['link_minute'] = {'status': 'manual', 'detail': 'legacy'}
    registration_state.update(EMAIL, steps=row['steps'], state='complete')
    for method in remote.values(): method.reset_mock()
    remote['vincular_minute'].side_effect = crowtado.CrowtadoError('rate limited', code='rate_limit', http_status=429)
    client.post(f'/api/accounts/{EMAIL}/resume', json={})
    assert await_terminal(client)['failed'] == 1
    remote['login'].assert_called_once_with(EMAIL, PASSWORD)
    remote['criar_conta'].assert_not_called()
    assert remote['preencher_demografia'].call_args.kwargs['session'] is remote['login'].return_value
    assert remote['vincular_minute'].call_args.kwargs['session'] is remote['login'].return_value
    assert 'validate' not in registration_state.load()[EMAIL]['steps']


@pytest.mark.parametrize('profile', [
    {'birth_month': True}, {'birth_year': '2000'}, {'gender': 'invalid'}])
def test_sync_route_rejects_invalid_profile_before_remote_work(setup, profile):
    app, body, remote, _, _ = setup
    response = app.test_client().post('/api/accounts/register', json={**body, **profile})
    assert response.status_code == 400
    remote['criar_conta'].assert_not_called()


def test_old_five_account_batch_without_profiles_never_reaches_provider(setup, monkeypatch):
    app, _, remote, minute_register, _ = setup
    generate = Mock(return_value={'email': EMAIL, 'senha': PASSWORD, 'nome': 'Fixture', 'sobrenome': 'Test'})
    monkeypatch.setattr(server.identity, 'gerar_identidade', generate)
    client = app.test_client()
    client.post('/api/accounts/bulk-register', json={'count': 5, 'domain': 'example.invalid'})
    result = await_terminal(client)
    assert result['created'] == 0
    assert result['completed'] == 1
    assert result['results'][0]['steps']['demographics']['code'] == 'demographics_required'
    generate.assert_called_once()
    for method in remote.values():
        method.assert_not_called()
    minute_register.assert_not_called()

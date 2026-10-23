"""Offline campaign routes with real organization, quality and quota gates.

Independent fixtures: reviews never inherit a mocked authorization gate.
"""
from contextlib import ExitStack, nullcontext
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from auth_fixtures import configure_app_check
from moneymin import campaign, minute_api, recovery
from moneymin.web import server


ORG = 'fixture-org'
QUALITY = f'/api/v1/organizations/{ORG}/quality-summary'
QUOTA = f'/api/v1/orgs/{ORG}/quota'
APPROVED = {'recordingAuthorization': 'APPROVED', 'canUpload': True, 'blockedReason': None}


@pytest.fixture
def admission(tmp_path):
    with ExitStack() as stack:
        f = SimpleNamespace(addCleanup=stack.callback)
        configure_app_check(f)
        secrets = tmp_path / 'secrets'
        secrets.mkdir()
        for name, value in [('ROOT', tmp_path), ('DATA_DIR', tmp_path),
                            ('SECRETS_DIR', secrets), ('LIBRARY_ROOT', tmp_path),
                            ('MEDIA_DATA_DIR', tmp_path / 'media')]:
            stack.enter_context(patch.object(server.config, name, value))
        for name in ('PREFS_PATH', 'BALANCES_PATH', 'CROWTADO_PW_PATH', 'ACCOUNT_HEALTH_PATH'):
            stack.enter_context(patch.object(server, name, tmp_path / (name + '.json')))
        f.email = 'quality-owner@example.invalid'
        f.token = server.config.token_path(f.email)
        f.token.write_text(json.dumps({'email': f.email, 'localId': 'fixture-subject',
                                       'idToken': 'inert-token', 'refreshToken': 'inert-refresh'}))
        stack.enter_context(patch.object(server, '_list_accounts', return_value=[{'email': f.email}]))
        stack.enter_context(patch.object(server, '_resolve_org', return_value=ORG))
        stack.enter_context(patch.object(server, '_identity_route', side_effect=lambda email: nullcontext()))
        f.catalog = stack.enter_context(patch.object(campaign, 'available_tasks', return_value=[{
            'id': 'fixture-task', 'name': 'Fixture', 'scenario': 'fixture-scenario',
            'clip_count': 1, 'available_for_duration': True}]))
        stack.enter_context(patch.object(server.readiness, 'campaign_readiness',
                                         return_value={'ready': True, 'checks': []}))
        stack.enter_context(patch.object(server, '_storage_snapshot',
                                         return_value={'free_bytes': 100 * 1024**3}))
        stack.enter_context(patch.object(recovery, 'snapshot', return_value={'items': []}))
        for name in ('RECOVERY', 'HOLO_CACHE_RUNNER', 'BALANCES_RUNNER', 'ORG_MIGRATION'):
            stack.enter_context(patch.object(server, name, Mock(running=False)))
        f.runner = Mock(running=False, state='idle', total_sends=1)
        stack.enter_context(patch.object(server, 'RUNNER', f.runner))
        f.ban = stack.enter_context(patch.object(server, '_ban_accounts'))
        f.profile = {'disabled': False, 'organizations': [
            {'resourceKey': 'other-org', 'disabled': True},
            {'resourceKey': ORG, 'disabled': False}]}
        f.quality = minute_api.HttpResponse(200, json.dumps({'userState': 'active'}), {})
        f.queries = []
        f.sessions = []

        def detailed(method, path, body=None):
            assert method == 'GET', 'admission must never POST telemetry or uploads'
            assert body is None
            f.queries.append(path)
            if path == '/api/v1/users/me':
                return minute_api.HttpResponse(200, json.dumps(f.profile), {})
            if path == QUALITY:
                return f.quality
            assert path == QUOTA, 'unexpected route (including legacy quality route)'
            return minute_api.HttpResponse(200, json.dumps(APPROVED), {})

        def from_email(email):
            assert email == f.email
            session = minute_api.Session({'email': email, 'idToken': 'inert-token'})
            session.request_detailed = Mock(side_effect=detailed)

            def request(method, path, body=None):
                response = detailed(method, path, body)
                return response.status, response.text

            session.request = Mock(side_effect=request)
            # Keep me/org_state/checked_quality_state/recording_geo and the
            # admission gate real; implicit warmup/login must fail the test.
            for name in ('ensure_auth', 'warmup', 'app_opened'):
                setattr(session, name, Mock(side_effect=AssertionError('implicit auth/telemetry')))
            f.sessions.append(session)
            return session

        stack.enter_context(patch.object(server.Session, 'from_email', side_effect=from_email))
        f.forbidden = []
        for name in ('automatic_candidates', '_ego_clip_inputs', 'prepare_clip',
                     'prepare_holoassist_clip', 'prepare_nymeria_clip',
                     '_prefetch_following', 'upload_to_account', 'upload_session'):
            f.forbidden.append(stack.enter_context(patch.object(
                campaign, name, side_effect=AssertionError('acquisition/preparation forbidden'))))
        f.forbidden.append(stack.enter_context(patch.object(
            campaign.recording_timeline, 'reserve', side_effect=AssertionError('timeline forbidden'))))
        for name in ('_request', '_request_detailed'):
            f.forbidden.append(stack.enter_context(patch.object(
                minute_api, name, side_effect=AssertionError('real transport forbidden'))))
        f.app = server.create_app(for_testing=True)
        f.client = f.app.test_client()
        f.body = {'accounts': [f.email], 'tasks': [{'task_id': 'fixture-task'}], 'dataset': 'ego4d'}
        yield f


def no_effects(f, receipt=None, confirmed_removal=False):
    f.runner.start.assert_not_called()
    if not confirmed_removal:
        f.ban.assert_not_called()
    for operation in f.forbidden:
        operation.assert_not_called()
    for session in f.sessions:
        session.ensure_auth.assert_not_called()
        session.warmup.assert_not_called()
        session.app_opened.assert_not_called()
    assert f.token.exists()
    if receipt:
        assert server.campaign_start_store.lookup(receipt) is None


def review(f):
    response = f.client.post('/api/campaigns/preflight', json=f.body)
    assert response.status_code == 200, response.json
    assert response.json['ok'], repr(response.json)
    assert QUALITY in f.queries
    assert QUOTA in f.queries
    no_effects(f)
    return response.json['preflight_id']


@pytest.mark.parametrize('state,code', [
    ('on_hold', 'access_paused'), ('inactive', 'access_paused'),
    ('unknown', 'invalid_response'), ('ACTIVE', 'invalid_response'),
    (None, 'invalid_response'), (True, 'invalid_response'),
])
@pytest.mark.parametrize('phase', ['preflight', 'legacy-start', 'reviewed-start'])
def test_approved_quota_cannot_override_quality(admission, state, code, phase):
    f = admission
    receipt = review(f) if phase == 'reviewed-start' else None
    f.queries.clear()
    f.catalog.reset_mock()
    f.quality = minute_api.HttpResponse(200, json.dumps({'userState': state}), {})
    body = {**f.body, **({'preflight_id': receipt} if receipt else {})}
    if phase == 'preflight':
        body['include_clip_plan'] = True
    path = '/api/campaigns/preflight' if phase == 'preflight' else '/api/campaigns'
    response = f.client.post(path, json=body)
    assert response.status_code == (200 if phase == 'preflight' else 400), response.json
    assert not response.json.get('ok', False)
    assert response.json['account_issues'][0]['code'] == code
    assert not response.json.get('preflight_id')
    assert QUALITY in f.queries
    assert QUOTA not in f.queries
    f.catalog.assert_not_called()
    no_effects(f, receipt)


@pytest.mark.parametrize('status,headers,code,retry', [
    (401, {}, 'authentication', None), (403, {}, 'forbidden', None),
    (404, {}, 'unknown', None), (429, {'Retry-After': '17'}, 'rate_limit', 17),
    (-1, {}, 'timeout', None),
    (403, {'X-Blocked-Reason': 'app_check'}, 'app_check', None),
])
@pytest.mark.parametrize('reviewed', [False, True])
def test_quality_http_failure_preserves_cause(admission, status, headers, code, retry, reviewed):
    f = admission
    receipt = review(f) if reviewed else None
    f.queries.clear()
    f.catalog.reset_mock()
    f.quality = minute_api.HttpResponse(status, 'ERRO: timed out private-quality-body', headers)
    body = {**f.body, **({'preflight_id': receipt} if receipt else {})}
    response = f.client.post('/api/campaigns' if reviewed else '/api/campaigns/preflight', json=body)
    assert response.status_code == (400 if reviewed else 200), response.json
    issue = response.json['account_issues'][0]
    assert issue['code'] == code
    assert issue.get('http_status') == (status if status > 0 else None)
    assert issue.get('retry_after_seconds') == retry
    assert not issue['restriction_confirmed']
    assert 'private-quality-body' not in response.get_data(as_text=True)
    assert QUOTA not in f.queries
    f.catalog.assert_not_called()
    no_effects(f, receipt)


@pytest.mark.parametrize('disabled,code', [('account', 'restricted'), ('organization', 'access_paused')])
@pytest.mark.parametrize('reviewed', [False, True])
def test_confirmed_disabled_precedes_failed_quality_and_quota(admission, disabled, code, reviewed):
    f = admission
    receipt = review(f) if reviewed else None
    f.queries.clear()
    if disabled == 'account':
        f.profile['disabled'] = True
    else:
        f.profile['organizations'][1]['disabled'] = True
    f.quality = minute_api.HttpResponse(503, 'unavailable private-quality-body', {})
    response = f.client.post('/api/campaigns' if reviewed else '/api/campaigns/preflight',
                             json={**f.body, **({'preflight_id': receipt} if receipt else {})})
    assert response.status_code == (400 if reviewed else 200), response.json
    if disabled == 'account' and not reviewed:
        # Preserve the route's existing handling of confirmed account restriction.
        assert response.json['removed_accounts'] == [f.email]
        f.ban.assert_called_once()
    else:
        assert response.json['account_issues'][0]['code'] == code
    assert QUALITY not in f.queries
    assert QUOTA not in f.queries
    no_effects(f, receipt, confirmed_removal=disabled == 'account' and not reviewed)


def test_active_review_and_start_revalidate_before_runner(admission):
    f = admission
    receipt = review(f)
    f.queries.clear()
    response = f.client.post('/api/campaigns', json={**f.body, 'preflight_id': receipt})
    assert response.status_code == 200, response.json
    assert f.queries == ['/api/v1/users/me', QUALITY, QUOTA]
    f.runner.start.assert_called_once()
    for operation in f.forbidden:
        operation.assert_not_called()


@pytest.mark.parametrize('text', ['broken', '[]', '{}', '{"userState":"unknown"}'])
def test_cached_approved_quota_never_bypasses_quality(admission, text):
    f = admission
    session = server.Session.from_email(f.email)
    session._quota_cache[ORG] = (minute_api.time.monotonic(), dict(APPROVED))
    f.quality = minute_api.HttpResponse(200, text, {})
    with pytest.raises(minute_api.AuthError) as caught:
        session.check_recording_authorization(ORG)
    assert caught.value.account_issue_code == 'invalid_response'
    assert QUOTA not in f.queries
    no_effects(f)


def test_transport_failure_status_is_preserved_by_gate(admission):
    f = admission
    f.quality = minute_api.HttpResponse(-1, 'ERRO: timed out private-quality-body', {})
    session = server.Session.from_email(f.email)
    with pytest.raises(minute_api.AuthError) as caught:
        session.check_recording_authorization(ORG)
    assert caught.value.http_status == -1
    assert caught.value.account_issue_code == 'timeout'
    assert 'private-quality-body' not in str(caught.value)
    no_effects(f)

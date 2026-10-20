"""One rejected bearer permits one replay; all credentials/transports are inert."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moneymin import appcheck, minute_api


class AuthReplayBoundTests(unittest.TestCase):
    def setup_session(self, stack):
        session = minute_api.Session({'email': 'fixture@example.invalid',
            'idToken': 'fixture-old', 'expires_at': 4102444800})
        session._live = True
        stack.enter_context(patch.object(session, '_check_write_policy'))
        stack.enter_context(patch.object(session, '_bearer', side_effect=lambda: session.data['idToken']))
        stack.enter_context(patch.object(minute_api.device_profile, 'get_profile',
            return_value=SimpleNamespace(headers=lambda **_kw: {})))
        stack.enter_context(patch.object(
            appcheck, 'get_app_check_header',
            return_value={'X-Firebase-AppCheck': 'fixture-appcheck'}))
        stack.enter_context(patch.object(appcheck, 'is_app_check_rejection', return_value=False))
        return session

    def test_second_401_is_returned_without_password_login_or_third_transport(self):
        routes = [('POST', '/api/v1/uploads?org_key=fixture', {'session_id': 'same-sid'}),
                  ('POST', '/api/v1/storage/sas/blobs', {'files': [{'filename': 'same.mp4'}]}),
                  ('PATCH', '/api/v1/uploads/fixture/complete', {'size_bytes': 21}),
                  ('GET', '/api/v1/users/me', None)]
        for detailed in (False, True):
            for method, path, body in routes:
                with self.subTest(detailed=detailed, route=path), ExitStack() as stack:
                    session = self.setup_session(stack)
                    calls = []
                    def transport(url, verb, *, headers, body):
                        calls.append((url, verb, dict(headers), body))
                        return minute_api.HttpResponse(401, 'fixture-rejected', {}) if detailed else (401, 'fixture-rejected')
                    def refresh_token():
                        session.data['idToken'] = 'fixture-refreshed'
                    refresh = stack.enter_context(patch.object(session, 'refresh', side_effect=refresh_token))
                    login = stack.enter_context(patch.object(session, '_relogin'))
                    stack.enter_context(patch.object(minute_api, '_request_detailed' if detailed else '_request', side_effect=transport))
                    result = (session.request_detailed if detailed else session.request)(method, path, body)
                    self.assertEqual(result.status if detailed else result[0], 401)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0][0:2], calls[1][0:2])
                    self.assertIs(calls[0][3], body)
                    self.assertIs(calls[1][3], body)
                    self.assertEqual(calls[0][2]['Authorization'], 'Bearer fixture-old')
                    self.assertEqual(calls[1][2]['Authorization'], 'Bearer fixture-refreshed')
                    refresh.assert_called_once()
                    login.assert_not_called()
                    self.assertFalse(session._refreshing)

    def test_successful_replay_keeps_response_and_single_refresh(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed), ExitStack() as stack:
                session = self.setup_session(stack)
                responses = [minute_api.HttpResponse(status, 'fixture', {}) if detailed else (status, 'fixture') for status in (401, 201)]
                request = stack.enter_context(patch.object(minute_api, '_request_detailed' if detailed else '_request', side_effect=responses))
                refresh = stack.enter_context(patch.object(session, 'refresh'))
                login = stack.enter_context(patch.object(session, '_relogin'))
                result = (session.request_detailed if detailed else session.request)('POST', '/fixture', {})
                self.assertEqual(result.status if detailed else result[0], 201)
                self.assertEqual(request.call_count, 2)
                refresh.assert_called_once()
                login.assert_not_called()
                self.assertFalse(session._refreshing)

    def test_refresh_error_preserves_original_exception_and_does_not_replay(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed), ExitStack() as stack:
                session = self.setup_session(stack)
                original = minute_api.AuthError('fixture-refresh-unavailable', code='network')
                response = minute_api.HttpResponse(401, 'fixture', {}) if detailed else (401, 'fixture')
                request = stack.enter_context(patch.object(minute_api, '_request_detailed' if detailed else '_request', return_value=response))
                stack.enter_context(patch.object(session, 'refresh', side_effect=original))
                login = stack.enter_context(patch.object(session, '_relogin'))
                with self.assertRaises(minute_api.AuthError) as caught:
                    (session.request_detailed if detailed else session.request)('PATCH', '/fixture', {})
                self.assertIs(caught.exception, original)
                request.assert_called_once()
                login.assert_not_called()
                self.assertFalse(session._refreshing)

    def test_header_only_appcheck_401_is_returned_without_auth_refresh_or_replay(self):
        headers = [('X-Blocked-Reason', 'app_check'),
                   ('x-blocked-reason', 'app_check'),
                   ('X-BLOCKED-REASON', 'app_check'),
                   ('x-BlOcKeD-rEaSoN', 'App_Check')]
        routes = [('POST', '/api/v1/uploads?org_key=fixture', {'session_id': 'same-sid'}),
                  ('PATCH', '/api/v1/uploads/fixture/complete', {'size_bytes': 21}),
                  ('GET', '/api/v1/uploads/fixture', None)]
        for key, value in headers:
            for method, path, body in routes:
                with self.subTest(header=key, value=value, route=path), ExitStack() as stack:
                    session = self.setup_session(stack)
                    original_tokens = dict(session.data)
                    response = minute_api.HttpResponse(
                        401, '{"detail":"Request cannot be completed."}', {key: value})
                    request = stack.enter_context(patch.object(
                        minute_api, '_request_detailed', return_value=response))
                    refresh = stack.enter_context(patch.object(session, 'refresh'))
                    login = stack.enter_context(patch.object(session, '_relogin'))
                    result = session.request_detailed(method, path, body)
                    self.assertIs(result, response)
                    self.assertEqual(result.headers, {key: value})
                    self.assertEqual(session.data, original_tokens)
                    request.assert_called_once()
                    self.assertIs(request.call_args.kwargs['body'], body)
                    refresh.assert_not_called()
                    login.assert_not_called()
                    self.assertFalse(getattr(session, '_refreshing', False))

    def test_non_appcheck_header_keeps_legitimate_auth_refresh_and_single_replay(self):
        headers = [{}, {'X-Blocked-Reason': 'rate_limit'},
                   {'x-blocked-reason': 'app_check_required'},
                   {'X-Other-Reason': 'app_check'}]
        for response_headers in headers:
            with self.subTest(headers=response_headers), ExitStack() as stack:
                session = self.setup_session(stack)
                rejected = minute_api.HttpResponse(
                    401, '{"detail":"Invalid Firebase ID token"}', response_headers)
                accepted = minute_api.HttpResponse(201, '{"id":"fixture-upload"}', {})
                snapshots = []
                def transport(url, method, *, headers, body):
                    snapshots.append(dict(headers))
                    return rejected if len(snapshots) == 1 else accepted
                def refresh_token():
                    session.data['idToken'] = 'fixture-refreshed'
                request = stack.enter_context(patch.object(
                    minute_api, '_request_detailed', side_effect=transport))
                refresh = stack.enter_context(patch.object(
                    session, 'refresh', side_effect=refresh_token))
                login = stack.enter_context(patch.object(session, '_relogin'))
                result = session.request_detailed('POST', '/api/v1/uploads', {})
                self.assertIs(result, accepted)
                self.assertEqual(request.call_count, 2)
                self.assertEqual([row['Authorization'] for row in snapshots],
                                 ['Bearer fixture-old', 'Bearer fixture-refreshed'])
                refresh.assert_called_once()
                login.assert_not_called()
                self.assertFalse(session._refreshing)

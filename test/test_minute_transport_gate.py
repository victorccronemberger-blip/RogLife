"""Local transport-policy contracts with inert sessions and no network access."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moneymin import appcheck, config, minute_api, transport, vpn


class MinuteTransportGateTests(unittest.TestCase):
    def fixture(self, stack, *, require_curl=True, transport_kind='urllib', live=True):
        session = minute_api.Session({
            'email': 'transport-policy@example.invalid',
            'idToken': 'fixture-only',
            'expires_at': 4102444800,
        })
        session._live = live
        stack.enter_context(patch.object(config, 'REQUIRE_CURL', require_curl))
        stack.enter_context(patch.object(vpn, 'ENFORCE', False))
        spies = {
            'kind': stack.enter_context(patch.object(transport, 'kind', return_value=transport_kind)),
            'profile': stack.enter_context(patch.object(
                minute_api.device_profile, 'get_profile',
                return_value=SimpleNamespace(headers=lambda **_kw: {}))),
            'appcheck': stack.enter_context(patch.object(
                appcheck, 'get_app_check_header',
                return_value={'X-Firebase-AppCheck': 'fixture-only'})),
            'device': stack.enter_context(patch.object(
                minute_api, '_align_physical_device_headers', return_value=None)),
        }
        for name in ('refresh', '_relogin', 'warmup'):
            spies[name] = stack.enter_context(patch.object(
                session, name, side_effect=AssertionError('Unexpected authentication or initialization')))
        for name in ('_request', '_request_detailed'):
            spies[name] = stack.enter_context(patch.object(
                minute_api, name, side_effect=AssertionError('Unexpected network dispatch')))
        return session, spies

    def assert_transport_refusal(self, error):
        self.assertEqual(str(error), 'Transporte obrigatório indisponível.')
        self.assertEqual(error.account_issue_code, 'service')
        self.assertIsNone(error.http_status)
        self.assertIsNone(error.provider_error_code)
        self.assertIs(error.remote_effect_possible, False)

    def test_required_curl_rejects_urllib_before_authentication_or_dispatch(self):
        routes = (
            ('GET', '/api/v1/users/me', None),
            ('POST', '/api/v1/uploads?org_key=fixture', {}),
            ('PATCH', '/api/v1/uploads/fixture/complete', {'size_bytes': 1}),
        )
        for detailed in (False, True):
            for live in (False, True):
                for method, path, body in routes:
                    with self.subTest(detailed=detailed, live=live, method=method), ExitStack() as stack:
                        session, spies = self.fixture(stack, live=live)
                        original_data = dict(session.data)
                        with self.assertRaises(minute_api.AuthError) as caught:
                            (session.request_detailed if detailed else session.request)(method, path, body)
                        self.assert_transport_refusal(caught.exception)
                        self.assertEqual(session.data, original_data)
                        self.assertEqual(session._live, live)
                        spies['kind'].assert_called_once_with()
                        for name, spy in spies.items():
                            if name != 'kind':
                                spy.assert_not_called()

    def test_compatible_transport_or_disabled_requirement_reaches_inert_dispatch(self):
        for detailed in (False, True):
            for required, kind in ((True, 'curl'), (False, 'urllib')):
                with self.subTest(detailed=detailed, required=required, kind=kind), ExitStack() as stack:
                    session, spies = self.fixture(stack, require_curl=required, transport_kind=kind)
                    name = '_request_detailed' if detailed else '_request'
                    response = minute_api.HttpResponse(200, '{"fixture":true}', {}) if detailed else (200, '{"fixture":true}')
                    spies[name].side_effect = None
                    spies[name].return_value = response
                    actual = (session.request_detailed if detailed else session.request)('GET', '/api/v1/users/me')
                    if detailed:
                        self.assertIs(actual, response)
                    else:
                        self.assertEqual(actual, response)
                    spies[name].assert_called_once()
                    self.assertEqual(spies[name].call_args.args, (config.BASE_URL + '/api/v1/users/me', 'GET'))
                    self.assertIsNone(spies[name].call_args.kwargs['body'])
                    self.assertEqual(spies[name].call_args.kwargs['headers']['Authorization'], 'Bearer fixture-only')
                    spies['_request' if detailed else '_request_detailed'].assert_not_called()
                    for name in ('refresh', '_relogin', 'warmup'):
                        spies[name].assert_not_called()

    def test_transport_loss_during_preparation_is_rechecked_before_dispatch(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed), ExitStack() as stack:
                session, spies = self.fixture(stack, transport_kind='curl')
                spies['kind'].side_effect = ['curl', 'urllib']
                with self.assertRaises(minute_api.AuthError) as caught:
                    (session.request_detailed if detailed else session.request)('GET', '/api/v1/users/me')
                self.assert_transport_refusal(caught.exception)
                self.assertEqual(spies['kind'].call_count, 2)
                spies['profile'].assert_called_once()
                spies['appcheck'].assert_called_once()
                for name in ('_request', '_request_detailed', 'refresh', '_relogin', 'warmup'):
                    spies[name].assert_not_called()


if __name__ == '__main__':
    unittest.main()

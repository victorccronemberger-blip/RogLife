import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from moneymin import config, minute_api, vpn
from moneymin.minute_api import AuthError, Session
from moneymin.service_policy import RecordingPolicy
from moneymin.web.account_issues import account_issue


POLICY = {"configVersion": 1, "minDurationMs": 1000,
          "maxDurationMs": 10000, "backlogCapMs": 60000}


class RecordingPolicyTests(unittest.TestCase):
    def test_valid_policy_is_immutable_and_does_not_alias_payload(self):
        payload = dict(POLICY)
        policy = RecordingPolicy.parse(payload)
        payload["maxDurationMs"] = 5
        self.assertEqual(policy.max_duration_ms, 10000)
        snapshot = policy.limits()
        snapshot["max_duration_ms"] = 3
        self.assertEqual(policy.max_duration_ms, 10000)

    def test_malformed_contracts_are_rejected_without_coercion(self):
        for payload in ([], {}, {**POLICY, "minDurationMs": True},
                        {**POLICY, "maxDurationMs": "10000"},
                        {**POLICY, "minDurationMs": 20000},
                        {**POLICY, "backlogCapMs": 0},
                        {**POLICY, "configVersion": 0}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                RecordingPolicy.parse(payload)

    def test_backlog_and_future_recordings_are_rejected(self):
        policy = RecordingPolicy.parse(POLICY)
        now = datetime(2026, 9, 18, tzinfo=timezone.utc)
        policy.validate_recording_time((now - timedelta(seconds=3)).isoformat(), 2000, now.timestamp())
        for value in (None, "invalid", "2026-09-18T00:00:00",
                      (now - timedelta(seconds=61)).isoformat(), now.isoformat()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.validate_recording_time(value, 2000, now.timestamp())


class SessionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="qmoney-service-policy-")))
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(minute_api, "_VERSION_GATE_MEMORY", {}))
        self.stack.enter_context(patch.object(vpn, "ENFORCE", False))
        self.stack.enter_context(patch.object(config, "REQUIRE_CURL", False))
        self.clock = self.stack.enter_context(patch.object(minute_api.time, "monotonic", return_value=100.0))
        self.stack.enter_context(patch.object(minute_api.device_profile, "get_profile",
                                            return_value=SimpleNamespace(device_model="fixture")))
        self.session = Session({"idToken": "fixture", "email": "test@example.invalid"})
        self.fetch = self.stack.enter_context(patch.object(self.session, "fetch_recording_config",
                                                         return_value=(200, json.dumps(POLICY))))
        self.camera_patch = patch.object(self.session, "camera_model_allowed", return_value=True)
        self.camera = self.stack.enter_context(self.camera_patch)
        self.stack.enter_context(patch.object(self.session, "version_gate", return_value=None))
        self.state = self.stack.enter_context(patch.object(self.session, "org_state", return_value={
            "blocked": False, "userState": "active", "cameraSources": ["built-in"]}))
        self.geo = self.stack.enter_context(patch.object(self.session, "recording_geo", return_value={
            "recordingAuthorization": "APPROVED", "canUpload": True, "blockedReason": None}))
        self.opened = self.stack.enter_context(patch.object(self.session, "app_opened"))

    def check(self, body=None):
        self.session._check_write_policy("POST", "/api/v1/uploads?org_key=fixture",
                                         body or {"duration_ms": 2000, "meta": {"source": "ego", "cameras": [{"source": "builtin"}]},
                                                  "recorded_at": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()})

    def test_success_is_cached_and_no_analytics_is_published(self):
        self.session.warmup()
        self.session.warmup()
        self.fetch.assert_called_once()
        self.opened.assert_not_called()

    def test_failure_is_retried_after_backoff(self):
        self.fetch.side_effect = [(503, "unavailable"), (200, json.dumps(POLICY))]
        self.session.warmup()
        self.assertIn("recording_config", self.session.initialization_errors)
        with self.assertRaises(AuthError):
            self.check()
        self.assertEqual(self.fetch.call_count, 1)
        self.clock.return_value = 106.0
        self.check()
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(self.session.initialization_errors, {})

    def test_expired_policy_cannot_authorize_after_refresh_failure(self):
        self.check()
        self.clock.return_value = 161.0
        self.fetch.return_value = (200, "[]")
        with self.assertRaises(AuthError) as caught:
            self.check()
        self.assertEqual(caught.exception.account_issue_code, "invalid_response")

    def test_recording_config_failure_preserves_actual_cause_during_backoff(self):
        for status, body, code in (
                (403, '{"detail":"User account is disabled."}', "restricted"),
                (403, '{"detail":{"error":"appcheck_required"}}', "app_check"),
                (403, '{"detail":"Forbidden"}', "forbidden"),
                (401, "Unauthorized", "authentication"),
                (429, "Slow down", "rate_limit"),
                (503, "Unavailable", "service")):
            with self.subTest(status=status, code=code):
                self.session._recording_checked_at = None
                self.session._recording_retry_at = 0
                self.fetch.return_value = (status, body)
                self.fetch.reset_mock()
                for _ in range(2):
                    with self.assertRaises(AuthError) as caught:
                        self.check()
                    self.assertEqual(caught.exception.account_issue_code, code)
                self.fetch.assert_called_once()

    def test_successful_policy_refresh_clears_previous_failure_cause(self):
        self.fetch.side_effect = [(503, "Unavailable"), (200, json.dumps(POLICY))]
        with self.assertRaises(AuthError):
            self.check()
        self.clock.return_value = 106
        self.check()
        self.assertEqual(self.session._initialization_causes, {})

    def test_remote_limits_are_isolated_even_for_same_email(self):
        original = config.recording_limits()
        self.session.warmup()
        other = Session(dict(self.session.data))
        with patch.object(other, "fetch_recording_config", return_value=(200, json.dumps({**POLICY, "maxDurationMs": 3000}))), \
             patch.object(other, "camera_model_allowed", return_value=True):
            other.warmup()
        self.assertEqual(self.session.recording_policy.max_duration_ms, 10000)
        self.assertEqual(other.recording_policy.max_duration_ms, 3000)
        self.assertEqual(config.recording_limits(), original)

    def test_identity_change_is_rejected_and_preserves_previous_policy(self):
        self.session.warmup()
        policy = self.session.recording_policy
        checked = self.session._recording_checked_at
        with self.assertRaises(AuthError):
            self.session.with_email("other@example.invalid")
        self.assertIs(self.session.recording_policy, policy)
        self.assertEqual(self.session._recording_checked_at, checked)
        self.assertEqual(self.session.email, "test@example.invalid")

    def test_policy_issue_is_not_a_ban_or_password_failure(self):
        issue = account_issue("fixture@example.invalid", AuthError("private diagnostic", code="policy"))
        self.assertEqual(issue["code"], "policy")
        self.assertFalse(issue["restriction_confirmed"])
        self.assertFalse(issue["retryable"])

    def test_http_error_cannot_supply_an_authorizing_policy(self):
        camera = {"policyVersion": 1, "iosAllowModels": [], "iosDeniedModels": [],
                  "androidAllowModels": ["fixture"], "androidAllowModelPatterns": [],
                  "normalization": {
                      "trimWhitespace": True, "lowercase": False, "collapseInternalWhitespace": True}}
        with patch.object(self.session, "get", return_value=(503, json.dumps(camera))):
            self.assertEqual(self.session.camera_policy(), {})
        with patch.object(self.session, "get", return_value=(500, '{"userState":"active"}')):
            self.assertEqual(self.session.quality_state("fixture"), {})

    def test_invalid_camera_policy_is_unknown_not_permission(self):
        with patch.object(self.session, "get", return_value=(200, '{"policyVersion":true}')):
            self.assertEqual(self.session.camera_policy(), {})
        incomplete = {"policyVersion": 1, "androidAllowModels": ["x"],
                      "androidAllowModelPatterns": [], "normalization": {
                          "trimWhitespace": True, "lowercase": True,
                          "collapseInternalWhitespace": True}}
        with patch.object(self.session, "get", return_value=(200, json.dumps(incomplete))):
            self.assertEqual(self.session.camera_policy(), {})

    def test_camera_decision_matches_hermes_normalize_and_patterns(self):
        session = Session({"idToken": "fixture", "email": "camera@example.invalid"})
        policy = {
            "policyVersion": 1,
            "iosAllowModels": [],
            "iosDeniedModels": ["iPhone"],
            "androidAllowModels": ["sm-s918b"],
            "androidAllowModelPatterns": [r"pixel\s*8"],
            "normalization": {
                "trimWhitespace": False, "lowercase": False,
                "collapseInternalWhitespace": False,
            },
        }
        with patch.object(session, "get", return_value=(200, json.dumps(policy))):
            # Flags remotas False não desligam a normalização do APK.
            self.assertIs(session.camera_model_allowed("  SM-S918B  "), True)
            self.assertIs(session.camera_model_allowed("Pixel   8 Pro"), True)
            self.assertIs(session.camera_model_allowed("SM-G991B"), False)
            # Deny iOS não afeta decisão Android.
            self.assertIs(session.camera_model_allowed("iPhone"), False)

    def test_ios_camera_model_uses_deny_precedence_and_exact_allow_list(self):
        session = Session({'idToken': 'fixture', 'email': 'ios@example.invalid'})
        policy = {'policyVersion': 1, 'iosAllowModels': ['iphone17,3', 'denied'],
                  'iosDeniedModels': ['denied'], 'androidAllowModels': ['android fixture'],
                  'androidAllowModelPatterns': ['iphone.*'],
                  'normalization': {'trimWhitespace': True, 'lowercase': True, 'collapseInternalWhitespace': True}}
        with patch.object(session, 'get', return_value=(200, json.dumps(policy))):
            self.assertIs(session.camera_model_allowed('  iPhone17,3 ', platform='ios'), True)
            for model in ('denied', 'iphone17,4', 'android fixture', ''):
                with self.subTest(model=model):
                    self.assertIs(session.camera_model_allowed(model, platform='ios'), False)
            self.assertIs(session.camera_model_allowed('iphone17,3', platform='unknown'), False)
        with patch.object(session, 'get', return_value=(503, 'unavailable')):
            self.assertIsNone(session.camera_model_allowed('iphone17,3', platform='ios'))

    def ios_body(self):
        return {'duration_ms': 2000,
                'recorded_at': (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
                'meta': {'source': 'ego', 'platform': {'os': 'ios'},
                         'device': {'model': 'DECLARED IOS FIXTURE'}, 'cameras': [{'name': 'ultra-wide'}]}}

    def test_ios_uses_capture_model_and_preserves_missing_camera_source(self):
        body = self.ios_body()
        self.camera.side_effect = lambda model, **kwargs: kwargs.get('platform') == 'ios' and model == 'DECLARED IOS FIXTURE'
        self.state.return_value = {'blocked': False, 'userState': 'active', 'cameraSources': ['external']}
        self.check(body)
        self.camera.assert_any_call('DECLARED IOS FIXTURE', platform='ios')
        self.assertNotIn('source', body['meta']['cameras'][0])
        self.assertIs(self.session.device_camera_allowed, False)

    def test_ios_retains_model_geo_org_duration_and_time_gates(self):
        for allowed, code in ((False, 'policy'), (None, 'service')):
            with self.subTest(allowed=allowed):
                self.camera.return_value = allowed
                with self.assertRaises(AuthError) as caught:
                    self.check(self.ios_body())
                self.assertEqual(caught.exception.account_issue_code, code)
        self.camera.return_value = True
        self.geo.return_value = {'recordingAuthorization': 'BLOCKED', 'canUpload': False, 'blockedReason': 'manual'}
        with self.assertRaises(AuthError):
            self.check(self.ios_body())
        self.geo.return_value = {'recordingAuthorization': 'APPROVED', 'canUpload': True, 'blockedReason': None}
        self.state.return_value = {'blocked': True, 'userState': 'active', 'cameraSources': ['external']}
        with self.assertRaises(AuthError):
            self.check(self.ios_body())
        self.state.return_value = {'blocked': False, 'userState': 'active', 'cameraSources': ['external']}
        for key, value in (('duration_ms', 10001), ('recorded_at', 'invalid')):
            body = self.ios_body()
            body[key] = value
            with self.subTest(key=key), self.assertRaises(AuthError):
                self.check(body)

    def test_ios_missing_model_or_invalid_camera_is_not_authorized(self):
        for key, value in (('device', {}), ('cameras', []), ('cameras', [None]),
                           ('cameras', [{'source': 'ego'}]), ('cameras', [{'source': None}])):
            body = self.ios_body()
            body['meta'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(AuthError):
                self.check(body)

    def test_error_response_cannot_masquerade_as_a_profile(self):
        with patch.object(self.session, "request", return_value=(503, '{"organizations":[]}')):
            self.assertEqual(self.session.me(), {})

    def test_denied_or_unknown_camera_never_authorizes(self):
        for allowed, code in ((False, "policy"), (None, "service")):
            with self.subTest(allowed=allowed):
                self.session._recording_checked_at = None
                self.session._recording_retry_at = 0
                self.camera.return_value = allowed
                with self.assertRaises(AuthError) as caught:
                    self.check()
                self.assertEqual(caught.exception.account_issue_code, code)

    def test_bad_duration_blocked(self):
        for duration in (True, "2000", 999, 10001):
            with self.subTest(duration=duration), self.assertRaises(AuthError):
                self.check({"duration_ms": duration, "meta": {"source": "ego", "cameras": [{"source": "builtin"}]}})

    def test_ego_format_and_camera_origin_are_separate_contracts(self):
        # Forma do metadata.json usado em produção, com os valores do OpenAPI.
        self.check()
        for cameras in (None, [], [None], [{"source": "ego"}],
                        [{"source": "external"}],
                        [{"source": "builtin"}, {"source": "external"}]):
            with self.subTest(cameras=cameras), self.assertRaises(AuthError):
                self.check({"duration_ms": 2000,
                            "recorded_at": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
                            "meta": {"source": "ego", "cameras": cameras}})
        self.state.return_value = {"blocked": False, "userState": "active", "cameraSources": ["external"]}
        self.check({"duration_ms": 2000,
                    "recorded_at": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
                    "meta": {"source": "ego", "cameras": [{"source": "external"}]}})

    def test_source_policy_failure_has_actionable_public_message(self):
        from moneymin.web.runner import friendly_campaign_error
        message = friendly_campaign_error("Origem da gravação não permitida pela organização.")
        self.assertIn("origem de câmera", message)
        self.assertNotIn("Valide a conta", message)

    def test_org_restrictions_and_sources_are_enforced(self):
        for state in ({"blocked": True, "userState": "on_hold", "cameraSources": ["built-in"]},
                      {"blocked": False, "userState": "unknown", "cameraSources": ["built-in"]},
                      {"blocked": False, "userState": "active", "cameraSources": []},
                      {"blocked": False, "userState": "active", "cameraSources": ["external"]}):
            with self.subTest(state=state):
                self.state.return_value = state
                with self.assertRaises(AuthError):
                    self.check()

    def test_vpn_rechecked_after_initialization_for_both_transports(self):
        self.session.warmup()
        with patch.object(vpn, "ENFORCE", True), patch.object(vpn, "vpn_active", return_value=True), \
             patch.object(minute_api, "_request") as plain, patch.object(minute_api, "_request_detailed") as detailed:
            for request in (self.session.request, self.session.request_detailed):
                with self.assertRaises(AuthError):
                    request("GET", "/api/v1/users/me")
            plain.assert_not_called()
            detailed.assert_not_called()

    def test_production_defaults_enforce_vpn_and_curl(self):
        with patch.dict("os.environ", {"MINUTE_VPN_ENFORCE": "", "MINUTE_REQUIRE_CURL": ""}, clear=False):
            self.assertTrue(vpn._env_enabled("MINUTE_VPN_ENFORCE", True))
            self.assertTrue(config._env_enabled("MINUTE_REQUIRE_CURL", True))
        with patch.dict("os.environ", {"MINUTE_VPN_ENFORCE": "0", "MINUTE_REQUIRE_CURL": "off"}):
            self.assertFalse(vpn._env_enabled("MINUTE_VPN_ENFORCE", True))
            self.assertFalse(config._env_enabled("MINUTE_REQUIRE_CURL", True))

    def test_blocked_upload_never_reaches_either_transport(self):
        self.camera.return_value = False
        with patch.object(minute_api, "_request") as plain, patch.object(minute_api, "_request_detailed") as detailed:
            for request in (self.session.request, self.session.request_detailed):
                with self.assertRaises(AuthError):
                    request("POST", "/api/v1/uploads?org_key=fixture", {"duration_ms": 2000})
            plain.assert_not_called()
            detailed.assert_not_called()

    def test_version_latch_requires_apk_detail_shape(self):
        try:
            minute_api._maybe_latch_version_gate(
                '{"detail":{"message":"please update the app","min_version":"9.9.9"}}')
            self.assertFalse(minute_api._version_gate_file().exists())
            minute_api._maybe_latch_version_gate(
                '{"detail":{"error":"app_version_too_old","minVersion":"9.9.9"}}')
            self.assertFalse(minute_api._version_gate_file().exists())
            minute_api._maybe_latch_version_gate(
                '{"detail":{"error":"app_version_too_old","min_version":"not-a-version"}}')
            self.assertFalse(minute_api._version_gate_file().exists())
            minute_api._maybe_latch_version_gate(
                '{"detail":{"error":"app_version_too_old","min_version":"9.9.9"}}')
            data = json.loads(minute_api._version_gate_file().read_text(encoding="utf-8"))
            self.assertEqual(data["minVersion"], "9.9.9")
            self.assertTrue(minute_api._version_gate_blocks())
        finally:
            with patch.object(config, "APP_VERSION", "9.9.9"):
                minute_api._maybe_latch_version_gate("", clear=True)

    def test_latched_version_blocks_all_mutations_not_reads(self):
        try:
            minute_api._maybe_latch_version_gate(
                '{"detail":{"error":"app_version_too_old","min_version":"9.9.9"}}')
            with self.assertRaises(AuthError) as caught:
                self.session._check_write_policy(
                    "POST", "/api/v1/organizations/join", {"code": "X"})
            self.assertEqual(caught.exception.account_issue_code, "version")
            previous = minute_api._version_gate_file().read_bytes()
            with self.assertRaises(AuthError):
                self.session.clear_version_gate()
            self.assertEqual(minute_api._version_gate_file().read_bytes(), previous)
            self.assertTrue(minute_api._version_gate_blocks())
            # Leituras seguem liberadas para diagnóstico.
            self.session._check_write_policy("GET", "/api/v1/users/me", None)
        finally:
            with patch.object(config, "APP_VERSION", "9.9.9"):
                minute_api._maybe_latch_version_gate("", clear=True)

    def test_minute_403_errors_are_typed(self):
        self.assertEqual(
            minute_api._classify_minute_403(
                '{"detail":{"error":"app_version_too_old","min_version":"2.0.0"}}'),
            "version")
        self.assertEqual(
            minute_api._classify_minute_403('{"detail":{"error":"device"}}'), "device")
        self.assertEqual(
            minute_api._classify_minute_403('{"detail":{"error":"uber-device"}}'),
            "uber_device")
        self.assertIsNone(minute_api._classify_minute_403('{"detail":{"error":"other"}}'))
        err = minute_api._auth_failure(
            403, '{"detail":{"error":"uber-device"}}', "Consulta")
        self.assertEqual(err.account_issue_code, "uber_device")
        issue = account_issue("fixture@example.invalid", err)
        self.assertEqual(issue["code"], "uber_device")
        self.assertFalse(issue["restriction_confirmed"])

    def test_recording_geo_blocks_upload_before_transport(self):
        cases = (
            ({}, "service"),
            ({"recordingAuthorization": "APPROVED", "canUpload": False,
              "blockedReason": "quota_exceeded"}, "policy"),
            ({"recordingAuthorization": "REQUIRES_LOCATION", "canUpload": True,
              "blockedReason": None}, "policy"),
            ({"recordingAuthorization": "BLOCKED", "canUpload": True,
              "blockedReason": None}, "policy"),
            ({"canUpload": True}, "service"),
            ({"recordingAuthorization": "REQUIRES_LOCATION", "canUpload": False,
              "blockedReason": None}, "policy"),
            ({"recordingAuthorization": "BLOCKED", "canUpload": False,
              "blockedReason": "geo_restricted"}, "policy"),
            ({"recordingAuthorization": "BLOCKED", "canUpload": False,
              "blockedReason": "quota_exceeded"}, "policy"),
        )
        for quota, code in cases:
            with self.subTest(quota=quota):
                self.geo.return_value = quota
                with patch.object(minute_api, "_request") as plain:
                    with self.assertRaises(AuthError) as caught:
                        self.check()
                    self.assertEqual(caught.exception.account_issue_code, code)
                    plain.assert_not_called()

    def test_semver_rejects_malformed_values(self):
        for value in ("1.23", "garbage 99", "1.2.3-beta4", "1..2", "1.2.3\n", None):
            with self.subTest(value=value):
                self.assertIsNone(minute_api._parse_semver_or_none(value))
        self.assertEqual(minute_api._parse_semver_or_none("1.23.4"), (1, 23, 4))

    def test_quota_missing_required_authorization_is_not_cached(self):
        for payload in ({"canUpload": True}, {"recordingAuthorization": "APPROVED"},
                        {"recordingAuthorization": "APPROVED", "canUpload": True}):
            with self.subTest(payload=payload), patch.object(
                    self.session, "request_detailed",
                    return_value=minute_api.HttpResponse(200, json.dumps(payload), {})):
                self.assertEqual(Session.recording_geo(self.session, "fixture"), {})
                self.assertNotIn("fixture", self.session._quota_cache)

    def test_diagnostic_auth_is_available_with_latched_version(self):
        self.session._live = True
        with patch.object(minute_api, "_version_gate_blocks", return_value=True), \
             patch.object(self.session, "request_detailed", return_value=minute_api.HttpResponse(200, '{"organizations":[]}', {})):
            self.assertEqual(self.session.ensure_auth(), {"organizations": []})
            with self.assertRaises(AuthError):
                self.session._check_write_policy("POST", "/api/v1/organizations/join", {})

    def test_app_opened_confirms_success_and_resets_on_account_change(self):
        with patch.object(config, "PUBLISH_APP_OPENED", True):
            self.opened.return_value = (503, "unavailable")
            self.session.warmup()
            self.assertFalse(self.session._app_opened_published)
            self.clock.return_value = 161.0
            self.opened.return_value = (204, "")
            self.session.warmup()
            self.assertTrue(self.session._app_opened_published)
            with self.assertRaises(AuthError):
                self.session.with_email("other@example.invalid")
            self.assertTrue(self.session._app_opened_published)

    def test_unknown_vpn_state_blocks_before_transport(self):
        with patch.object(vpn, "ENFORCE", True), patch.object(vpn, "vpn_active", return_value=None), \
             patch.object(minute_api, "_request") as transport:
            with self.assertRaises(AuthError) as caught:
                self.session.request("GET", "/api/v1/users/me")
            self.assertEqual(caught.exception.account_issue_code, "service")
            transport.assert_not_called()


class RecordingAuthorizationDiagnosticsTests(unittest.TestCase):
    """Real quota parser/gate with isolated state and no external operations."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="qmoney-quota-")))
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "APP_VERSION", "1.29.0"))
        self.stack.enter_context(patch.object(minute_api, "_VERSION_GATE_MEMORY", {}))
        self.session = Session({"idToken": "fixture", "email": "quota@example.invalid"})
        self.response = minute_api.HttpResponse(200, json.dumps({
            "recordingAuthorization": "APPROVED", "canUpload": True, "blockedReason": None}), {})
        # Only quota responses vary in these diagnostics. Profile and quality
        # still pass through the real me/org_state/checked_quality_state parsers.
        self.http = Mock(return_value=self.response)
        self.reads = []
        self.profile = {"disabled": False, "organizations": [
            {"resourceKey": org, "disabled": False}
            for org in ("fixture-org", "other-org", "unavailable-org", "approved-org")]}
        self.quality = minute_api.HttpResponse(200, json.dumps({"userState": "active"}), {})

        def detailed(method, path, body=None):
            self.assertEqual(method, "GET")
            self.assertIsNone(body)
            self.reads.append(path)
            if path == "/api/v1/users/me":
                return minute_api.HttpResponse(200, json.dumps(self.profile), {})
            if path in [f"/api/v1/organizations/{org['resourceKey']}/quality-summary"
                        for org in self.profile["organizations"]]:
                return self.quality
            self.assertIn(path, [f"/api/v1/orgs/{org['resourceKey']}/quota"
                                 for org in self.profile["organizations"]])
            return self.http(method, path)

        def request(method, path, body=None):
            response = detailed(method, path, body)
            return response.status, response.text

        self.stack.enter_context(patch.object(self.session, "request_detailed", side_effect=detailed))
        self.stack.enter_context(patch.object(self.session, "request", side_effect=request))

    def test_quota_http_failures_keep_typed_status_without_leaking_bodies(self):
        cases = (
            (401, "private-token", "authentication"),
            (403, '{"detail":"Forbidden private-token"}', "forbidden"),
            (403, '{"detail":{"error":"app_version_too_old","min_version":"1.30.0",'
                  '"message":"private-token"}}', "version"),
            (403, '{"detail":{"error":"device"}}', "device"),
            (403, '{"detail":"User account is disabled."}', "restricted"),
            (403, '{"detail":{"error":"appcheck_required"}}', "app_check"),
            (429, "private-token", "rate_limit"),
            (408, "private-token", "timeout"),
            (503, "private-token", "service"),
            (-1, "ERRO: timed out private-token", "timeout"),
            (-1, "ERRO: TLS certificate private-token", "tls"),
            (-1, "ERRO: connection refused private-token", "network"),
        )
        for status, body, code in cases:
            with self.subTest(status=status, code=code):
                self.http.return_value = minute_api.HttpResponse(status, body, {"Retry-After": "7"})
                with self.assertRaises(AuthError) as caught:
                    self.session.check_recording_authorization("fixture-org")
                error = caught.exception
                self.assertEqual(error.account_issue_code, code)
                self.assertEqual(error.http_status, status)
                self.assertEqual(error.retry_after_seconds, 7 if status == 429 else None)
                self.assertFalse(error.remote_effect_possible)
                self.assertNotIn("private-token", str(error))
                self.assertNotIn("fixture-org", self.session._quota_cache)
                if code == "version":
                    self.assertIn("1.29.0", str(error))
                    self.assertIn("1.30.0", str(error))

    def test_quota_invalid_success_has_distinct_safe_diagnosis(self):
        for body in ("private-token", "[]", "null", "{}", json.dumps({
                "recordingAuthorization": "APPROVED", "canUpload": True}), json.dumps({
                "recordingAuthorization": True, "canUpload": True, "blockedReason": None}),
                json.dumps({"recordingAuthorization": "APPROVED", "canUpload": "true",
                            "blockedReason": None}), json.dumps({
                "recordingAuthorization": "APPROVED", "canUpload": True, "blockedReason": "new-reason"})):
            with self.subTest(body=body):
                self.http.return_value = minute_api.HttpResponse(200, body, {})
                with self.assertRaises(AuthError) as caught:
                    self.session.check_recording_authorization("fixture-org")
                self.assertEqual(caught.exception.account_issue_code, "invalid_response")
                self.assertEqual(caught.exception.http_status, 200)
                self.assertNotIn("private-token", str(caught.exception))
                self.assertNotIn("fixture-org", self.session._quota_cache)

    def test_approved_quota_is_cached_per_org_without_mutating_response(self):
        self.session.check_recording_authorization("fixture-org")
        quota = self.session.recording_geo("fixture-org")
        quota["canUpload"] = False
        self.session.check_recording_authorization("fixture-org")
        self.http.assert_called_once_with("GET", "/api/v1/orgs/fixture-org/quota")
        self.session.check_recording_authorization("other-org")
        self.assertEqual(self.http.call_count, 2)

    def test_recovered_quota_drops_previous_error_and_does_not_cache_unknown(self):
        self.http.side_effect = [minute_api.HttpResponse(503, "private-token", {}), self.response]
        self.assertEqual(self.session.recording_geo("fixture-org"), {})
        self.assertIn("fixture-org", self.session._quota_failures)
        self.session.check_recording_authorization("fixture-org")
        self.assertNotIn("fixture-org", self.session._quota_failures)
        self.assertEqual(self.http.call_count, 2)

    def test_quota_failure_is_not_reused_for_another_org(self):
        self.http.side_effect = [minute_api.HttpResponse(503, "private-token", {}), self.response]
        self.assertEqual(self.session.recording_geo("unavailable-org"), {})
        self.session.check_recording_authorization("approved-org")
        self.assertNotIn("approved-org", self.session._quota_failures)

    def test_cached_authorization_expires_and_a_failed_refresh_cannot_authorize(self):
        with patch.object(minute_api.time, "monotonic", return_value=100):
            self.session.check_recording_authorization("fixture-org")
        self.http.return_value = minute_api.HttpResponse(503, "private-token", {})
        with patch.object(minute_api.time, "monotonic", return_value=161):
            with self.assertRaises(AuthError) as caught:
                self.session.check_recording_authorization("fixture-org")
        self.assertEqual(caught.exception.account_issue_code, "service")

    def test_cached_quota_cannot_bypass_fresh_quality_verification(self):
        self.session.check_recording_authorization("fixture-org")
        self.assertEqual(self.reads, [
            "/api/v1/users/me", "/api/v1/organizations/fixture-org/quality-summary",
            "/api/v1/orgs/fixture-org/quota"])
        self.http.reset_mock()
        for status, payload, code in (
                (200, {}, "invalid_response"),
                (200, {"userState": "unknown"}, "invalid_response"),
                (200, {"userState": "on_hold"}, "access_paused"),
                (200, {"userState": "inactive"}, "access_paused"),
                (503, {"detail": "private-token"}, "service"),
                (403, {"detail": "Forbidden"}, "forbidden")):
            with self.subTest(status=status, payload=payload):
                self.reads.clear()
                self.quality = minute_api.HttpResponse(status, json.dumps(payload), {})
                with self.assertRaises(AuthError) as caught:
                    self.session.check_recording_authorization("fixture-org")
                self.assertEqual(caught.exception.account_issue_code, code)
                self.assertNotIn("private-token", str(caught.exception))
                self.assertEqual(self.reads, [
                    "/api/v1/users/me", "/api/v1/organizations/fixture-org/quality-summary"])
                self.http.assert_not_called()
        self.quality = minute_api.HttpResponse(200, json.dumps({"userState": "active"}), {})
        self.session.check_recording_authorization("fixture-org")
        self.http.assert_not_called()  # Only quota, never quality, was cached.

    def test_explicit_geo_and_quota_denials_remain_fail_closed(self):
        for auth, can_upload, reason in (
                ("REQUIRES_LOCATION", True, None), ("BLOCKED", False, "geo_restricted"),
                ("APPROVED", False, "quota_exceeded"), ("APPROVED", True, "manual")):
            with self.subTest(auth=auth, can_upload=can_upload, reason=reason):
                self.session._quota_cache.clear()
                self.http.return_value = minute_api.HttpResponse(200, json.dumps({
                    "recordingAuthorization": auth, "canUpload": can_upload, "blockedReason": reason}), {})
                with self.assertRaises(AuthError) as caught:
                    self.session.check_recording_authorization("fixture-org")
                self.assertEqual(caught.exception.account_issue_code, "policy")

    def test_real_http_adapter_latches_exact_130_refusal_and_prevents_later_writes(self):
        # Bind the real adapter while keeping only the network/header boundaries inert.
        self.http.side_effect = lambda *args, **kwargs: Session.request_detailed(self.session, *args, **kwargs)
        denied = minute_api.HttpResponse(403, json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "1.30.0", "message": "private-token"}}), {})
        profile = Mock()
        profile.headers.return_value = {}
        with patch.object(Session, "id_token", new_callable=PropertyMock, return_value="fixture"), \
                patch.object(minute_api, "require_app_check_for_write", return_value={}), \
                patch.object(minute_api, "_align_physical_device_headers"), \
                patch.object(self.session, "_check_local_restrictions"), \
                patch.object(minute_api.device_profile, "get_profile", return_value=profile), \
                patch.object(minute_api, "_request_detailed", return_value=denied) as network:
            with self.assertRaises(AuthError) as caught:
                self.session.check_recording_authorization("fixture-org")
            self.assertEqual(caught.exception.account_issue_code, "version")
            self.assertEqual(caught.exception.http_status, 403)
            self.assertIn("1.30.0", str(caught.exception))
            self.assertNotIn("private-token", str(caught.exception))
            self.assertEqual(self.session.version_gate()["minVersion"], "1.30.0")
            with self.assertRaises(AuthError):
                self.session.check_recording_authorization("fixture-org")
            with self.assertRaises(AuthError):
                self.session._check_write_policy("POST", "/api/v1/uploads?org_key=fixture-org", {})
            network.assert_called_once()
            self.assertEqual(network.call_args.args[1], "GET")
        # Reading the quota still returns the backward-compatible unknown value.
        self.http.side_effect = None
        self.http.return_value = denied
        self.assertEqual(self.session.recording_geo("fixture-org"), {})

    def test_version_message_never_exposes_unvalidated_minimum(self):
        error = minute_api._auth_failure(403, json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "private-token"}}), "Quota")
        self.assertEqual(error.account_issue_code, "version")
        self.assertNotIn("private-token", str(error))


class VpnDetectionTests(unittest.TestCase):
    def test_detection_distinguishes_failure_from_no_active_adapter(self):
        for code, output, expected in ((0, "0", False), (0, "1", True),
                                      (1, "0", None), (0, "", None),
                                      (0, "invalid", None), (0, "-1", None)):
            with self.subTest(code=code, output=output), patch.object(
                vpn.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=output)
            ) as run:
                self.assertIs(vpn._detect(), expected)
                self.assertIn("$_.Status -eq 'Up'", run.call_args.args[0][-1])
        with patch.object(vpn.subprocess, "run", side_effect=OSError):
            self.assertIsNone(vpn._detect())

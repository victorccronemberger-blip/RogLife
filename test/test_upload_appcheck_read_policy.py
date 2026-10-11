"""Explicit App Check refusals must survive upload queries and evaluation."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moneymin import upload


class DetailedSession:
    def __init__(self, status=429, headers=None, *, conflict=False):
        self.status = status
        self.headers = {"x-blocked-reason": "app_check"} if headers is None else headers
        self.conflict = conflict
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append(("legacy", method, path))
        return self.status, "{}"

    def request_detailed(self, method, path, body=None):
        self.calls.append(("detailed", method, path))
        if self.conflict and path.endswith("/complete"):
            return SimpleNamespace(status=409, text="{}", headers={})
        return SimpleNamespace(status=self.status, text="{}", headers=self.headers)


class UploadAppCheckReadPolicyTests(unittest.TestCase):
    def assert_policy_error(self, error, status):
        self.assertEqual(error.status_code, status)
        self.assertIsInstance(error.blocked_reason, str)
        self.assertEqual(error.blocked_reason.casefold(), "app_check")
        self.assertFalse(error.retryable)
        self.assertIn("app_check", str(error))

    def test_query_preserves_detailed_app_check_refusal(self):
        for status in (403, 429):
            with self.subTest(status=status):
                session = DetailedSession(status)
                with self.assertRaises(upload.UploadError) as caught:
                    upload.get_upload(session, "fixture-upload")
                self.assert_policy_error(caught.exception, status)
                self.assertEqual(session.calls, [
                    ("detailed", "GET", "/api/v1/uploads/fixture-upload")])

    def test_evaluation_preserves_app_check_refusal_without_retry(self):
        for status in (403, 429, 503):
            with self.subTest(status=status):
                session = DetailedSession(status)
                with patch.object(upload.time, "sleep") as sleep:
                    with self.assertRaises(upload.UploadError) as caught:
                        upload.evaluate_upload(session, "fixture-upload")
                self.assert_policy_error(caught.exception, status)
                sleep.assert_not_called()
                self.assertEqual(session.calls, [
                    ("detailed", "POST", "/api/v1/uploads/fixture-upload/evaluate")])

    def test_complete_conflict_preserves_policy_refusal_and_receipt_uncertainty(self):
        session = DetailedSession(conflict=True)
        with patch.object(upload.time, "sleep") as sleep:
            with self.assertRaises(upload.UploadError) as caught:
                upload._with_retry(
                    lambda: upload.complete_upload(session, "fixture-upload", 1),
                    max_retries=3)
        self.assert_policy_error(caught.exception, 429)
        self.assertTrue(caught.exception.review_required)
        self.assertEqual(caught.exception.attempts, 1)
        sleep.assert_not_called()
        self.assertEqual(session.calls, [
            ("detailed", "PATCH", "/api/v1/uploads/fixture-upload/complete"),
            ("detailed", "GET", "/api/v1/uploads/fixture-upload")])

    def test_complete_conflict_ordinary_429_keeps_retry_limit(self):
        session = DetailedSession(headers={}, conflict=True)
        with patch.object(upload.time, "sleep") as sleep:
            with self.assertRaises(upload.UploadError) as caught:
                upload._with_retry(
                    lambda: upload.complete_upload(session, "fixture-upload", 1),
                    max_retries=3)
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.review_required)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertEqual(len(session.calls), 6)
        self.assertEqual(sleep.call_count, 2)

    def test_evaluation_transient_responses_keep_retry_limit(self):
        for status in (-1, 408, 500, 503, 504):
            with self.subTest(status=status):
                session = DetailedSession(status, headers={})
                with patch.object(upload.time, "sleep") as sleep:
                    with self.assertRaises(upload.UploadError) as caught:
                        upload.evaluate_upload(session, "fixture-upload")
                self.assertTrue(caught.exception.retryable)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.attempts, 3)
                self.assertEqual(len(session.calls), 3)
                self.assertEqual(sleep.call_count, 2)

    def test_evaluation_ordinary_429_keeps_existing_single_request(self):
        session = DetailedSession(headers={})
        with patch.object(upload.time, "sleep") as sleep:
            with self.assertRaises(upload.UploadError) as caught:
                upload.evaluate_upload(session, "fixture-upload")
        self.assertTrue(caught.exception.retryable)
        self.assertIsNone(caught.exception.blocked_reason)
        self.assertEqual(len(session.calls), 1)
        sleep.assert_not_called()

    def test_legacy_tuple_success_responses_remain_supported(self):
        evaluation = {"upload_id": "fixture-upload", "checks": [
            {"id": "fixture", "label": "fixture", "status": "pass", "detail": None}]}
        def request(method, path, body=None):
            return 200, json.dumps(evaluation if path.endswith("/evaluate")
                                   else {"id": "fixture-upload"})
        session = SimpleNamespace(request=request)
        self.assertEqual(upload.get_upload(session, "fixture-upload"), {"id": "fixture-upload"})
        self.assertEqual(upload.evaluate_upload(session, "fixture-upload"), evaluation)

    def test_query_and_evaluation_resolve_facade_adapters_at_call_time(self):
        for operation in (upload.get_upload, upload.evaluate_upload):
            with self.subTest(operation=operation.__name__):
                error = upload.UploadError("fixture policy refusal", status_code=429,
                                           blocked_reason="app_check", transient=False)
                with patch.object(upload, "_session_request", return_value=(
                        429, "private-response-canary", {"X-Blocked-Reason": "app_check"})) as request, \
                     patch.object(upload, "_http_upload_error", return_value=error) as classify:
                    with self.assertRaises(upload.UploadError) as caught:
                        operation(object(), "fixture-upload")
                self.assertIs(caught.exception, error)
                request.assert_called_once()
                classify.assert_called_once()
                if operation is upload.evaluate_upload:
                    self.assertNotIn("private-response-canary", str(classify.call_args))

    def test_new_read_policy_does_not_expose_arbitrary_blocked_headers(self):
        for operation in (upload.get_upload, upload.evaluate_upload):
            with self.subTest(operation=operation.__name__):
                session = DetailedSession(headers={
                    "X-Blocked-Reason": "https://fixture.invalid/blob?sig=private-header-canary"})
                with self.assertRaises(upload.UploadError) as caught:
                    operation(session, "fixture-upload")
                self.assertIsNone(caught.exception.blocked_reason)
                self.assertNotIn("private-header-canary", str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))

    def test_undeclared_detailed_method_preserves_legacy_mock_adapter(self):
        session = Mock()
        session.request.return_value = (429, "private-response-canary")
        for operation in (upload.get_upload, upload.evaluate_upload):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(upload.UploadError) as caught:
                    operation(session, "fixture-upload")
                self.assertEqual(caught.exception.status_code, 429)
                session.request_detailed.assert_not_called()

    def test_ambiguous_detailed_reply_never_dispatches_a_second_request(self):
        for response in ({"private": "private-response-canary"},
                         SimpleNamespace(status="private-response-canary", text="{}", headers={})):
            with self.subTest(response_type=type(response).__name__):
                class AmbiguousSession(DetailedSession):
                    def request_detailed(self, method, path, body=None):
                        self.calls.append(("detailed", method, path))
                        return response
                session = AmbiguousSession()
                with self.assertRaises(upload.UploadError) as caught:
                    upload.get_upload(session, "fixture-upload")
                self.assertTrue(caught.exception.review_required)
                self.assertNotIn("private-response-canary", str(caught.exception))
                self.assertEqual(len(session.calls), 1)

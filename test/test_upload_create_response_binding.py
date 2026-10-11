"""A contradictory CREATE receipt cannot authorize transfer or another CREATE."""
import unittest

from moneymin import upload
import test_upload_response_contract as fixtures


class UploadCreateResponseBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.UploadResponseContractTests(
            "test_new_upload_defaults_register_before_sas_and_transport")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def send(self, session, *, task_id="fixture-task", register_first=True):
        # The SAS fixture stops at the next boundary, without any transport.
        return upload._upload_single_chunk(
            session=session, video_path=self.fixture.video, org_key="fixture-org",
            session_id="fixture-session", chunk_index=0, task_id=task_id,
            content_type="video/mp4", timeout_blob=1,
            recorded_at="2026-10-03T00:00:00.000Z", device_meta={},
            platform_meta={}, video_meta={}, network_meta={}, sidecar=False,
            max_retries=3, retry_backoff=0, fail_on_error=False,
            register_first=register_first, checkpoint=self.fixture.checkpoint,
        )

    def session(self, **echoes):
        return fixtures.FakeSession(create_body={
            "id": "fixture-upload", "status": "initiated", "meta": {}, **echoes,
        }, raw_sas="{}")

    def assert_review(self, session, result):
        self.assertEqual(result.state, upload.STATE_QUARANTINE)
        self.assertEqual(result.upload_id, "fixture-upload")
        self.assertEqual(session.events, ["create"])
        self.assertEqual(result.attempts, 1)
        self.fixture.video_put.assert_not_called()
        self.fixture.zip_put.assert_not_called()
        row = self.fixture.checkpoint.call_args.kwargs
        self.assertEqual(row["phase"], "registration_review")
        self.assertEqual(row["upload_id"], "fixture-upload")
        self.assertNotIn("private-response-canary", result.error)

    def test_each_contradictory_binding_preserves_receipt_and_stops(self):
        for field in ("session_id", "log_id", "task_id"):
            with self.subTest(field=field):
                session = self.session(**{field: "private-response-canary"})
                self.assert_review(session, self.send(session))

    def test_invalid_binding_types_do_not_get_coerced(self):
        for field in ("session_id", "log_id", "task_id"):
            for value in (None, True, 1, [], {}):
                with self.subTest(field=field, value=value):
                    session = self.session(**{field: value})
                    self.assert_review(session, self.send(session))

    def test_minimal_native_receipt_remains_compatible(self):
        session = self.session()
        self.send(session)
        self.assertEqual(session.events, ["create", "sas"])

    def test_matching_present_bindings_remain_compatible(self):
        for echoes in ({"session_id": "fixture-session"},
                       {"log_id": "fixture-session_0"},
                       {"task_id": "fixture-task"},
                       {"session_id": "fixture-session", "log_id": "fixture-session_0",
                        "task_id": "fixture-task"}):
            with self.subTest(echoes=echoes):
                session = self.session(**echoes)
                self.send(session)
                self.assertEqual(session.events, ["create", "sas"])

    def test_optional_task_null_matches_an_omitted_request_task(self):
        session = self.session(task_id=None)
        self.send(session, task_id=None)
        self.assertNotIn("task_id", session.calls[0][2])
        self.assertEqual(session.events, ["create", "sas"])

    def test_omitted_task_does_not_constrain_server_task_resolution(self):
        session = self.session(task_id="fixture-server-task")
        self.send(session, task_id=None)
        self.assertNotIn("task_id", session.calls[0][2])
        self.assertEqual(session.events, ["create", "sas"])


if __name__ == "__main__":
    unittest.main()

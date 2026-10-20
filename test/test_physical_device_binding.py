"""Physical phone wire binding: provider video stays; device fields follow phone."""
from __future__ import annotations

import io
import json
import zipfile
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from moneymin import campaign, sidecar, upload
from moneymin.campaign_types import AccountSpec
from moneymin.delivery import admission as delivery_admission
from moneymin.delivery import media as delivery_media
from moneymin.mobile import physical_binding as pb
from moneymin.mobile.physical_binding import PhysicalDeviceRequiredError
from moneymin.upload_types import UploadError


def _fixture_identity(*, model: str, device_id: str, app_version: str, sdk: int,
                      uptime_ns: int = 10_000_000_000, serial: str = "SERIAL",
                      collected_at_unix: int | None = None,
                      network_type: str | None = None,
                      location: dict | None = None) -> dict:
    snap = {
        "serial": serial,
        "device_id": device_id,
        "model": model,
        "system_name": "Android",
        "system_version": "16",
        "sdk_int": sdk,
        "uptime_ns": uptime_ns,
        "app_version": app_version,
    }
    if collected_at_unix is not None:
        snap["collected_at_unix"] = collected_at_unix
    if network_type is not None:
        snap["network_type"] = network_type
    if location is not None:
        snap["location"] = location
    return snap


class PhysicalWireFieldsTests(TestCase):
    def test_two_identities_bind_distinct_device_fields(self):
        a = pb.physical_wire_fields(_fixture_identity(
            model="SM-S901E", device_id="android.ssaid:aaa",
            app_version="1.29.0", sdk=36, serial="PHONE-A"))
        b = pb.physical_wire_fields(_fixture_identity(
            model="SM-S911B", device_id="android.ssaid:bbb",
            app_version="1.30.0", sdk=34, serial="PHONE-B",
            uptime_ns=20_000_000_000))
        self.assertEqual(a["upload_device"]["model"], "SM-S901E")
        self.assertEqual(b["upload_device"]["model"], "SM-S911B")
        self.assertEqual(a["app_version"], "1.29.0")
        self.assertEqual(b["app_version"], "1.30.0")
        self.assertEqual(a["device_id"], "android.ssaid:aaa")
        self.assertEqual(b["device_id"], "android.ssaid:bbb")
        self.assertEqual(a["sidecar_platform"]["version"], 36)
        self.assertEqual(b["sidecar_platform"]["version"], 34)
        self.assertNotEqual(a["uptime_ns"], b["uptime_ns"])
        self.assertEqual(a["upload_platform"], {"os": "android"})
        self.assertEqual(a["sidecar_device"]["model"], "SM-S901E")
        self.assertEqual(a["sidecar_device"]["systemName"], "Android")
        self.assertIsInstance(a["calib"], dict)
        self.assertEqual(a["calib"]["distortion_model"], "brown_conrady")
        self.assertIsInstance(a["frames_gop"], int)
        self.assertGreater(a["frames_gop"], 0)
        self.assertGreater(a["video_bitrate_mbps"], 0)
        # SM-S901E uses the sole known Minute-native capture (camera2).
        self.assertEqual(a["calib"]["cameraName"], "camera2")
        self.assertEqual(a["calib"]["logicalCameraId"], "2")
        self.assertEqual(a["calib"]["readoutS"], 0.012730151010582011)
        self.assertEqual(a["calib"]["video_frame"]["fx"], 566.8083665086525)
        # Distinct phones → distinct sensor signatures (model and/or SSAID seed).
        self.assertNotEqual(a["calib"]["fx"], b["calib"]["fx"])
        self.assertNotEqual(a["calib"].get("cameraName"), b["calib"].get("cameraName"))

    def test_missing_identity_fields_refuse_with_device_cause(self):
        with self.assertRaises(PhysicalDeviceRequiredError) as ctx:
            pb.physical_wire_fields({"model": "SM-S901E"})
        self.assertEqual(ctx.exception.cause, "physical_device_id_missing")

    def test_require_binding_uses_injected_identity_without_adb(self):
        snap = _fixture_identity(
            model="Pixel 8", device_id="android.ssaid:pixel",
            app_version="1.28.0", sdk=35)
        bound = pb.require_campaign_physical_binding(identity=snap, wall_ms=1_700_000_000_000)
        self.assertEqual(bound["upload_device"]["model"], "Pixel 8")
        self.assertEqual(bound["app_version"], "1.28.0")
        self.assertIn("calib", bound)
        self.assertIn("frames_gop", bound)

    def test_same_phone_calib_gop_stable_across_calls(self):
        snap = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:stablephone",
            app_version="1.29.0", sdk=36)
        first = pb.physical_wire_fields(snap)
        second = pb.physical_wire_fields(snap)
        self.assertEqual(first["calib"], second["calib"])
        self.assertEqual(first["frames_gop"], second["frames_gop"])
        self.assertEqual(first["video_bitrate_mbps"], second["video_bitrate_mbps"])


class DeliveryGateTests(TestCase):
    def test_default_device_meta_refuses_synthetic_in_device_mode(self):
        services = MagicMock()
        services.config.NATIVE_DEVICE_MODEL = "SYNTHETIC-DEFAULT"
        services.UploadError = UploadError
        profile = MagicMock()
        profile.upload_device_meta.return_value = {"model": "SYNTHETIC-PROFILE"}
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          side_effect=PhysicalDeviceRequiredError(
                              "Nenhum celular", cause="physical_identity_unavailable")):
            with self.assertRaises(UploadError) as ctx:
                delivery_media.default_device_meta(services, profile)
        self.assertIn("celular", str(ctx.exception).casefold())
        profile.upload_device_meta.assert_not_called()

    def test_default_device_meta_binds_physical_model_dynamically(self):
        services = MagicMock()
        services.UploadError = UploadError
        profile = MagicMock()
        profile.upload_device_meta.return_value = {"model": "SYNTHETIC-PROFILE"}
        snap = _fixture_identity(
            model="SM-A546E", device_id="android.ssaid:dyn",
            app_version="1.29.0", sdk=33)
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(snap)):
            meta = delivery_media.default_device_meta(services, profile)
        self.assertEqual(meta, {"model": "SM-A546E"})
        profile.upload_device_meta.assert_not_called()

    def test_admission_blocks_when_physical_identity_missing(self):
        activation = {"explicitly_disabled": False, "serial": "PHONE-X"}
        account = MagicMock()
        account.email = "fixture@example.invalid"
        account.org_key = "org"
        with patch("moneymin.roglife_appcheck.provider.ensure_device_defaults_for_writes",
                   return_value=activation), \
             patch("moneymin.roglife_appcheck.provider.device_mode_enabled",
                   return_value=True), \
             patch("moneymin.mobile.physical_binding.require_campaign_physical_binding",
                   side_effect=PhysicalDeviceRequiredError(
                       "missing", cause="physical_identity_unavailable")), \
             patch("moneymin.appcheck.ensure_app_check_fresh") as ensure_fresh:
            message = delivery_admission.campaign_create_blocker([account])
        self.assertIsInstance(message, str)
        self.assertIn("Identidade física", message)
        self.assertIn("physical_identity_unavailable", message)
        self.assertIn("PHONE-X", message)
        ensure_fresh.assert_not_called()


class AdmissionGateDirectTests(TestCase):
    def test_admission_gate_message_via_shipped_helper(self):
        """Drive require_campaign_physical_binding refusal the admission path uses."""
        with patch("moneymin.roglife_appcheck.provider.device_mode_enabled",
                   return_value=True), \
             patch("moneymin.roglife_appcheck.provider.physical_identity",
                   return_value=None):
            with self.assertRaises(PhysicalDeviceRequiredError) as ctx:
                pb.require_campaign_physical_binding()
        self.assertEqual(ctx.exception.cause, "physical_identity_unavailable")


class SidecarAppVersionPhysicalTests(TestCase):
    def test_build_sidecar_metadata_app_version_from_physical_phone(self):
        """Shipped _build_sidecar must put phone appVersion into metadata.json."""
        phone_version = "1.29.7-physical"
        profile_version_trap = "9.9.9-PROFILE-MUST-NOT-WIN"
        snap = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:sidecar",
            app_version=phone_version, sdk=36, uptime_ns=88_000_000_000)
        profile_trap_calib = {
            "distortion_model": "brown_conrady",
            "fx": 1.0, "fy": 1.0, "cx": 1.0, "cy": 1.0,
            "k1": 0.0, "k2": 0.0, "k3": 0.0, "p1": 0.0, "p2": 0.0,
            "readoutS": 0.0999, "logicalCameraId": "99",
            "referenceWidth": 4032, "referenceHeight": 3024,
        }
        profile = SimpleNamespace(
            frames_gop=99,
            calib=profile_trap_calib,
            uptime_ns_at=lambda _wall: 7,
            sidecar_device_meta=lambda: {
                "model": "SYNTHETIC-PROFILE", "systemName": "Android",
                "systemVersion": "99",
            },
            sidecar_platform_meta=lambda: {"os": "android", "version": 99},
        )
        item = {
            "source": "ego4d",
            "duration_ms": 2_000,
            "n_samples": 1,
            "imu_diagnostics": None,
        }
        bound = pb.physical_wire_fields(snap)
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=bound), \
             patch("moneymin.config.APP_VERSION", profile_version_trap):
            blob = campaign._build_sidecar(
                item, "phys-session", "phys-session_0",
                "2026-10-04T12:00:00.000Z", profile,
                video_probe={"duration_ms": 2_000, "fps": 30.0,
                             "width": 1440, "height": 1080},
                imu_csv="t,ax,ay,az,wx,wy,wz\n0,1,2,3,4,5,6\n",
                frames_csv="i,ptsNs,dtNs,tNs,key\n0,0,0,88000000000,1\n",
                chunk_index=0, duration_ms=2_000)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            meta = json.loads(archive.read("phys-session_0.metadata.json"))
        self.assertEqual(meta["appVersion"], phone_version)
        self.assertNotEqual(meta["appVersion"], profile_version_trap)
        self.assertEqual(meta["device"]["model"], "SM-S901E")
        self.assertEqual(meta["platform"]["version"], 36)
        camera = meta["cameras"][0]
        self.assertEqual(camera["name"], "camera2")
        self.assertEqual(
            camera["rolling_shutter_readout_s"],
            0.012730151010582011)
        self.assertEqual(camera["intrinsics"]["fx"], 566.8083665086525)
        self.assertEqual(camera["intrinsics"]["fy"], 566.9303374764808)
        self.assertEqual(camera["intrinsics"]["cx"], 705.8244546207659)
        self.assertEqual(camera["intrinsics"]["cy"], 536.2907337767916)
        self.assertNotEqual(camera["name"], "camera_logical_99")
        self.assertNotEqual(
            camera["rolling_shutter_readout_s"],
            float(profile_trap_calib["readoutS"]))
        self.assertEqual(bound["calib"]["cameraName"], "camera2")


class MultiChunkPhysicalOffsetTests(TestCase):
    def test_multi_chunk_frames_offset_ns_from_physical_not_profile(self):
        """Under device mode, per-chunk frames offset must follow the phone uptime."""
        physical_uptime = 55_000_000_000
        profile_uptime = 7  # trap: must not appear as offset_ns
        snap = _fixture_identity(
            model="SM-A546E", device_id="android.ssaid:chunk",
            app_version="1.29.0", sdk=33, uptime_ns=physical_uptime)
        # No collected_at_unix → wall_ms must not rewrite uptime_ns.
        self.assertNotIn("collected_at_unix", snap)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "provider_clip_native.mp4"
            media.write_bytes(b"provider-video-bytes-multi-chunk")
            account = AccountSpec("chunk@example.invalid", "chunk-org")
            profile = SimpleNamespace(
                frames_gop=99,  # trap: must not appear as frames gop
                calib={},
                uptime_ns_at=lambda _wall: profile_uptime,
                sidecar_device_meta=lambda: {
                    "model": "SYNTHETIC", "systemName": "Android",
                    "systemVersion": "1",
                },
                sidecar_platform_meta=lambda: {"os": "android", "version": 1},
            )
            session = SimpleNamespace(
                _live=True,
                _moneymin_pending_pumped=True,
                recording_policy=SimpleNamespace(
                    limits=lambda: dict(min_duration_ms=60_000, max_duration_ms=60_000)),
                email=account.email,
                warmup=lambda: None,
            )
            item = {
                "source": "fixture-local",
                "duration_ms": 120_000,
                "video_path": str(media),
                "clip_uid": "fixture-clip",
                "registry_key": "fixture-key",
                "imu_csv": "t,ax,ay,az,wx,wy,wz\n0,1,2,3,4,5,6\n",
                "n_samples": 1,
                "probe": {"duration_ms": 120_000, "fps": 30.0,
                          "width": 1440, "height": 1080},
            }
            offsets: list[int] = []
            gops: list[int] = []
            expected_gop = pb.physical_wire_fields(snap)["frames_gop"]

            def capture_frames(path, **kwargs):
                offsets.append(int(kwargs["offset_ns"]))
                gops.append(int(kwargs["gop"]))
                return sidecar.build_frames_csv(
                    kwargs["duration_ms"], kwargs.get("fps") or 30.0,
                    gop=kwargs.get("gop") or 30, offset_ns=kwargs["offset_ns"])

            def cut(_source, destination, *_args, **_kwargs):
                destination.write_bytes(b"declared-inert-chunk")
                return destination

            from moneymin import appcheck
            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    appcheck, "ensure_app_check_fresh",
                    return_value={"token_available": True}))
                stack.enter_context(patch.object(
                    campaign.org_policy, "account_kind", return_value="other"))
                stack.enter_context(patch.object(
                    campaign.device_profile, "get_profile", return_value=profile))
                stack.enter_context(patch.object(
                    campaign, "_new_identity",
                    return_value=("chunk-session", "chunk-session_0",
                                  "2026-10-04T12:00:00.000Z")))
                stack.enter_context(patch.object(
                    campaign, "probe_video",
                    return_value={"duration_ms": 60_000, "fps": 30.0,
                                  "has_video": True, "width": 1440, "height": 1080}))
                stack.enter_context(patch.object(
                    campaign, "_chunk_video_path",
                    side_effect=lambda _base, index, *_a, **_k: root / f"chunk-{index}.mp4"))
                stack.enter_context(patch.object(
                    campaign, "_cut_video_chunk", side_effect=cut))
                stack.enter_context(patch.object(
                    campaign, "build_frames_csv_from_video", side_effect=capture_frames))
                stack.enter_context(patch.object(
                    pb, "device_mode_requires_physical", return_value=True))
                stack.enter_context(patch.object(
                    pb, "require_campaign_physical_binding",
                    side_effect=lambda **kw: pb.physical_wire_fields(
                        snap, wall_ms=kw.get("wall_ms"))))
                stack.enter_context(patch.object(upload, "save_sidecar"))
                stack.enter_context(patch.object(
                    campaign.sent_registry, "mark_sent", create=True))
                send = stack.enter_context(patch.object(campaign, "upload_session"))
                send.return_value = SimpleNamespace(
                    session_id="chunk-session", finalized=True, finalize_status=204,
                    chunks=[SimpleNamespace(
                        state="done", error=None, upload_id=f"up-{i}",
                        evaluate_result={}) for i in range(2)])
                result = campaign.upload_to_account(
                    item, account, "fixture-task", 30, True, True,
                    session=session, recover_pending=False)

            self.assertTrue(result.get("ok"), result.get("error"))
            # Full item frames + two per-chunk frames rebuilds.
            self.assertGreaterEqual(len(offsets), 3)
            for offset in offsets:
                self.assertEqual(offset, physical_uptime)
                self.assertNotEqual(offset, profile_uptime)
            self.assertEqual(gops, [expected_gop] * len(gops))
            self.assertNotIn(99, gops)
            self.assertEqual(item["video_path"], str(media))
            self.assertEqual(Path(item["video_path"]).read_bytes(),
                             b"provider-video-bytes-multi-chunk")


class ProviderVideoPreservedOnDeliveryPathTests(TestCase):
    def test_upload_to_account_keeps_provider_video_under_physical_binding(self):
        """Real account_delivery path: ego4d video stays; phone owns device fields."""
        from moneymin import appcheck, content_provenance

        phone_model = "SM-S911B"
        phone_version = "1.30.1-phone"
        physical_uptime = 66_000_000_000
        snap = _fixture_identity(
            model=phone_model, device_id="android.ssaid:provider-path",
            app_version=phone_version, sdk=34, uptime_ns=physical_uptime)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "ego4d_clip_native.mp4"
            provider_bytes = b"provider-ego4d-video-bytes-not-from-phone"
            media.write_bytes(provider_bytes)
            sensor = root / "canonical_imu.csv"
            sensor.write_text(
                "canonical_timestamp_ms,gyro_x,gyro_y,gyro_z,accl_x,accl_y,accl_z\n"
                + "".join(f"{t},4,5,6,1,2,3\n" for t in range(0, 60_001, 10)),
                encoding="utf-8")
            diagnostics: dict = {}
            imu_csv = campaign.ego4d.build_imu_csv(
                sensor, (0, 60), duration_ms=60_000, stats=diagnostics)
            item = content_provenance.prepare_content_provenance(
                media, sensor, media, imu_csv,
                "i,ptsNs,dtNs,tNs,key\n0,0,0,0,1\n",
                clip_uid="ego4d:fixture-clip",
                parent_video_uid="ego4d:fixture-parent",
                media_uid="ego4d:fixture-media",
                window_s=(0, 60), media_offset_s=0,
                normalization_start_s=0, selection_evidence=None)
            item.update(
                source="ego4d",
                imu_real=True,
                clip_uid="ego4d:fixture-clip",
                video_path=str(media),
                duration_ms=60_000,
                n_samples=diagnostics["sampleCount"],
                imu_diagnostics=diagnostics,
                probe={"duration_ms": 60_000, "fps": 30.0,
                       "width": 1440, "height": 1080},
                _content_candidate={"clip_uid": "ego4d:fixture-clip"},
                registry_key="ego4d-fixture-key",
            )
            before_path = item["video_path"]
            before_source = item["source"]
            before_bytes = Path(before_path).read_bytes()

            account = AccountSpec("provider@example.invalid", "provider-org")
            profile = SimpleNamespace(
                frames_gop=30,
                calib={},
                uptime_ns_at=lambda _wall: 7,
                sidecar_device_meta=lambda: {
                    "model": "SYNTHETIC-PROFILE", "systemName": "Android",
                    "systemVersion": "99",
                },
                sidecar_platform_meta=lambda: {"os": "android", "version": 99},
            )
            session = SimpleNamespace(
                _live=True,
                _moneymin_pending_pumped=True,
                recording_policy=None,
                email=account.email,
                warmup=lambda: None,
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    appcheck, "ensure_app_check_fresh",
                    return_value={"token_available": True}))
                stack.enter_context(patch.object(
                    campaign.ego4d, "revalidate_selection_evidence",
                    return_value={"schema": 1, "physical_provenance_verified": False},
                    create=True))
                stack.enter_context(patch.object(
                    campaign.org_policy, "account_kind", return_value="other"))
                stack.enter_context(patch.object(
                    campaign.device_profile, "get_profile", return_value=profile))
                stack.enter_context(patch.object(
                    campaign, "_new_identity",
                    return_value=("prov-session", "prov-session_0",
                                  "2026-10-04T12:00:00.000Z")))
                stack.enter_context(patch.object(
                    campaign, "probe_video",
                    return_value={"duration_ms": 60_000, "fps": 30.0,
                                  "has_video": True, "width": 1440, "height": 1080}))
                stack.enter_context(patch.object(
                    campaign, "build_frames_csv_from_video",
                    side_effect=lambda _path, **kw: sidecar.build_frames_csv(
                        kw["duration_ms"], kw.get("fps") or 30.0,
                        gop=kw.get("gop") or 30, offset_ns=kw["offset_ns"])))
                stack.enter_context(patch.object(
                    pb, "device_mode_requires_physical", return_value=True))
                stack.enter_context(patch.object(
                    pb, "require_campaign_physical_binding",
                    side_effect=lambda **kw: pb.physical_wire_fields(
                        snap, wall_ms=kw.get("wall_ms"))))
                stack.enter_context(patch.object(upload, "save_sidecar"))
                stack.enter_context(patch.object(
                    campaign.sent_registry, "mark_sent", create=True))
                send = stack.enter_context(patch.object(campaign, "upload_session"))
                send.return_value = SimpleNamespace(
                    session_id="prov-session", finalized=True, finalize_status=204,
                    chunks=[SimpleNamespace(
                        state="done", error=None, upload_id="up-1",
                        evaluate_result={})])
                result = campaign.upload_to_account(
                    item, account, "ego4d-task", 30, True, True,
                    session=session, recover_pending=False)

            self.assertTrue(result.get("ok"), result.get("error"))
            self.assertEqual(item["source"], before_source)
            self.assertEqual(item["video_path"], before_path)
            self.assertEqual(Path(item["video_path"]).read_bytes(), before_bytes)
            self.assertEqual(before_bytes, provider_bytes)
            send.assert_called_once()
            # upload_session first positional after sess is the video path(s).
            delivered = send.call_args.args[1]
            delivered_path = delivered if isinstance(delivered, (str, Path)) else delivered[0]
            self.assertEqual(Path(delivered_path).resolve(), media.resolve())
            self.assertEqual(Path(delivered_path).read_bytes(), provider_bytes)
            sidecar_blob = send.call_args.kwargs.get("sidecar_data")
            if isinstance(sidecar_blob, list):
                sidecar_blob = sidecar_blob[0]
            with zipfile.ZipFile(io.BytesIO(sidecar_blob)) as archive:
                meta = json.loads(archive.read("prov-session_0.metadata.json"))
            bound = pb.physical_wire_fields(snap)
            self.assertEqual(meta["appVersion"], phone_version)
            self.assertEqual(meta["device"]["model"], phone_model)
            self.assertEqual(meta["platform"]["version"], 34)
            self.assertEqual(
                int(meta["timebase"]["firstFrameSensorTimestampNs"]),
                physical_uptime)
            camera = meta["cameras"][0]
            # SM-S911B has no ported native capture → catalog camera_logical_*.
            expected_name = bound["calib"].get("cameraName") or (
                f"camera_logical_{bound['calib']['logicalCameraId']}")
            self.assertEqual(camera["name"], expected_name)
            self.assertEqual(
                camera["rolling_shutter_readout_s"],
                float(bound["calib"]["readoutS"]))
            self.assertNotEqual(camera["name"], "camera_logical_99")


class PhysicalResidualSurfacesTests(TestCase):
    def test_two_identities_bind_distinct_opened_and_network(self):
        a = pb.physical_wire_fields(_fixture_identity(
            model="SM-S901E", device_id="android.ssaid:aaa",
            app_version="1.29.0", sdk=36, network_type="wifi",
            location={"latitude": -23.55052, "longitude": -46.633308,
                       "accuracy": 12.0, "is_mock": False}))
        b = pb.physical_wire_fields(_fixture_identity(
            model="SM-S911B", device_id="android.ssaid:bbb",
            app_version="1.30.0", sdk=34, uptime_ns=20_000_000_000,
            network_type="cellular",
            location={"latitude": -22.906847, "longitude": -43.172897,
                       "accuracy": 8.0, "is_mock": False}))
        self.assertEqual(a["opened"]["device_model"], "SM-S901E")
        self.assertEqual(b["opened"]["device_model"], "SM-S911B")
        self.assertEqual(a["opened"]["os_version"], "android 36")
        self.assertEqual(b["opened"]["os_version"], "android 34")
        self.assertEqual(a["opened"]["app_version"], "1.29.0")
        self.assertEqual(b["opened"]["app_version"], "1.30.0")
        self.assertEqual(a["network_type"], "wifi")
        self.assertEqual(b["network_type"], "cellular")
        self.assertEqual(a["network_meta"], {"type": "wifi", "carrier": None})
        self.assertEqual(b["network_meta"]["type"], "cellular")
        self.assertEqual(a["user_agent"], b["user_agent"])
        self.assertEqual(a["user_agent"], "okhttp/4.12.0")
        self.assertEqual(a["location_header"], "-23.550520,-46.633308,12,false")
        self.assertEqual(b["location_header"], "-22.906847,-43.172897,8,false")
        self.assertNotEqual(a["location_header"], b["location_header"])

    def test_missing_network_refuses_require_physical_network(self):
        bound = pb.physical_wire_fields(_fixture_identity(
            model="SM-S901E", device_id="android.ssaid:nonet",
            app_version="1.29.0", sdk=36))
        self.assertIsNone(bound["network_type"])
        with self.assertRaises(PhysicalDeviceRequiredError) as ctx:
            pb.require_physical_network(bound)
        self.assertEqual(ctx.exception.cause, "physical_network_unavailable")

    def test_default_network_meta_uses_physical_and_refuses_without_sample(self):
        services = MagicMock()
        services.UploadError = UploadError
        snap = _fixture_identity(
            model="SM-A546E", device_id="android.ssaid:net",
            app_version="1.29.0", sdk=33, network_type="cellular")
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(snap)):
            meta = delivery_media.default_network_meta(services)
        self.assertEqual(meta, {"type": "cellular", "carrier": None})

        bare = _fixture_identity(
            model="SM-A546E", device_id="android.ssaid:net2",
            app_version="1.29.0", sdk=33)
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(bare)):
            with self.assertRaises(UploadError) as ctx:
                delivery_media.default_network_meta(services)
        self.assertIn("network_type", str(ctx.exception).casefold())

    def test_align_headers_follow_physical_and_strip_synthetic_location(self):
        from moneymin import minute_api

        snap = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:hdr",
            app_version="1.29.7", sdk=36, network_type="wifi",
            location={"latitude": -23.5, "longitude": -46.6,
                       "accuracy": 10.0, "is_mock": False})
        headers = {
            "X-Device-Id": "android.ssaid:SYNTHETIC",
            "X-App-Version": "0.0.0-profile",
            "User-Agent": "should-be-replaced",
            "X-Device-Location": "0.000000,0.000000,1,false",
        }
        with patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(snap)), \
             patch("moneymin.roglife_appcheck.device_mode_enabled",
                   return_value=True):
            minute_api._align_physical_device_headers(headers)
        self.assertEqual(headers["X-Device-Id"], "android.ssaid:hdr")
        self.assertEqual(headers["X-App-Version"], "1.29.7")
        self.assertEqual(headers["User-Agent"], "okhttp/4.12.0")
        self.assertEqual(headers["X-Device-Location"], "-23.500000,-46.600000,10,false")

        no_gps = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:hdr2",
            app_version="1.29.7", sdk=36, network_type="wifi")
        headers2 = {
            "X-Device-Id": "android.ssaid:SYNTHETIC",
            "X-App-Version": "0.0.0",
            "User-Agent": "x",
            "X-Device-Location": "1.000000,2.000000,3,false",
        }
        with patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(no_gps)), \
             patch("moneymin.roglife_appcheck.device_mode_enabled",
                   return_value=True):
            minute_api._align_physical_device_headers(headers2)
        self.assertNotIn("X-Device-Location", headers2)

    def test_align_headers_refuse_without_physical_identity(self):
        from moneymin import minute_api
        from moneymin.minute_api import AuthError

        with patch("moneymin.roglife_appcheck.device_mode_enabled",
                   return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          side_effect=PhysicalDeviceRequiredError(
                              "missing", cause="physical_identity_unavailable")):
            with self.assertRaises(AuthError) as ctx:
                minute_api._align_physical_device_headers({"X-Device-Id": "x"})
        self.assertEqual(ctx.exception.account_issue_code, "device")

    def test_app_opened_payload_from_physical_phone(self):
        from moneymin.minute_api import Session

        snap = _fixture_identity(
            model="SM-S911B", device_id="android.ssaid:opened",
            app_version="1.30.1", sdk=34, network_type="wifi")
        session = object.__new__(Session)
        session.email = "opened@example.invalid"
        captured: dict = {}

        def fake_post(_self, path, body):
            captured["path"] = path
            captured["body"] = body
            return 204, ""

        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(snap)), \
             patch.object(Session, "post", fake_post):
            status, _text = session.app_opened("SESSION_RESUMED")
        self.assertEqual(status, 204)
        self.assertEqual(captured["path"], "/api/v1/app/opened")
        self.assertEqual(captured["body"]["device_model"], "SM-S911B")
        self.assertEqual(captured["body"]["app_version"], "1.30.1")
        self.assertEqual(captured["body"]["os_version"], "android 34")
        self.assertEqual(captured["body"]["auth_method"], "SESSION_RESUMED")

    def test_residual_binding_leaves_provider_video_untouched(self):
        item = {
            "source": "ego4d",
            "video_path": r"C:\provider\clip_native.mp4",
            "duration_ms": 60_000,
        }
        before = dict(item)
        snap = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:vid",
            app_version="1.29.0", sdk=36, network_type="wifi")
        bound = pb.physical_wire_fields(snap)
        self.assertEqual(bound["network_type"], "wifi")
        self.assertEqual(item, before)
        self.assertEqual(item["source"], "ego4d")
        self.assertEqual(item["video_path"], before["video_path"])

    def test_device_info_parsers_read_network_and_location_from_dumpsys(self):
        from moneymin.roglife_appcheck import device_info as di

        class _Proc:
            def __init__(self, stdout: str):
                self.stdout = stdout

        wifi_dump = "Active network NetworkAgentInfo{ transport=WIFI }\n"
        cell_dump = "NetworkInfo: type: MOBILE, state: CONNECTED/CONNECTED\n"
        android16_wifi = (
            "Active default network: 105\n"
            "  NetworkAgentInfo{network{105}  handle{1}  ni{WIFI CONNECTED extra: }\n")
        android16_cell = (
            "Active default network: 106\n"
            "  NetworkAgentInfo{network{106}  handle{2}  ni{CELLULAR CONNECTED extra: }\n")
        android16_no_default = (
            "Active default network: None\n"
            "  NetworkAgentInfo{network{105}  handle{1}  ni{WIFI CONNECTED extra: }\n")
        loc_dump = (
            "last location=Location[gps  -23.550520,-46.633308 "
            "acc=12.0 mock=false]\n")

        with patch.object(di, "adb_shell", return_value=_Proc(wifi_dump)):
            self.assertEqual(di._network_type("SERIAL"), "wifi")
        with patch.object(di, "adb_shell", return_value=_Proc(cell_dump)):
            self.assertEqual(di._network_type("SERIAL"), "cellular")
        with patch.object(di, "adb_shell", return_value=_Proc(android16_wifi)):
            self.assertEqual(di._network_type("SERIAL"), "wifi")
        with patch.object(di, "adb_shell", return_value=_Proc(android16_cell)):
            self.assertEqual(di._network_type("SERIAL"), "cellular")
        with patch.object(di, "adb_shell", return_value=_Proc(android16_no_default)):
            self.assertIsNone(di._network_type("SERIAL"))
        with patch.object(di, "adb_shell", return_value=_Proc("")):
            self.assertIsNone(di._network_type("SERIAL"))
        with patch.object(di, "adb_shell", return_value=_Proc(loc_dump)):
            sample = di._location_sample("SERIAL")
        self.assertEqual(sample["latitude"], -23.550520)
        self.assertEqual(sample["longitude"], -46.633308)
        self.assertEqual(sample["accuracy"], 12.0)
        self.assertIs(sample["is_mock"], False)

    def test_complete_upload_uses_physical_network_not_caller_wifi_default(self):
        """Shipped complete_upload (recovery/chunk path) must resolve phone network."""
        from moneymin import upload as upload_mod

        snap = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:complete",
            app_version="1.29.0", sdk=36, network_type="cellular")
        captured: dict = {}

        def fake_session_request(_session, method, path, body=None, **_kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return 204, "", {}

        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(snap)), \
             patch.object(upload_mod, "_session_request", fake_session_request):
            # Caller omits/forges wifi like recovery.py does today.
            upload_mod.complete_upload(
                object(), "upload-residual", 4096,
                suppress_per_chunk_catbear=True,
                session_complete=False,
                network_type="wifi",
            )
        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(
            captured["path"], "/api/v1/uploads/upload-residual/complete")
        self.assertEqual(captured["body"]["network_type"], "cellular")
        self.assertNotEqual(captured["body"]["network_type"], "wifi")

    def test_complete_upload_refuses_without_physical_network_sample(self):
        from moneymin import upload as upload_mod

        bare = _fixture_identity(
            model="SM-S901E", device_id="android.ssaid:completenonet",
            app_version="1.29.0", sdk=36)
        with patch.object(pb, "device_mode_requires_physical", return_value=True), \
             patch.object(pb, "require_campaign_physical_binding",
                          return_value=pb.physical_wire_fields(bare)), \
             patch.object(upload_mod, "_session_request") as session_request:
            with self.assertRaises(UploadError) as ctx:
                upload_mod.complete_upload(
                    object(), "upload-residual-missing", 1024,
                    network_type="wifi")
        self.assertIn("network_type", str(ctx.exception).casefold())
        self.assertEqual(getattr(ctx.exception, "phase", None), "complete")
        session_request.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()

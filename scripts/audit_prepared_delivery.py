"""Real prepared media through the delivery motor, with offline remote responses."""
import argparse
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("item", type=Path)
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    spec = importlib.util.spec_from_file_location("offline_audit_guard", project / "scripts/run_tests.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    exec(guard.OFFLINE_GUARD, {})
    item = json.loads(args.item.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="qfar-real-media-offline-") as temporary:
        os.environ["QMONEY_USER_ROOT"] = temporary
        os.environ["QMONEY_LIBRARY_ROOT"] = str(args.library_root)
        from moneymin import campaign, content_provenance, device_profile, minute_api, nymeria, upload
        from moneymin.campaign_types import AccountSpec
        email = "media-qa@example.invalid"
        calls = []
        task_id = item.get("task_id") or "offline-task"
        task_binding = "prepared_item"
        if item["source"] == "nymeria" and item.get("task_id") is None:
            # Standalone media QA has no account/catalog ID. Map the same action
            # to this offline catalog; leave every measured byte/window intact.
            # Production revalidation still checks the complete bound proof.
            name = item["task_name_authoritative"]
            registry_key = f"minute|{task_id}|{name}"
            candidate = nymeria.bind_task(item["_content_candidate"], task_name=name,
                                         task_id=task_id, registry_key=registry_key)
            proof = candidate["selection_evidence"]
            if proof.get("acquisition_plan") is not None:
                proof["acquisition_plan"] = nymeria.bind_task(
                    proof["acquisition_plan"], task_name=name,
                    task_id=task_id, registry_key=registry_key)
            item.update(task_id=task_id, registry_key=registry_key,
                        _content_candidate=candidate, selection_evidence=proof)
            lineage = item["content_provenance"]
            lineage["selection_evidence"] = proof
            lineage.pop("lineage_sha256")
            lineage["lineage_sha256"] = content_provenance.canonical_digest(lineage)
            item["source_provenance"].update(
                selection_evidence=proof, lineage_sha256=lineage["lineage_sha256"])
            task_binding = "offline_catalog_same_action"

        class Session:
            _live = True
            recording_policy = None
            def warmup(self):
                pass
            def request(self, method, path, body=None, **kwargs):
                calls.append({"method": method, "path": path})
                if method == "POST" and path.startswith("/api/v1/uploads?"):
                    return 201, json.dumps({"id": "offline-upload", "status": "initiated", "meta": {}})
                if path == "/api/v1/storage/sas/blobs":
                    return 200, json.dumps({"signed_urls": [
                        {"filename": f["filename"], "blob_url": "https://blob.invalid/" + f["filename"],
                         "expires_at": "2030-01-01T00:00:00Z"} for f in body["files"]]})
                if path.endswith("/complete"):
                    return 200, json.dumps({"id": "offline-upload", "status": "uploaded", "meta": {}})
                if path.endswith("/finalize"):
                    return 204, ""
                raise AssertionError(f"Unexpected offline protocol operation: {method} {path}")

        profile = device_profile.DeviceProfile(email=email, device_id="offline-fixture-device",
            boot_wall_ms=int(time.time() * 1000) - 3 * 86_400_000,
            created_wall_ms=int(time.time() * 1000) - 2 * 86_400_000)
        with ExitStack() as stack:
            stack.enter_context(patch.object(minute_api, "_request", side_effect=AssertionError("Real remote access forbidden")))
            stack.enter_context(patch.object(campaign.org_policy, "account_kind", return_value="claru"))
            stack.enter_context(patch.object(campaign.device_profile, "get_profile", return_value=profile))
            blob = stack.enter_context(patch.object(upload, "_put_blob_file", return_value=201))
            archive = stack.enter_context(patch.object(upload, "_put_blob", return_value=201))
            stack.enter_context(patch.object(upload, "evaluate_upload", return_value={
                "upload_id": "offline-upload", "checks": [{"id": "offline-contract", "status": "pass"}]}))
            result = campaign.upload_to_account(item, AccountSpec(email, "offline-org"), task_id,
                timeout_blob=10, evaluate=True, finalize=True, session=Session(), recover_pending=False)
            journals = upload.list_sidecars()
            members = []
            for call in archive.call_args_list:
                with zipfile.ZipFile(io.BytesIO(call.args[1])) as zipped:
                    members.extend(zipped.namelist())
                    assert len(zipped.namelist()) == 3
                    imu_name = next(name for name in zipped.namelist() if name.endswith(".imu.csv"))
                    frames_name = next(name for name in zipped.namelist() if name.endswith(".frames.csv"))
                    assert zipped.read(imu_name).splitlines()[0] == b"t,ax,ay,az,wx,wy,wz"
                    assert zipped.read(frames_name).splitlines()[0] == b"i,ptsNs,dtNs,tNs,key"
        report = {"real_media": True, "dataset": item["source"], "duration_ms": item["duration_ms"],
                  "task_binding": task_binding,
                  "remote_transport": "offline_fixture", "remote_acceptance_verified": False,
                  "ok": result.get("ok"), "finalized": result.get("finalized"),
                  "error": result.get("error"), "blob_transfers": blob.call_count + archive.call_count,
                  "sidecar_members": members,
                  "operations": calls, "journals": [{"state": row.get("state"),
                      "evaluation_verified": row.get("evaluation_verified"),
                      "finalized": row.get("finalized")} for row in journals]}
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report["ok"] or not report["finalized"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

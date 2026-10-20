"""campaign original routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('AccountSpec', 'AuthError', 'CampaignConfig', 'HOLO_CACHE_RUNNER', 'OperationLeaseError', 'OriginalAdmissionError', 'Path', 'RECOVERY', 'RUNNER', 'Session', 'TaskSpec', '_HEAVY_RUNNER_LOCK', '_identity_route', '_list_accounts', '_preflight_fingerprint', '_resolve_org', 'campaign_closing_response', 'campaign_drain', 'campaign_start_store', 'config', 'decode_json_state', 'jsonify', 'nymeria_library_worker', 'original_body', 'original_error', 'original_local_inspection', 'original_not_busy', 'original_pairs', 'original_policy', 'original_preflights', 'original_prepare', 'original_receipt_current', 'original_session', 'original_starts', 'original_summary', 'original_task', 'original_tasks', 'original_verify', 'preflight_lock', 're', 'recovery', 'request', 'time', 'token_store', 'uuid')


def original_library_paths(_services):
    root = _services.config.MEDIA_DATA_DIR / "ego4d"
    return root, root / "library.sqlite3"

class OriginalAdmissionError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status

def original_error(_services, code, status=400):
    # Neither OS/auth exceptions nor paths, metadata or provider bodies
    # are suitable public explanations for an admission failure.
    message = ("O último CREATE desta conta e organização foi recusado por App Check; "
               "nenhum envio foi iniciado. Revise Integrações → Rechecar App Check."
               if code == "app_check" else
               "A captura original não foi admitida. Revise os arquivos, a conta e a verificação antes de continuar.")
    return _services.jsonify({"ok": False, "error_code": code, "error": message}), status

def original_body(_services, *, starting=False):
    try:
        body = _services.decode_json_state(_services.request.get_data(cache=True))
        allowed = {"account_email", "task_id", "file_pairs", "expected_chunk_count",
                   "evaluate", "finalize", "start_request_id"}
        if starting:
            allowed.add("preflight_id")
        if set(body) - allowed:
            raise ValueError
        email = _services.token_store.email_key(body.get("account_email"))
        task_id, start_id = body.get("task_id"), body.get("start_request_id")
        if (not isinstance(task_id, str) or not _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id)
                or not isinstance(start_id, str) or not _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", start_id)
                or body.get("evaluate") is not True or body.get("finalize") is not True):
            raise ValueError
        pairs = body.get("file_pairs")
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 64:
            raise ValueError
        for pair in pairs:
            if (not isinstance(pair, dict) or set(pair) != {"video_path", "sidecar_path"}
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 4096
                           for value in pair.values())):
                raise ValueError
        if "expected_chunk_count" in body and (type(body["expected_chunk_count"]) is not int
                                                or body["expected_chunk_count"] != len(pairs)):
            raise ValueError
        result = dict(body, account_email=email)
        receipt_id = result.pop("preflight_id", None)
        if starting and (not isinstance(receipt_id, str) or not receipt_id
                         or len(receipt_id) > 160):
            raise _services.OriginalAdmissionError("original_preflight_missing")
        return result, receipt_id
    except _services.OriginalAdmissionError:
        raise
    except (ValueError, TypeError, _services.token_store.TokenStoreError):
        raise _services.OriginalAdmissionError("original_invalid_request") from None

def original_pairs(_services, body):
    return [(_services.Path(pair["video_path"]), _services.Path(pair["sidecar_path"])) for pair in body["file_pairs"]]

def original_local_inspection(_services, body):
    from ...original_capture import inspect_original_capture_group
    try:
        return inspect_original_capture_group(_services.original_pairs(body), account_email=body["account_email"],
                                              task_id=body["task_id"], expected_chunk_count=body.get("expected_chunk_count"),
                                              now=_services.time.time())
    except (ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_capture_invalid") from None

def original_session(_services, email):
    try:
        if email not in {_services.token_store.email_key(row["email"]) for row in _services._list_accounts()}:
            raise _services.OriginalAdmissionError("original_account_unavailable")
        with _services._identity_route(email):
            session = _services.Session.from_email(email)
            if _services.token_store.email_key(session.email) != _services.token_store.email_key(email):
                raise _services.OriginalAdmissionError("original_account_unavailable")
            org_key = _services._resolve_org(email, session=session)
            session.ensure_auth(org_key=org_key)
            return session, org_key
    except _services.OriginalAdmissionError:
        raise
    except (_services.AuthError, ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_policy_unavailable") from None

def original_tasks(_services, session, org_key):
    try:
        with _services._identity_route(session.email):
            source = session.all_tasks(org_key)
        if not isinstance(source, list) or len(source) > 4096:
            raise ValueError
        result, ids = [], set()
        for row in source:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", row["id"]):
                raise ValueError
            if row["id"] in ids:
                raise ValueError
            ids.add(row["id"])
            if any(key in row and not isinstance(row[key], str) for key in ("name", "description")):
                raise ValueError
            result.append({"id": row["id"], "name": (row.get("name", "").strip() or row["id"])[:256],
                           "description": row.get("description", "")[:2000]})
        return result
    except (_services.AuthError, ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_catalog_unavailable") from None

def original_task(_services, session, org_key, task_id):
    for task in _services.original_tasks(session, org_key):
        if task["id"] == task_id:
            return task
    raise _services.OriginalAdmissionError("original_task_unavailable")

def original_policy(_services, plan, session):
    from ...original_capture import validate_original_capture_session_policy
    from ...service_policy import RecordingPolicy
    try:
        # Shared with direct delivery: exact original CREATE bodies and
        # real policy reads, without CREATE, PUT or journal effects.
        with _services._identity_route(session.email):
            validate_original_capture_session_policy(plan, session)
            if not isinstance(session.recording_policy, RecordingPolicy):
                raise ValueError
            return session.recording_policy.limits()
    except (_services.AuthError, ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_policy_unavailable") from None

def original_prepare(_services, body, session, org_key):
    from ...original_capture import prepare_original_capture_plan
    from ...service_policy import RecordingPolicy
    try:
        if not isinstance(session.recording_policy, RecordingPolicy):
            raise _services.OriginalAdmissionError("original_policy_unavailable")
        plan = prepare_original_capture_plan(_services.original_pairs(body), account_email=body["account_email"],
                                             org_key=org_key, task_id=body["task_id"],
                                             expected_chunk_count=body.get("expected_chunk_count"), now=_services.time.time(),
                                             limits=session.recording_policy.limits())
        limits = _services.original_policy(plan, session)
        # warmup may have changed recording-config while checking policy.
        from ...original_capture import verify_original_capture_plan
        verify_original_capture_plan(plan, account_email=body["account_email"], org_key=org_key,
                                     task_id=body["task_id"], now=_services.time.time(), limits=limits)
        return plan, limits
    except _services.OriginalAdmissionError:
        raise
    except (ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_capture_invalid") from None

def original_summary(_services, plan, task):
    return {"source_mode": "original", "completion_policy": "explicit_finalize", "group_count": 1,
            "chunks": len(plan.captures), "expected_chunk_count": len(plan.captures),
            "total_duration_ms": sum(plan.durations_ms), "plan_digest": plan.digest,
            "content_digest": plan.content_digest,
            "task_binding": {"task_id": task["id"], "name": task["name"], "binding": plan.task_binding},
            "owner_binding": plan.owner_binding, "org_binding": plan.org_binding,
            "completeness_binding": plan.completeness_binding,
            "source_chunk_count_verified": plan.source_chunk_count_verified,
            "physical_provenance_verified": False, "provider_acceptance_verified": False,
            "media_probe_verified": True, "csv_temporal_consistency_verified": True,
            "clock_domains_preserved": True, "cross_clock_conversion_verified": False,
            "files": [{"media": {"bytes": capture.media.bytes, "sha256": capture.media.sha256},
                       "sidecar": {"bytes": capture.sidecar.bytes, "sha256": capture.sidecar.sha256}}
                      for capture in plan.captures]}

def original_not_busy(_services, email):
    if _services.RUNNER.running or _services.RECOVERY.running or _services.HOLO_CACHE_RUNNER.running or _services.nymeria_library_worker.running:
        raise _services.OriginalAdmissionError("original_busy", 409)
    try:
        items = _services.recovery.snapshot()["items"]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError
        if any(item.get("email") == email and item.get("blocks_campaign", True) for item in items):
            raise _services.OriginalAdmissionError("original_recovery_pending", 409)
        return items
    except _services.OriginalAdmissionError:
        raise
    except (ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_recovery_pending", 409) from None

def original_receipt_current(_services, receipt_id, receipt, body):
    if _services.original_preflights.get(receipt_id) is not receipt:
        raise _services.OriginalAdmissionError("original_preflight_missing", 409)
    if receipt["body"] != body:
        raise _services.OriginalAdmissionError("original_request_changed", 409)
    if receipt["expires"] <= _services.time.monotonic():
        raise _services.OriginalAdmissionError("original_preflight_expired", 409)
    if receipt["fingerprint"] != _services._preflight_fingerprint([body["account_email"]]):
        raise _services.OriginalAdmissionError("original_identity_changed", 409)

def original_verify(_services, receipt, body, *, limits=None):
    from ...original_capture import verify_original_capture_plan
    try:
        verify_original_capture_plan(receipt["plan"], account_email=body["account_email"],
                                     org_key=receipt["org_key"], task_id=body["task_id"], now=_services.time.time(),
                                     limits=receipt["limits"] if limits is None else limits)
    except (ValueError, OSError, RuntimeError):
        raise _services.OriginalAdmissionError("original_source_changed", 409) from None

def original_campaign_tasks(_services):
    try:
        email = _services.token_store.email_key(_services.request.args.get("account_email"))
        session, org_key = _services.original_session(email)
        return _services.jsonify({"ok": True, "account_email": email, "tasks": _services.original_tasks(session, org_key),
                        "physical_provenance_verified": False})
    except _services.OriginalAdmissionError as exc:
        return _services.original_error(exc.code, exc.status)
    except (ValueError, _services.token_store.TokenStoreError):
        return _services.original_error("original_invalid_request")

def original_campaign_preflight(_services):
    try:
        body, _ = _services.original_body()
        _services.original_local_inspection(body)  # All local invariants precede auth.
        session, org_key = _services.original_session(body["account_email"])
        from ...delivery.admission import campaign_create_blocker
        try:
            create_blocker = campaign_create_blocker([_services.AccountSpec(body["account_email"], org_key)])
        except (ValueError, OSError, RuntimeError):
            raise _services.OriginalAdmissionError("original_create_receipts_unavailable", 409) from None
        if create_blocker:
            raise _services.OriginalAdmissionError("app_check", 409)
        task = _services.original_task(session, org_key, body["task_id"])
        plan, limits = _services.original_prepare(body, session, org_key)
        summary = _services.original_summary(plan, task)
        with _services._HEAVY_RUNNER_LOCK:
            if _services.campaign_drain["requested"]:
                return _services.campaign_closing_response()
            _services.original_not_busy(body["account_email"])
            with _services.preflight_lock:
                now = _services.time.monotonic()
                for key in list(_services.original_preflights):
                    if _services.original_preflights[key]["expires"] <= now:
                        _services.original_preflights.pop(key)
                if len(_services.original_preflights) >= 32 or len(_services.original_starts) >= 256:
                    raise _services.OriginalAdmissionError("original_busy", 409)
                receipt_id = _services.uuid.uuid4().hex
                _services.original_preflights[receipt_id] = {"body": body, "plan": plan, "task": task,
                    "org_key": org_key, "limits": limits, "summary": summary,
                    "fingerprint": _services._preflight_fingerprint([body["account_email"]]), "expires": now + 600}
        return _services.jsonify({"ok": True, "preflight_id": receipt_id, "receipt": receipt_id,
                        "account_email": body["account_email"], "task_id": body["task_id"],
                        "start_request_id": body["start_request_id"], "original_summary": summary,
                        "completion_policy": "explicit_finalize", "readiness": {"ready": True}})
    except _services.OriginalAdmissionError as exc:
        return _services.original_error(exc.code, exc.status)

def start_original_campaign(_services):
    try:
        body, receipt_id = _services.original_body(starting=True)
        with _services.preflight_lock:
            prior = _services.campaign_start_store.lookup(body["start_request_id"], kind="original",
                                                receipt_id=receipt_id, body=body)
            if prior is not None:
                if prior["phase"] == "claimed":
                    return _services.original_error("original_start_outcome_unknown", 500)
                return _services.jsonify({**prior["reply"], "already_running": True})
            receipt = _services.original_preflights.get(receipt_id)
            if receipt is None:
                raise _services.OriginalAdmissionError("original_preflight_missing", 409)
            _services.original_receipt_current(receipt_id, receipt, body)
        _services.original_verify(receipt, body)  # Changed local files fail before auth.
        session, org_key = _services.original_session(body["account_email"])
        if org_key != receipt["org_key"]:
            raise _services.OriginalAdmissionError("original_identity_changed", 409)
        from ...delivery.admission import campaign_create_blocker
        try:
            create_blocker = campaign_create_blocker([_services.AccountSpec(body["account_email"], org_key)])
        except (ValueError, OSError, RuntimeError):
            raise _services.OriginalAdmissionError("original_create_receipts_unavailable", 409) from None
        if create_blocker:
            raise _services.OriginalAdmissionError("app_check", 409)
        _services.original_task(session, org_key, body["task_id"])
        _services.original_policy(receipt["plan"], session)
        with _services._HEAVY_RUNNER_LOCK:
            if _services.campaign_drain["requested"]:
                return _services.campaign_closing_response()
            items = _services.original_not_busy(body["account_email"])
            with _services.preflight_lock:
                _services.original_receipt_current(receipt_id, receipt, body)
                # Recheck identity, organization, policy and source bytes at
                # admission, after waiting for the shared runner barrier.
                if (_services.token_store.email_key(session.email) != body["account_email"]
                        or _services._resolve_org(body["account_email"], session=session) != org_key):
                    raise _services.OriginalAdmissionError("original_identity_changed", 409)
                # Availability can change while waiting; the reviewed
                # label and plan remain frozen after this membership read.
                _services.original_task(session, org_key, body["task_id"])
                limits = _services.original_policy(receipt["plan"], session)
                _services.original_verify(receipt, body, limits=limits)
                plan, task = receipt["plan"], receipt["task"]
                cfg = _services.CampaignConfig(accounts=[_services.AccountSpec(body["account_email"], org_key)],
                    tasks=[_services.TaskSpec(task_id=task["id"], scenario="original", min_dur_s=sum(plan.durations_ms)/1000,
                                    max_dur_s=sum(plan.durations_ms)/1000, task_name=task["name"],
                                    task_label=task["name"], task_description=task["description"], count=1)],
                    work_dir=_services.config.MEDIA_DATA_DIR / "original", evaluate=True, finalize=True, account_workers=1,
                    account_max_attempts=1, shuffle_schedule=False, share_clips=False, unique_video=False,
                    allow_new_accounts=False, cleanup_after_upload=False, realistic_timeline=False,
                    dataset_provider="original", start_request_id=body["start_request_id"],
                    original_capture_plan=plan, recovery_exclusions=_services.recovery.campaign_exclusions(items))
                reply = {"ok": True, "already_running": False, "start_request_id": body["start_request_id"],
                         "preflight_id": receipt_id, "receipt": receipt_id, "account_email": body["account_email"],
                         "task_id": body["task_id"], "total_sends": 1, "accounts": [body["account_email"]],
                         "selected_tasks": [{"task_id": task["id"]}], "original_summary": receipt["summary"]}
                # Claim the UUID before invoking a collaborator that may
                # accept work and then fail to return a response.
                # Hashing/probing and policy reads can be slow. Recheck the
                # monotonic TTL and owner fingerprint immediately at start.
                _services.original_receipt_current(receipt_id, receipt, body)
                claimed, prior = _services.campaign_start_store.claim(body["start_request_id"], kind="original",
                    receipt_id=receipt_id, body=body,
                    bindings={"accounts":[{"email":body["account_email"],"org_key":org_key}],
                              "tasks":[task["id"]],"session_id":plan.session_id,
                              "plan_digest":plan.digest,"content_digest":plan.content_digest,
                              "expected_chunk_count":len(plan.captures)},
                    protected_assets=[{"path":str(asset.path),"sha256":asset.sha256}
                                      for capture in plan.captures for asset in (capture.media,capture.sidecar)])
                if not claimed:
                    if prior["phase"] == "claimed":
                        return _services.original_error("original_start_outcome_unknown",500)
                    return _services.jsonify({**prior["reply"],"already_running":True})
                try:
                    _services.RUNNER.start(cfg)
                except Exception:
                    # A lost/exceptional start outcome is not proof of
                    # rejection. The caller must poll this UUID, never repost.
                    return _services.original_error("original_start_outcome_unknown", 500)
                _services.original_preflights.pop(receipt_id, None)
                _services.campaign_start_store.acknowledge(body["start_request_id"],reply)
        return _services.jsonify(reply)
    except _services.campaign_start_store.StartConflictError:
        return _services.original_error("original_start_conflict",409)
    except (_services.campaign_start_store.StartStoreError, _services.OperationLeaseError, OSError):
        return _services.original_error("original_start_outcome_unknown",500)
    except _services.OriginalAdmissionError as exc:
        return _services.original_error(exc.code, exc.status)

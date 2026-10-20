"""Upload journal reconciliation and immutable attempt matching."""
from __future__ import annotations

def _acknowledge_campaign_upload(_services, session_id: str, rows: list[dict[str, Any]] | None = None) -> None:
    # Callers that already validated a complete group can avoid rescanning the
    # entire sidecar store. Standalone post-upload acknowledgments still take a
    # fresh validated listing.
    for source in _services.list_sidecars() if rows is None else rows:
        row = dict(source)
        if row.get("session_id") == session_id:
            row["campaign_reconciled"] = True
            _services.save_sidecar(row)

def _legacy_upload_context(_services, session_id: str, email: str) -> dict[str, Any] | None:
    return _services._legacy_upload_contexts({(session_id, email)}).get((session_id, email))

def _legacy_upload_contexts(_services, wanted: set[tuple[str, str]], journals: list[dict] | None = None) -> dict:
    """Read each history file once for a batch of legacy journals."""
    found = {}
    direct_matches = {}
    media_matches = {}
    journal_media = {}
    for row in journals or []:
        identity = (row.get("session_id"), row.get("account_email"))
        name = str(row.get("local_video_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if identity in wanted and name and row.get("task_id"):
            journal_media.setdefault((name, row["task_id"], identity[1]), set()).add(identity)
    paths = {p for p in _services.config.DATA_DIR.glob("campaign_*.json") if _services.is_campaign_history_name(p.name)}
    # Older installations kept campaign history beside the media library.
    paths.update(p for p in _services.config.MEDIA_DATA_DIR.glob("campaign_*.json") if _services.is_campaign_history_name(p.name))
    for path in sorted(paths):
        try:
            history = _services.json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(history, dict):
            continue
        for item in history.get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("clip_uid"), str) or not item["clip_uid"]:
                continue
            for row in item.get("accounts") or []:
                if not isinstance(row, dict):
                    continue
                identity = (row.get("session_id"), row.get("email"))
                if not isinstance(identity[1], str):
                    continue
                key = item.get("registry_key") or (
                    f"minute|{item['task_id']}|{item['task_name']}"
                    if item.get("task_id") and item.get("task_name") else item.get("task_scenario"))
                if not isinstance(key, str) or not key:
                    continue
                context = {"registry_key": key, "clip_uid": item["clip_uid"],
                           "task_id": item.get("task_id"), "history_name": path.name}
                if identity in wanted:
                    direct_matches.setdefault(identity, {})[(key, item["clip_uid"])] = context
                name = str(item.get("video_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
                for target in journal_media.get((name, item.get("task_id"), row.get("email")), ()):
                    media_matches.setdefault(target, {})[(key, item["clip_uid"])] = context
    # A filename alone is not proof. Require the same task and account and a
    # unique clip/registry mapping across all available histories.
    for identity, matches in direct_matches.items():
        if len(matches) == 1:
            found[identity] = next(iter(matches.values()))
    for identity, matches in media_matches.items():
        if identity not in direct_matches and len(matches) == 1:
            found[identity] = next(iter(matches.values()))
    return found

def _terminal_retry_parent(_services, rows: list[dict[str, Any]], account: AccountSpec,
                           item: dict[str, Any], task_id: str) -> str | None:
    """Return the unique leaf of validated terminal receipts for this delivery."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sid = row.get("session_id")
        if isinstance(sid, str) and sid:
            groups.setdefault(sid, []).append(row)

    reset_checker = _services.sent_registry.recovery_reset_checker()
    terminal: dict[str, dict[str, Any]] = {}
    for sid, chunks in groups.items():
        first = next((row for row in chunks
                      if isinstance(row.get("account_email"), str)
                      and row["account_email"].strip().casefold()
                      == account.email.strip().casefold()), None)
        if first is None or not all(
                isinstance(row.get("account_email"), str)
                and row["account_email"].strip().casefold() == account.email.strip().casefold()
                and row.get("org_key") == account.org_key
                and row.get("task_id") == task_id
                and row.get("campaign_context") == first.get("campaign_context")
                for row in chunks):
            continue
        context = first.get("campaign_context")
        if (not isinstance(context, dict)
                or context.get("task_id") not in (None, task_id)
                or context.get("clip_uid") != item.get("clip_uid")
                or not isinstance(context.get("registry_key"), str)
                or not context["registry_key"]):
            continue
        parent = context.get("retry_of_session_id")
        if parent is not None and (not isinstance(parent, str) or not parent or parent == sid):
            raise _services.UploadError(
                "Vínculo da tentativa terminal inválido; preserve os registros para revisão.",
                transient=False, phase="recovery", review_required=True)
        expected = first.get("expected_chunk_count", 1)
        indices = [row.get("chunk_index", 0) for row in chunks]
        structurally_complete = (
            _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", sid) is not None
            and type(expected) is int and expected > 0 and len(chunks) == expected
            and all(type(index) is int and index >= 0 for index in indices)
            and set(indices) == set(range(expected)))
        if (structurally_complete and _services.all_terminal_remote_failure(chunks)
                and not reset_checker(sid, context["registry_key"],
                                      context.get("history_name", ""))):
            terminal[sid] = context

    if not terminal:
        return None
    referenced = {context.get("retry_of_session_id") for context in terminal.values()
                  if isinstance(context.get("retry_of_session_id"), str)}
    leaves = sorted(set(terminal) - referenced)
    if len(leaves) != 1:
        raise _services.UploadError(
            "Há mais de uma tentativa terminal sem vínculo; preserve os recibos para revisão.",
            transient=False, phase="recovery", review_required=True)
    return leaves[0]

def _reconcile_uploads(_services, rows: list[dict[str, Any]], account: AccountSpec,
                       item: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    groups: dict[str, list[dict[str, Any]]] = {}

    def owned(row):
        email = row.get("account_email")
        return (isinstance(email, str) and email.strip().casefold() == account.email.strip().casefold()
                and row.get("org_key") == account.org_key)

    for row in rows:
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid:
            if owned(row) and row.get("task_id") == task_id:
                return {"email": account.email, "ok": False, "finalized": False,
                        "error": "Registro de envio anterior com identidade inválida; preserve os arquivos para revisão."}
            continue
        # Keep all rows of a session, including malformed or foreign chunks.
        # A filtered subset must not become evidence of a complete receipt.
        groups.setdefault(sid, []).append(row)
    # Validate every owned session before any registry/ACK mutation. A later
    # corrupt session must not consume earlier valid receipts, and journals
    # for another clip must receive the same integrity check as this item.
    contexts: dict[str, Any] = {}
    lineages: dict[str, dict[str, Any]] = {}
    owned_groups = [(sid, chunks, first) for sid, chunks in groups.items()
                    if (first := next((row for row in chunks if owned(row)), None)) is not None]
    reset_checker = _services.sent_registry.recovery_reset_checker() if owned_groups else None
    active_groups = [(sid, chunks, first) for sid, chunks, first in owned_groups
                     if not reset_checker(sid, "", "")]
    missing_contexts = {(sid, account.email) for sid, _chunks, first in active_groups
                        if not first.get("campaign_context")}
    legacy_contexts = (_services._legacy_upload_contexts(missing_contexts, rows)
                       if missing_contexts else {})
    for sid, chunks, first in active_groups:
        context = first.get("campaign_context") or legacy_contexts.get((sid, account.email))
        contexts[sid] = context
        lineage = context.get("content_provenance") if isinstance(context, dict) else None
        if lineage is not None:
            from ..content_provenance import canonical_digest
            try:
                if (not isinstance(lineage, dict) or lineage.get("session_id") != sid
                        or lineage.get("task_id") != first.get("task_id")
                        or lineage.get("org_key") != account.org_key
                        or lineage.get("delivery_binding_sha256") != canonical_digest({
                            key: value for key, value in lineage.items()
                            if key != "delivery_binding_sha256"})):
                    raise ValueError
                lineages[sid] = _services.json.loads(_services.json.dumps(lineage, allow_nan=False))
            except (TypeError, ValueError, OverflowError) as exc:
                raise _services.UploadError(
                    "Vínculo de conteúdo da retomada inválido; preserve os registros.",
                    transient=False, phase="recovery", review_required=True) from exc
    matched = None
    acknowledgments = []
    for sid, chunks, first in owned_groups:
        if sid not in contexts:
            continue
        context = contexts[sid]
        if (not isinstance(context, dict)
                or any(not isinstance(context.get(key), str) or not context[key]
                       for key in ("registry_key", "clip_uid"))):
            if first.get("task_id") == task_id:
                matched = {"email": account.email, "ok": False, "session_id": sid,
                           "error": "Envio anterior sem identificação do clipe; confira a sessão antes de reenviar."}
            continue
        same = context["clip_uid"] == item.get("clip_uid") and first.get("task_id") == task_id
        key = item.get("registry_key") if same else context["registry_key"]
        expected = first.get("expected_chunk_count", 1)
        indices = [row.get("chunk_index") for row in chunks]
        structurally_complete = (_services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", sid) is not None
                    and type(expected) is int and expected > 0 and len(chunks) == expected
                    and all(type(index) is int and index >= 0 for index in indices)
                    and set(indices) == set(range(expected))
                    and all(owned(row)
                            and type(row.get("expected_chunk_count", 1)) is int
                            and row.get("expected_chunk_count", 1) == expected
                            and row.get("task_id") == first.get("task_id")
                            and row.get("campaign_context") == first.get("campaign_context")
                            for row in chunks))
        # A GET-validated terminal failure is not a delivery. Only a complete,
        # ownership-consistent group is ignored here; mixed or partial groups
        # remain pending and continue to block a new session.
        if structurally_complete and _services.all_terminal_remote_failure(chunks):
            continue
        complete = (structurally_complete
                    and all(_services.journal_delivery_confirmed(row) for row in chunks))
        if complete and reset_checker(
                sid, context["registry_key"], context.get("history_name", "")):
            continue
        if complete and all(row.get("campaign_reconciled") is True for row in chunks):
            continue
        if complete:
            acknowledgments.append((sid, key or context["registry_key"], context["clip_uid"], chunks))
        if same and (matched is None or not complete):
            matched = {"email": account.email, "ok": complete, "finalized": complete,
                       "org_key": account.org_key,
                       "session_id": sid, "recovered": complete,
                       "error": None if complete else "Envio anterior ainda pendente; nova sessão não criada."}
            if not complete and any(row.get("authentication_required") is True for row in chunks):
                matched["authentication_required"] = True
                matched["retryable"] = False
                if not structurally_complete:
                    matched["review_required"] = True
                matched["error"] = "Autenticação recusada; recibo anterior preservado para retomada."
            if sid in lineages:
                # Keep the immutable planning descriptor and expose receipt
                # confirmation separately. Updating its flag would invalidate
                # its original digest or turn a plan into evidence of delivery.
                matched['content_provenance'] = lineages[sid]
                matched['content_receipt_confirmed']=complete
    # All owned sessions, including unrelated clips, have now been checked.
    # Only after that full pass may receipts update the sent index or journals.
    if acknowledgments:
        _services.sent_registry.mark_sent_many([
            (scenario, clip_uid, account.email)
            for _sid, scenario, clip_uid, _chunks in acknowledgments])
        for sid, _scenario, _clip_uid, chunks in acknowledgments:
            _services._acknowledge_campaign_upload(sid, chunks)
    return matched

def _nymeria_resample_delivery(_services, item: dict[str, Any], *, origin_ns: int | None = None,
                               duration_ms: int | None = None) -> tuple[str, dict[str, Any]]:
    """Measure one delivery interval from the original VRS sensor records."""
    from .. import content_provenance, nymeria_vrs
    try:
        source = item["_nymeria_resample_inputs"]
        transform = item["content_provenance"]["transform"]
        first_rgb = int(transform["first_rgb_capture_ns"])
        if (source["motion_vrs"] != item["_content_inputs"]["source_imu"]
                or source["data_vrs"] != item["_content_inputs"]["source_video"]
                or source["source_device_window_ns"] != transform["source_device_window_ns"]
                or type(source["origin_ns"]) is not int or source["origin_ns"] != first_rgb
                or type(source["duration_ms"]) is not int or source["duration_ms"] != item["duration_ms"]
                or source["imu_label"] != "imu-right"
                or type(source["sample_rate_hz"]) is not int or source["sample_rate_hz"] != 500):
            raise ValueError(content_provenance.INTEGRITY_ERROR)
        origin = first_rgb if origin_ns is None else origin_ns
        duration = item["duration_ms"] if duration_ms is None else duration_ms
        if (type(origin) is not int or type(duration) is not int or duration <= 0
                or origin < first_rgb
                or origin + duration * 1_000_000 > first_rgb + item["duration_ms"] * 1_000_000 + 1000):
            raise ValueError(content_provenance.INTEGRITY_ERROR)
        read_stats, stats = {}, {}
        read_start, read_end = ((source["source_device_window_ns"])
                                if origin_ns is None and duration_ms is None
                                else (origin, origin + duration * 1_000_000))
        samples = nymeria_vrs.read_imu_samples(
            _services.Path(source["motion_vrs"]["path"]), t0_ns=read_start, t1_ns=read_end,
            label=source["imu_label"], stats=read_stats)
        output = nymeria_vrs.build_imu_csv_from_samples(
            samples, t0_ns=origin, duration_ms=duration, sample_rate_hz=500,
            source_dropped_timestamps_ns=read_stats["droppedRowTimestampsNs"], stats=stats)
        content_provenance.verify_input(source["motion_vrs"])
        return output, stats
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        raise _services.UploadError("Fonte ou diagnóstico IMU Nymeria divergente; novo preparo necessário.",
                          transient=False, phase="prepare") from exc

REQUIRES = ('Path', 'UploadError', '_acknowledge_campaign_upload', '_legacy_upload_contexts', 'all_terminal_remote_failure', 'config', 'is_campaign_history_name', 'journal_delivery_confirmed', 'json', 'list_sidecars', 're', 'save_sidecar', 'sent_registry')

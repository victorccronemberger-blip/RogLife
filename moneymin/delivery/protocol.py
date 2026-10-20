"""Protocol upload protocol core with declared collaborators."""
from __future__ import annotations
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

@dataclass
class ProtocolServices:
    JsonStateError: Any
    STATE_DONE: Any
    UploadError: Any
    _error_detail: Any
    _header: Any
    _http_upload_error: Any
    _require_completion_flags: Any
    _session_request: Any
    _validate_known_receipt: Any
    _validate_native_upload_out: Any
    decode_json_state: Any
    get_upload: Any
    json: Any
    random: Any
    re: Any
    recorded_at_to_wall_ms: Any
    time: Any

REQUIRES = ('JsonStateError', 'STATE_DONE', 'UploadError', '_error_detail', '_header', '_http_upload_error', '_require_completion_flags', '_session_request', '_validate_known_receipt', '_validate_native_upload_out', 'decode_json_state', 'get_upload', 'json', 'random', 're', 'recorded_at_to_wall_ms', 'time')

def _session_request(_services, 
    session: Any,
    method: str,
    path: str,
    body: Any = None,
) -> tuple[int, str, dict[str, str]]:
    """Usa headers de resposta quando a sessão oferece a API detalhada."""
    # Dynamic adapters can manufacture an attribute for any name. Use the
    # detailed API only when it is declared, while resolving its value late.
    detailed = (getattr(session, "request_detailed", None)
                if getattr_static(session, "request_detailed", None) is not None else None)
    if callable(detailed):
        response = detailed(method, path) if body is None else detailed(method, path, body)
        try:
            if hasattr(response, "status"):
                return (
                    int(response.status), str(response.text),
                    {str(k): str(v) for k, v in dict(response.headers).items()},
                )
            if isinstance(response, tuple) and len(response) == 3:
                status, text, headers = response
                return int(status), str(text), {
                    str(k): str(v) for k, v in dict(headers or {}).items()}
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        # A detailed request has already been dispatched. Falling back here
        # could duplicate its remote effect and expose malformed private data.
        raise _services.UploadError("Resposta HTTP detalhada inválida; preserve o registro para revisão.",
                                    transient=False, phase="request", review_required=True) from None
    status, text = (session.request(method, path) if body is None
                    else session.request(method, path, body))
    return int(status), str(text), {}
def _header(_services, headers: dict[str, str], name: str) -> str | None:
    wanted = name.casefold()
    return next((value for key, value in headers.items()
                 if key.casefold() == wanted), None)
def _error_detail(_services, text: str) -> str:
    try:
        body = _services.json.loads(text)
    except (_services.json.JSONDecodeError, ValueError, TypeError):
        return text[:500]
    if isinstance(body, dict):
        for key in ("detail", "message", "error", "code"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:500]
            if isinstance(value, dict):
                for nested in ("detail", "message", "code", "reason"):
                    nested_value = value.get(nested)
                    if isinstance(nested_value, str) and nested_value:
                        return nested_value[:500]
    return text[:500]
def _http_upload_error(_services, 
    phase: str,
    status: int,
    text: str,
    headers: dict[str, str] | None = None,
) -> UploadError:
    response_headers = headers or {}
    blocked = _services._header(response_headers, "X-Blocked-Reason")
    if phase in ("GET /uploads", "evaluate"):
        # These paths previously discarded policy headers. Retain only known
        # protocol markers; arbitrary values may contain private credentials.
        blocked = (blocked.casefold() if isinstance(blocked, str) and blocked.casefold()
                   in {"user", "device", "uber-device", "app_check"} else None)
    # Create/SAS responses can contain signed URLs. These are temporary
    # credentials and must not become result.error or persisted journal text.
    private_response = phase in ("SAS", "POST /uploads")
    detail = ("o serviço não concluiu esta etapa"
              if private_response else _services._error_detail(text))
    message = f"{phase} falhou ({status}): {detail}"
    if blocked:
        displayed_reason = (blocked if not private_response or _services.re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", blocked) else "não especificado")
        message += f" [bloqueio: {displayed_reason}]"
    # Explicit App Check blocks are policy rejections, including when Minute
    # returns them as HTTP 429. They must not look like rate-limit retries.
    app_check_block = isinstance(blocked, str) and blocked.casefold() == "app_check"
    transient = (
        False if app_check_block
        else (status == -1 or status in (408, 429) or status >= 500)
    )
    return _services.UploadError(
        message,
        status_code=status,
        transient=transient,
        blocked_reason=blocked,
        phase=phase,
    )
def _status_from_exception(_services, exc: BaseException) -> int | None:
    # curl_cffi exposes libcurl transport errors (for example CURLE_COULDNT_RESOLVE_HOST=6)
    # through ``code``. They are not HTTP responses and must remain retryable
    # network failures. Only actual HTTP status ranges are accepted here.
    for raw in (getattr(exc, "status_code", None), getattr(exc, "code", None)):
        if raw is None:
            continue
        try:
            status = int(raw)
        except (TypeError, ValueError):
            continue
        if status == -1 or 100 <= status <= 599:
            return status
    match = _services.re.search(r"\((\d{3})\)", str(exc))
    if not match:
        return None
    status = int(match.group(1))
    return status if 100 <= status <= 599 else None
def _with_retry(_services, 
    fn: Callable[[], Any],
    *,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    retry_delay: float = 1.0,
    label: str = "etapa",
) -> tuple[Any, int]:
    """Executa `fn` com auto-retry em falhas transientes.

    O app nativo usa "auto-retry" para falhas de create/sas, transport e sidecar
    (strings de log: `[upload] transient ... -> auto-retry`). Aqui replicamos com
    backoff exponencial: 1s, 1.5s, 2.25s... Devemos exaurir os retries.
    Retorna (resultado, tentativas_usadas).
    Levanta a última UploadError depois de `max_retries` tentativas.
    """
    attempt = 0
    delay = retry_delay
    last: _services.UploadError | None = None
    while attempt < max_retries:
        attempt += 1
        try:
            return fn(), attempt
        except _services.UploadError as exc:
            last = exc
            if not exc.retryable or attempt >= max_retries:
                break
            _services.time.sleep(delay * _services.random.uniform(0.80, 1.20))
            delay *= retry_backoff
    assert last is not None
    # Preserve a contagem mesmo quando a atribuição do retorno de _with_retry
    # não acontece. Todos os caminhos de falha conseguem então registrar a
    # quantidade correta sem depender de uma variável local ainda inexistente.
    last.attempts = attempt
    raise last
def fail_upload(_services, session: Any, upload_id: str, error_message: str) -> dict[str, Any]:
    """PATCH /api/v1/uploads/{id}/fail — marca o upload como falho no backend.

    O app nativo usa isso quando o upload falha após o registro (`_failUpload`).
    O backend guarda `error_message` (max 500 chars) no registro.
    Retorna o UploadOut parseado. Levanta UploadError se o backend recusar.
    """
    status, text = session.request(
        "PATCH", f"/api/v1/uploads/{upload_id}/fail",
        {"error_message": error_message[:500]},
    )
    if status != 200:
        raise _services.UploadError(f"PATCH /fail falhou ({status}).", status_code=status,
                          transient=(status == -1 or status in (408, 429) or status >= 500), phase="fail")
    try:
        parsed = _services.decode_json_state(text)
    except _services.JsonStateError:
        raise _services.UploadError("Resposta de sinalização de falha inválida; recibo preservado para revisão.",
                          status_code=status, transient=False, phase="fail", review_required=True) from None
    try:
        _services._validate_known_receipt(parsed, upload_id, "fail")
    except _services.UploadError as exc:
        exc.status_code = status
        raise
    return parsed
def delete_upload(_services, session: Any, upload_id: str) -> bool:
    """DELETE /api/v1/uploads/{upload_id} — remove o upload do backend.

    Usado pela política perfect-only: se o evaluate reprovar, o registro de
    upload é apagado (rows + referência). Sucesso = 204 No Content.
    Retorna True se deletado.
    """
    status, text = session.request("DELETE", f"/api/v1/uploads/{upload_id}")
    if status == 204:
        return True
    raise _services.UploadError(f"DELETE do upload falhou ({status}).", status_code=status,
                      transient=(status == -1 or status in (408, 429) or status >= 500), phase="delete-upload")
def delete_session(_services, session: Any, org_key: str, session_id: str) -> bool:
    """DELETE /api/v1/organizations/{org}/sessions/{sid} — remove a sessão.

    Apaga a sessão de gravação (rows + blobs do Azure) para o caller.
    Sucesso = 204 No Content. Retorna True se deletada.
    """
    status, text = session.request(
        "DELETE", f"/api/v1/organizations/{org_key}/sessions/{session_id}",
    )
    if status == 204:
        return True
    raise _services.UploadError(
        f"DELETE da sessão falhou ({status}).", status_code=status,
        transient=(status == -1 or status in (408, 429) or status >= 500), phase="delete-session")
def _validate_known_receipt(_services, data: dict[str, Any], upload_id: str, phase: str) -> None:
    """An optional legacy ID must still refer to the requested receipt.

    Empty legacy acknowledgements remain supported. This does not implement
    the complete native UploadOut schema or prove remote acceptance.
    """
    if any(data[key] != upload_id or not isinstance(data[key], str)
           for key in ("id", "uploadId", "upload_id") if key in data):
        raise _services.UploadError("Resposta referente a outro recibo ou com identidade inválida; "
                          "envio preservado para revisão.", transient=False,
                          phase=phase, review_required=True)
def _validate_native_upload_out(_services, data: dict[str, Any], phase: str) -> None:
    """UploadOut declared in APK 1.29 (decompiled.js L357468); status is a string.

    Preserve finite extra metadata for review. blob_path belongs to meta and,
    when present, must be a string; an absent field differs from JSON null.
    A structurally valid response alone does not prove physical capture quality.
    """
    meta = data.get("meta")
    if (not isinstance(data.get("id"), str)
            or not isinstance(data.get("status"), str)
            or not isinstance(meta, dict)
            or ("blob_path" in meta and not isinstance(meta["blob_path"], str))):
        raise _services.UploadError("Resposta do upload incompleta ou com tipos inválidos; "
                          "recibo preservado para revisão.", transient=False,
                          phase=phase, review_required=True)
def get_upload(_services, session: Any, upload_id: str) -> dict[str, Any]:
    """GET /api/v1/uploads/{upload_id} — consulta o status do upload.

    O app nativo consulta o registro para saber o estado (`upload_status`).
    Devolve o dict com campos camelCase (uploadId, sessionId, logId, status,
    durationMs, recordedAt, ...). Levanta UploadError se não achar (404).
    """
    status, text, response_headers = _services._session_request(
        session, "GET", f"/api/v1/uploads/{upload_id}")
    if status != 200:
        raise _services._http_upload_error("GET /uploads", status, text, response_headers)
    try:
        parsed = _services.decode_json_state(text)
    except _services.JsonStateError:
        raise _services.UploadError("Resposta de consulta do upload inválida; envio preservado para revisão.",
                          transient=False, phase="query", review_required=True) from None
    _services._validate_known_receipt(parsed, upload_id, "query")
    return parsed
def _require_completion_flags(_services, suppress_per_chunk_catbear: bool,
                              session_complete: bool = False) -> None:
    if type(suppress_per_chunk_catbear) is not bool or type(session_complete) is not bool:
        raise _services.UploadError("Política de conclusão inválida; use valores booleanos.",
                          transient=False, phase="preflight")
def _journal_flag(_services, journal: dict[str, Any], key: str) -> bool:
    """Absence is the legacy False policy; never coerce an existing value."""
    value = journal.get(key, False)
    if type(value) is not bool:
        raise _services.UploadError("Política do registro de envio inválida; preserve o arquivo para revisão.",
                          transient=False, phase="recovery")
    return value
def _journal_recorded_at(_services, journal: dict[str, Any]) -> str:
    value = journal.get("recorded_at")
    if not isinstance(value, str) or not value.strip() or _services.recorded_at_to_wall_ms(value) is None:
        raise _services.UploadError("Horário original do envio ausente ou inválido; preserve o arquivo para revisão.",
                          transient=False, phase="recovery")
    return value
def _journal_has_remote_receipt(_services, journal: dict[str, Any]) -> bool:
    upload_id = journal.get("upload_id", "")
    if upload_id is not None and not isinstance(upload_id, str):
        raise _services.UploadError("Recibo de envio inválido; preserve o arquivo para revisão.",
                          transient=False, phase="recovery")
    return (bool(upload_id and upload_id.strip()) or journal.get("state") == _services.STATE_DONE
            or journal.get("phase") == "done" or journal.get("finalized") is True)
def _existing_receipt_error(_services, ) -> UploadError:
    return _services.UploadError("Este envio já possui um recibo ou estado de conclusão; use Retomada "
                       "para as etapas compatíveis ou preserve o registro para revisão. "
                       "Um novo envio não substituirá o recibo existente.",
                       transient=False, phase="recovery")
def _journal_creation_uncertain(_services, journal: dict[str, Any]) -> bool:
    """No receipt does not prove that a previously attempted CREATE failed.

    Legacy queued journals precede any remote creation. Other legacy phases
    lack a durable request marker, so cannot authorize another CREATE safely.
    Known receipts retain their existing completion/finalization handling.
    """
    upload_id = journal.get("upload_id")
    if isinstance(upload_id, str) and upload_id.strip():
        return False
    if (journal.get("request_not_sent") is True
            and journal.get("review_required") is not True
            and journal.get("phase") in {"create", "create_in_flight"}):
        # Durable proof that CREATE never left the client. Intent may remain
        # monotonic (create_attempted) while recovery may resume the same SID.
        return False
    if journal.get("phase") in {"create_in_flight", "registered"}:
        return True
    if "create_attempted" in journal:
        return journal["create_attempted"] is not False
    return journal.get("phase") != "queued"
def _uncertain_creation_error(_services, ) -> UploadError:
    return _services.UploadError("A criação anterior pode ter sido aceita sem um recibo local. "
                       "O envio foi preservado para revisão; uma nova criação foi bloqueada.",
                       transient=False, phase="recovery")
def _upload_owner(_services, session: Any, profile: DeviceProfile | None) -> str:
    """Use the session's declared owner, never infer auth from device metadata."""
    value = getattr(session, "email", None)
    owner = value.strip().casefold() if isinstance(value, str) else ""
    profile_value = getattr(profile, "email", None) if profile is not None else None
    profile_owner = (profile_value.strip().casefold()
                     if isinstance(profile_value, str) else "")
    if owner and profile_owner and owner != profile_owner:
        raise _services.UploadError("O perfil de dispositivo difere da conta da sessão; o envio foi bloqueado.",
                          transient=False, phase="preflight")
    return owner
def _validate_journal_owner(_services, journal: dict[str, Any], session_owner: str) -> None:
    value = journal.get("account_email")
    if value is None:
        return  # Legacy unknown ownership is preserved, not retrospectively inferred.
    if not isinstance(value, str):
        raise _services.UploadError("Dono do registro de envio inválido; preserve o arquivo para revisão.",
                          transient=False, phase="recovery")
    owner = value.strip().casefold()
    if owner and (not session_owner or owner != session_owner):
        raise _services.UploadError("A conta da sessão não corresponde ao dono do registro de envio; "
                          "o arquivo foi preservado para revisão.",
                          transient=False, phase="recovery")
def complete_upload(_services, 
    session: Any,
    upload_id: str,
    size_bytes: int,
    *,
    suppress_per_chunk_catbear: bool = True,
    session_complete: bool = False,
    network_type: str | None = "wifi",
    _native_response_schema: bool = False,
) -> dict[str, Any]:
    """Confirma um blob já enviado; operação reutilizável após reinício.

    No APK 1.29.0, ``_completeUpload`` envia sempre ``suppress_per_chunk_catbear``
    e ``network_type`` (wifi|cellular); ``session_complete`` só no último chunk.
    O QMoney usa ``complete -> finalize`` em etapas separadas. Sem prova de
    aceitação remota pelo Catbear.

    Em modo device, ``network_type`` vem obrigatoriamente da amostra do celular
    conectado (todos os callers, inclusive recovery); sem amostra a operação
    recusa em vez de enviar o default sintético ``wifi``.
    """
    _services._require_completion_flags(suppress_per_chunk_catbear, session_complete)
    if type(_native_response_schema) is not bool:
        raise _services.UploadError("Contrato de resposta inválido; use um valor booleano.",
                          transient=False, phase="preflight")
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError
    if pb.device_mode_requires_physical():
        try:
            bound = pb.require_campaign_physical_binding()
            network_type, _network_meta = pb.require_physical_network(bound)
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(
                str(exc), transient=False, phase="complete") from exc
    elif not (isinstance(network_type, str) and network_type.strip()):
        network_type = "wifi"
    else:
        network_type = str(network_type).strip()
    body: dict[str, Any] = {"size_bytes": int(size_bytes)}
    # Helper Android 1.29.0 (decompiled L357749–357760).
    if suppress_per_chunk_catbear or session_complete:
        body["suppress_per_chunk_catbear"] = True
    if network_type:
        body["network_type"] = str(network_type)
    if session_complete:
        body["session_complete"] = True
    status, text, response_headers = _services._session_request(
        session, "PATCH", f"/api/v1/uploads/{upload_id}/complete", body)
    if status == 409:
        try:
            current = _services.get_upload(session, upload_id)
        except _services.UploadError as exc:
            # A failed/ambiguous GET cannot prove that the conflicting Complete
            # failed. Retry only a transient lookup; otherwise keep the receipt
            # for review, without PATCH /fail or repeating transport.
            message = "Confirmação conflitante sem consulta confiável; envio preservado para revisão."
            if isinstance(exc.blocked_reason, str) and exc.blocked_reason.casefold() == "app_check":
                message += " [bloqueio: app_check]"
            raise _services.UploadError(message, status_code=exc.status_code,
                              transient=exc.retryable, blocked_reason=exc.blocked_reason, phase="complete",
                              review_required=not exc.retryable) from None
        current_status = str(
            current.get("status") or current.get("upload_status") or ""
        ).casefold()
        if current_status in {"completed", "complete", "done"}:
            if _native_response_schema:
                _services._validate_native_upload_out(current, "complete")
            return current
    if status not in (200, 204):
        raise _services._http_upload_error(
            "PATCH /complete", status, text, response_headers)
    try:
        parsed = _services.decode_json_state(text) if text.strip() else {}
    except _services.JsonStateError:
        raise _services.UploadError("Resposta de conclusão do upload inválida; recibo preservado para revisão.",
                          transient=False, phase="complete", review_required=True) from None
    _services._validate_known_receipt(parsed, upload_id, "complete")
    if _native_response_schema:
        _services._validate_native_upload_out(parsed, "complete")
    return parsed
def _finalize_session(_services, 
    session: Any,
    org_key: str,
    session_id: str,
    expected_chunk_count: int,
) -> tuple[bool, int]:
    """POST /api/v1/organizations/{org}/sessions/{sid}/finalize.

    Marca a sessão como completa e elegível para catbear (avaliação de qualidade).
    Retorna (sucesso, status_http). Aceita confirmação HTTP 200 ou 204;
    HTTP 202 permanece pendente. Preserva recusas explícitas de App Check.
    """
    finalize_body = {"expected_chunk_count": expected_chunk_count}
    status, text, response_headers = _services._session_request(
        session,
        "POST",
        f"/api/v1/organizations/{org_key}/sessions/{session_id}/finalize",
        finalize_body,
    )
    blocked = _services._header(response_headers, "X-Blocked-Reason")
    if isinstance(blocked, str) and blocked.casefold() == "app_check":
        raise _services._http_upload_error("finalize", status, text, response_headers)
    return status in (200, 204), status

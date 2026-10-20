"""integrations routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('SecureStoreError', '_INTEGRATION_OPERATION_LOCK', '_PERSISTENCE_LOCK', '_apply_ego4d', '_apply_hostinger', '_hostinger_profiles', '_hostinger_routes', '_integration_error', '_integration_snapshot', '_legacy_aws_credentials', '_migrate_legacy_integrations', '_test_ego4d', 'config', 'ego4d', 'ego4d_catalog', 'hashlib', 'hostinger_mail', 'json', 'jsonify', 'request', 'save_secure_settings', 'uuid')


def get_integrations(_services):
    """Estados e dicas somente; nunca devolve um segredo salvo."""
    try:
        return _services.jsonify(_services._integration_snapshot())
    except _services.SecureStoreError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        return _services.jsonify({"error": _services._integration_error(exc, "configuração local")}), 400


def get_android_device(_services):
    """Delegate the public USB diagnostic route to its mobile domain."""
    from . import mobile

    return mobile.get_android_device(_services)


def get_appcheck_status(_services):
    """Delegate the read-only App Check status route to its mobile domain."""
    from . import mobile

    return mobile.get_appcheck_status(_services)


def recheck_appcheck_integration(_services):
    """Delegate the explicit App Check recheck to its mobile domain."""
    from . import mobile

    return mobile.recheck_appcheck_integration(_services)


def put_ego4d_integration(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict):
        return _services.jsonify({"error": "preencha as credenciais do Ego4D"}), 400
    secure = _services._migrate_legacy_integrations()
    current = (secure.get("ego4d")
               if isinstance(secure.get("ego4d"), dict)
               else _services._legacy_aws_credentials())
    values = {
        "access_key_id": str(body.get("access_key_id") or current.get("access_key_id") or "").strip(),
        "secret_access_key": str(body.get("secret_access_key") or current.get("secret_access_key") or "").strip(),
        "session_token": str(
            body.get("session_token") if "session_token" in body
            else current.get("session_token") or "").strip(),
        "region": str(
            body.get("region") if "region" in body
            else current.get("region") or "").strip(),
    }
    if len(values["access_key_id"]) < 12 or len(values["secret_access_key"]) < 20:
        return _services.jsonify({
            "error": "informe o Access Key ID e o Secret Access Key recebidos do Ego4D",
        }), 400
    try:
        tested = _services._test_ego4d(values)
    except Exception as exc:  # noqa: BLE001 — traduz resposta de boto/AWS
        return _services.jsonify({"error": _services._integration_error(exc, "AWS do Ego4D")}), 400
    with _services._PERSISTENCE_LOCK:
        secure["schema"] = 1
        secure["ego4d"] = values
        _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
        _services._apply_ego4d(values)
    return _services.jsonify({"ok": True, "test": tested,
                    "integrations": _services._integration_snapshot()})

def test_ego4d_integration(_services):
    secure = _services._migrate_legacy_integrations()
    current = (secure.get("ego4d")
               if isinstance(secure.get("ego4d"), dict)
               else _services._legacy_aws_credentials())
    body = _services.request.get_json(silent=True) or {}
    values = {
        "access_key_id": str(body.get("access_key_id") or current.get("access_key_id") or "").strip(),
        "secret_access_key": str(body.get("secret_access_key") or current.get("secret_access_key") or "").strip(),
        "session_token": str(body.get("session_token") or current.get("session_token") or "").strip(),
        "region": str(body.get("region") or current.get("region") or "").strip(),
    }
    if not values or not values.get("access_key_id") or not values.get("secret_access_key"):
        return _services.jsonify({"error": "configure as credenciais do Ego4D primeiro"}), 400
    try:
        tested = _services._test_ego4d(values)
    except Exception as exc:  # noqa: BLE001
        return _services.jsonify({"error": _services._integration_error(exc, "AWS do Ego4D")}), 400
    return _services.jsonify(tested)

def prepare_ego4d_catalog(_services):
    secure = _services._migrate_legacy_integrations()
    values = (secure.get("ego4d")
              if isinstance(secure.get("ego4d"), dict)
              else _services._legacy_aws_credentials())
    if not values or not values.get("access_key_id") or not values.get("secret_access_key"):
        return _services.jsonify({"error": "configure as credenciais do Ego4D primeiro"}), 400
    body = _services.request.get_json(silent=True) or {}
    if body.get("include_annotations") is True:
        key = (str(_services.config.ROOT), str(_services.config.LIBRARY_ROOT),
               _services.hashlib.sha256(_services.json.dumps(values, sort_keys=True).encode()).hexdigest())

        def prepare_complete(progress):
            with _services._INTEGRATION_OPERATION_LOCK:
                try:
                    from ...ego4d_annotations import sync_timed_annotations
                    _services._apply_ego4d(values)
                    meta, clips = _services.ego4d.sync_meta()
                    annotations = sync_timed_annotations(progress=progress)
                    return {"ok": True, "message": "Catálogo e anotações temporais Ego4D preparados.",
                            "metadata_bytes": meta.stat().st_size, "clips_bytes": clips.stat().st_size,
                            "annotation_videos": annotations["videos"], "annotation_events": annotations["events"]}, 200
                except Exception as exc:
                    return {"error": _services._integration_error(exc, "catálogo e anotações Ego4D")}, 400

        result, status = _services.ego4d_catalog.get(key, prepare_complete, scope="ego4d-catalog")
        return _services.jsonify(result), status
    _services._apply_ego4d(values)
    try:
        meta, clips = _services.ego4d.sync_meta()
    except Exception as exc:  # noqa: BLE001
        return _services.jsonify({"error": _services._integration_error(exc, "AWS do Ego4D")}), 400
    return _services.jsonify({
        "ok": True,
        "message": "Catálogo básico do Ego4D preparado.",
        "metadata_bytes": meta.stat().st_size,
        "clips_bytes": clips.stat().st_size,
    })

def put_hostinger_integration(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict):
        return _services.jsonify({"error": "informe o token da Hostinger"}), 400
    secure = _services._migrate_legacy_integrations()
    host = (secure.get("hostinger")
            if isinstance(secure.get("hostinger"), dict) else {})
    profiles = _services._hostinger_profiles(host)
    if body.get("auto_detect"):
        token = str(body.get("token") or "").strip()
        if len(token) < 16:
            return _services.jsonify({
                "error": "cole um token válido da API Mail da Hostinger",
            }), 400
        duplicates = [
            item["name"] for item in profiles
            if item["token"] == token and item.get("mailbox_id")
        ]
        if duplicates and all(item["routes"] for item in profiles if item["token"] == token):
            return _services.jsonify({
                "error": (
                    "esta API Hostinger já está conectada"
                    + (f" ({', '.join(duplicates)})" if duplicates else "")
                ),
                "code": "hostinger_already_connected",
            }), 409
        try:
            detected = _services.hostinger_mail.discover_mailboxes(token)
        except Exception as exc:  # noqa: BLE001
            return _services.jsonify({"error": _services._integration_error(exc, "Hostinger")}), 400
        detected_ids = {item["resource_id"] for item in detected}
        existing_by_mailbox = {
            item["mailbox_id"]: item
            for item in profiles if item.get("mailbox_id")
        }
        # Uma conexão antiga sem ID de caixa é substituída quando o mesmo
        # token é identificado. As demais APIs permanecem intactas.
        profiles = [
            item for item in profiles
            if item.get("mailbox_id") not in detected_ids
            and not (item["token"] == token and not item.get("mailbox_id"))
        ]
        imported = []
        for index, mailbox in enumerate(detected):
            previous = existing_by_mailbox.get(mailbox["resource_id"], {})
            address = mailbox.get("address") or ""
            domain = mailbox.get("domain") or ""
            item = {
                "id": previous.get("id") or _services.uuid.uuid4().hex,
                "name": address or f"Caixa Hostinger {index + 1}",
                "token": token,
                "mailbox_id": mailbox["resource_id"],
                "routes": [domain] if domain else [],
            }
            profiles.append(item)
            imported.append({
                "name": item["name"],
                "domain": domain,
            })
        with _services._PERSISTENCE_LOCK:
            secure["schema"] = 2
            secure["hostinger"] = {"profiles": profiles}
            _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
            _services._apply_hostinger(profiles)
        return _services.jsonify({
            "ok": True,
            "detected_count": len(imported),
            "detected": imported,
            "integrations": _services._integration_snapshot(),
        })
    profile_id = str(body.get("profile_id") or "").strip()
    current = next(
        (item for item in profiles if item["id"] == profile_id), {})
    # Compatibilidade com a tela antiga: quando havia apenas uma conexão,
    # um PUT sem id significava editar aquela conexão, não criar outra.
    if not current and not body.get("create") and len(profiles) == 1:
        current = profiles[0]
        profile_id = current["id"]
    values = {
        "id": profile_id or _services.uuid.uuid4().hex,
        "name": str(body.get("name") or current.get("name")
                    or f"Caixa {len(profiles) + 1}").strip(),
        "token": str(body.get("token") or current.get("token") or "").strip(),
        "mailbox_id": str(
            body.get("mailbox_id") if "mailbox_id" in body
            else current.get("mailbox_id") or "").strip(),
        "routes": _services._hostinger_routes(
            body.get("routes") if "routes" in body
            else current.get("routes") or []),
    }
    if len(values["token"]) < 16:
        return _services.jsonify({"error": "informe um token válido da API Mail da Hostinger"}), 400
    if any(item["token"] == values["token"] and item["id"] != values["id"]
           for item in profiles):
        return _services.jsonify({
            "error": "esta API Hostinger já está conectada",
            "code": "hostinger_already_connected",
        }), 409
    try:
        tested = _services.hostinger_mail.test_connection(
            token=values["token"], mailbox=values["mailbox_id"] or None)
    except Exception as exc:  # noqa: BLE001
        return _services.jsonify({"error": _services._integration_error(exc, "Hostinger")}), 400
    replaced = False
    for index, item in enumerate(profiles):
        if item["id"] == values["id"]:
            profiles[index] = values
            replaced = True
            break
    if not replaced:
        profiles.append(values)
    with _services._PERSISTENCE_LOCK:
        secure["schema"] = 2
        secure["hostinger"] = {"profiles": profiles}
        _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
        _services._apply_hostinger(profiles)
    return _services.jsonify({"ok": True, "profile_id": values["id"], "test": tested,
                    "integrations": _services._integration_snapshot()})

def delete_hostinger_integration(_services, profile_id: str):
    secure = _services._migrate_legacy_integrations()
    host = (secure.get("hostinger")
            if isinstance(secure.get("hostinger"), dict) else {})
    profiles = _services._hostinger_profiles(host)
    remaining = [item for item in profiles if item["id"] != profile_id]
    if len(remaining) == len(profiles):
        return _services.jsonify({"error": "conexão Hostinger não encontrada"}), 404
    with _services._PERSISTENCE_LOCK:
        secure["schema"] = 2
        secure["hostinger"] = {"profiles": remaining}
        _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
        _services._apply_hostinger(remaining)
    return _services.jsonify({"ok": True, "integrations": _services._integration_snapshot()})

def test_hostinger_integration(_services):
    secure = _services._migrate_legacy_integrations()
    host = (secure.get("hostinger")
            if isinstance(secure.get("hostinger"), dict) else {})
    profiles = _services._hostinger_profiles(host)
    body = _services.request.get_json(silent=True) or {}
    profile_id = str(body.get("profile_id") or "").strip()
    current = next(
        (item for item in profiles if item["id"] == profile_id), {})
    if not current and not body.get("create") and len(profiles) == 1:
        current = profiles[0]
    token = str(body.get("token") or current.get("token")
                or _services.config.HOSTINGER_MAIL_TOKEN or "").strip()
    mailbox = str(body.get("mailbox_id") or current.get("mailbox_id")
                  or _services.config.HOSTINGER_MAILBOX_ID or "").strip()
    if not token:
        return _services.jsonify({"error": "configure o token da Hostinger primeiro"}), 400
    try:
        return _services.jsonify(_services.hostinger_mail.test_connection(
            token=token, mailbox=mailbox or None))
    except Exception as exc:  # noqa: BLE001
        return _services.jsonify({"error": _services._integration_error(exc, "Hostinger")}), 400

"""Bounded, public Android diagnostics through the local ADB executable.

Only the USB inventory, four public properties, ``id`` and ``which su`` are
queried. Finding ``su`` does not execute it or grant any app a permission.
This module never reads app data, credentials, caches or tokens.
"""
from __future__ import annotations

import re
from typing import Any


_DEADLINE_SECONDS = 8.0
_PROPERTIES = (
    ("model", "ro.product.model"),
    ("manufacturer", "ro.product.manufacturer"),
    ("android_version", "ro.build.version.release"),
    ("sdk", "ro.build.version.sdk"),
)
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-\[\]]{0,255}\Z")
_UID = re.compile(r"(?:^|\s)uid=(\d+)(?:\D|$)")


def _text(value: str) -> str:
    """Keep display fields short and free of terminal control characters."""
    return " ".join("".join(char for char in value if char.isprintable()).split())[:200]


def _inventory(output: str, text: Any = _text) -> list[dict[str, str]]:
    devices = []
    seen: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in {"device", "unauthorized", "offline"}:
            continue
        serial, state = parts[:2]
        if serial in seen:
            continue
        seen.add(serial)
        model = next((token[6:] for token in parts[2:] if token.startswith("model:")), "")
        devices.append({"serial": serial, "state": state,
                        "model": text(model.replace("_", " "))})
    return devices


def _connection_failure(output: str) -> str | None:
    """Classify transport failures privately; raw ADB errors never leave here."""
    value = output.lower()
    if "unauthorized" in value:
        return "unauthorized"
    if "device offline" in value or "device is offline" in value:
        return "offline"
    if ("no devices/emulators found" in value or "no devices found" in value
            or "device disconnected" in value or re.search(r"device[^\n]*not found", value)):
        return "disconnected"
    if "more than one device" in value or "more than one emulator" in value:
        return "multiple"
    return None


_MESSAGES = {
    "adb_missing": "ADB não encontrado. Instale Android SDK Platform-Tools ou configure QMONEY_ADB_PATH.",
    "disconnected": "Nenhum aparelho disponível. Conecte o cabo USB e habilite a depuração USB no Android.",
    "unauthorized": "Autorize a depuração USB no diálogo do aparelho e atualize o diagnóstico.",
    "offline": "O aparelho está offline no ADB. Reconecte o cabo USB e atualize o diagnóstico.",
    "multiple": "Há mais de um aparelho disponível. Selecione o dispositivo para consultar.",
    "error": "Não foi possível consultar o ADB. Confira o cabo USB e a instalação do Platform-Tools e tente novamente.",
}


class _QueryFailure(Exception):
    def __init__(self, state: str, message: str):
        self.state = state
        self.message = message


def snapshot(
    serial: str | None = None,
    *,
    find_adb,
    subprocess_module,
    time_module,
    windows: bool,
    datetime_type,
    timezone,
    deadline_seconds: float = _DEADLINE_SECONDS,
    properties=_PROPERTIES,
    serial_pattern=_SERIAL,
    uid_pattern=_UID,
    messages=_MESSAGES,
    text=_text,
    inventory=None,
    connection_failure=None,
) -> dict:
    """Return a stable public snapshot, with a shared eight-second deadline.

    A requested serial must be present in the fresh inventory before any shell
    command. With no selection, only one authorized device is selected. The
    inventory records the beginning of this query; transport failures during
    collection are reflected in ``connection_state`` and ``collection``.
    """
    if inventory is None:
        inventory = lambda output: _inventory(output, text=text)
    if connection_failure is None:
        connection_failure = _connection_failure
    started = time_module.monotonic()
    deadline = started + deadline_seconds
    result = {
        "adb_available": False,
        "connection_state": "adb_missing",
        "devices": [],
        "selected_serial": None,
        "device": {key: "" for key, _ in properties},
        "root": {"state": "unknown", "message": "Root não verificado. Isso não informa permissões de aplicativos."},
        "collection": {
            "state": "unavailable", "message": messages["adb_missing"],
            "checked_at": datetime_type.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "elapsed_ms": 0,
        },
    }

    def finish(state: str, message: str, *, error: bool = False) -> dict:
        result["connection_state"] = state
        result["collection"].update(state="error" if error else "unavailable", message=message)
        result["collection"]["elapsed_ms"] = max(0, int((time_module.monotonic() - started) * 1000))
        return result

    def run(arguments: list[str], *, absent_ok: bool = False) -> Any:
        remaining = deadline - time_module.monotonic()
        if remaining <= 0:
            raise _QueryFailure("error", "Tempo limite do diagnóstico atingido. Confira a conexão USB e tente novamente.")
        options = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=remaining, shell=False)
        if windows:
            options["creationflags"] = getattr(subprocess_module, "CREATE_NO_WINDOW", 0x08000000)
        try:
            completed = subprocess_module.run([adb, *arguments], **options)
        except subprocess_module.TimeoutExpired:
            raise _QueryFailure("error", "Tempo limite do diagnóstico atingido. Confira a conexão USB e tente novamente.") from None
        except FileNotFoundError:
            result["adb_available"] = False
            raise _QueryFailure("adb_missing", messages["adb_missing"]) from None
        except OSError:
            raise _QueryFailure("error", messages["error"]) from None
        if completed.returncode:
            failure = connection_failure(f"{completed.stdout}\n{completed.stderr}")
            if failure:
                raise _QueryFailure(failure, messages[failure])
            if not absent_ok:
                raise _QueryFailure("error", messages["error"])
        return completed

    try:
        adb = find_adb()
        if adb is None:
            return finish("adb_missing", messages["adb_missing"])
        result["adb_available"] = True
        result["devices"] = inventory(run(["devices", "-l"]).stdout)
        inventory = result["devices"]
        if serial is not None:
            if not isinstance(serial, str) or not serial_pattern.fullmatch(serial):
                return finish("error", "Seleção de aparelho inválida. Atualize a lista e selecione um dispositivo conectado.", error=True)
            selected = next((device for device in inventory if device["serial"] == serial), None)
            if selected is None:
                result["selected_serial"] = serial
                return finish("disconnected", "O aparelho selecionado não está no inventário ADB. Reconecte-o ou selecione outro dispositivo.")
        else:
            connected = [device for device in inventory if device["state"] == "device"]
            if len(connected) > 1 or (not connected and len(inventory) > 1):
                return finish("multiple", messages["multiple"])
            selected = connected[0] if connected else inventory[0] if inventory else None
        if selected is None:
            return finish("disconnected", messages["disconnected"])
        result["selected_serial"] = selected["serial"]
        state = selected["state"]
        if state != "device":
            return finish(state, messages[state])
        if not serial_pattern.fullmatch(selected["serial"]):
            return finish("error", "Identificador ADB inválido. Atualize a lista de dispositivos.", error=True)
        prefix = ["-s", selected["serial"], "shell"]
        for field, prop in properties:
            result["device"][field] = text(run([*prefix, "getprop", prop]).stdout)
        identity = run([*prefix, "id"]).stdout.strip()
        uid = uid_pattern.search(identity)
        if uid and uid.group(1) == "0":
            result["root"] = {
                "state": "root_shell",
                "message": "O shell ADB informou uid=0. Isso não confirma permissões root de nenhum aplicativo.",
            }
        else:
            availability = run([*prefix, "which", "su"], absent_ok=True)
            su_available = availability.returncode == 0 and any(
                line.strip().startswith("/") and line.strip().endswith("/su")
                for line in availability.stdout.splitlines()
            )
            if su_available:
                result["root"] = {
                    "state": "su_available",
                    "message": "O executável su está disponível; ele não foi executado. Isso não confirma permissões root de aplicativos.",
                }
            elif uid and not availability.stderr.strip() and availability.returncode in (0, 1):
                result["root"] = {
                    "state": "not_detected",
                    "message": "A consulta pública não detectou shell root nem executável su. Isso não informa permissões de aplicativos.",
                }
        result["connection_state"] = "connected"
        result["collection"].update(state="done", message="Informações públicas do aparelho coletadas.")
        result["collection"]["elapsed_ms"] = max(0, int((time_module.monotonic() - started) * 1000))
        return result
    except _QueryFailure as failure:
        result["device"] = {key: "" for key, _ in properties}
        result["root"] = {"state": "unknown", "message": "Root não verificado. Isso não informa permissões de aplicativos."}
        return finish(failure.state, failure.message, error=True)
    except OSError:
        return finish("error", messages["error"], error=True)

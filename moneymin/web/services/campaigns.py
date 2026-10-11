"""campaigns services: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('MAX_DUR_S', '_parse_active_hours', 'math')


def _validate_campaign_request(_services, body: Any) -> None:
    """Use the same parameter checks for preview and execution."""
    if not isinstance(body, dict):
        raise ValueError("a campanha deve ser um objeto JSON")
    emails = body.get("accounts", [])
    if (not isinstance(emails, list) or any(not isinstance(e, str) or not e.strip() for e in emails)
            or len({e.strip() for e in emails}) != len(emails)):
        raise ValueError("selecione uma lista de contas válidas, sem duplicação")
    tasks = body.get("tasks", [])
    if not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks):
        raise ValueError("selecione uma lista de categorias válidas")
    for name in ("target_hours", "delay_s", "account_gap_s"):
        value = body.get(name, 0)
        if isinstance(value, bool) or not _services.math.isfinite(float(value or 0)):
            raise ValueError("parâmetro numérico inválido: " + name)
    if isinstance(body.get("count", 1), bool):
        raise ValueError("quantidade de vídeos inválida")
    if body.get("delay_mode", "off") not in {"off", "clip", "fixed"}:
        raise ValueError("delay_mode inválido (off|clip|fixed)")
    if not isinstance(body.get("cleanup_after_upload", True), bool):
        raise ValueError("cleanup_after_upload deve ser true ou false")
    if not isinstance(body.get("run_until_exhausted", True), bool):
        raise ValueError("run_until_exhausted deve ser true ou false")
    if _services._parse_active_hours(body.get("active_hours")) is False:
        raise ValueError("active_hours inválido — use [início, fim] com 0 <= início < fim <= 24")

def _parse_duration_range(_services, values) -> tuple[float, float]:
    """Valida o intervalo de duração solicitado pela interface."""
    bounds = []
    for name, default in (("min_dur_s", 60), ("max_dur_s", _services.MAX_DUR_S)):
        try:
            value = float(values.get(name, default))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} inválido") from exc
        if not _services.math.isfinite(value) or not 60 <= value <= _services.MAX_DUR_S:
            raise ValueError(f"{name} deve estar entre 60 e 1800 segundos")
        bounds.append(value)
    minimum, maximum = bounds
    if minimum > maximum:
        raise ValueError("min_dur_s não pode exceder max_dur_s")
    return minimum, maximum

def _parse_active_hours(_services, raw) -> tuple[int, int] | None | bool:
    """[7, 18] -> (7, 18); None/ausente -> None (sem janela); inválido -> False."""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2 or any(type(v) is not int for v in raw):
        return False
    start, end = raw
    if not (0 <= start < end <= 24):
        return False
    return (start, end)

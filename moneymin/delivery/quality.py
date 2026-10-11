"""Upload evaluation and perfect-only acceptance policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class QualityServices:
    upload_error: type[Exception]
    json_state_error: type[Exception]
    decode_json_state: Callable[[Any], Any]
    with_retry: Callable[..., Any]
    evaluate_upload: Callable[..., dict[str, Any]]
    summarize_checks: Callable[[dict[str, Any]], dict[str, Any]]
    is_perfect: Callable[[dict[str, Any]], bool]
    fail_upload: Callable[..., dict[str, Any]]
    delete_upload: Callable[..., bool]
    delete_session: Callable[..., bool]
    session_request: Callable[..., tuple[int, str, dict[str, str]]]
    http_upload_error: Callable[..., Exception]


def summarize_checks(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Resume status counts and failures from an EvaluationResult."""
    checks = evaluation.get("checks") if isinstance(evaluation, dict) else None
    valid = (isinstance(checks, list) and bool(checks)
             and all(isinstance(c, dict) and c.get("status") in ("pass", "fail", "skip")
                     for c in checks))
    if not valid:
        return {"counts": {"pass": 0, "fail": 0, "skip": 0},
                "failures": [], "valid": False}
    counts = {"pass": 0, "fail": 0, "skip": 0}
    failures: list[dict[str, Any]] = []
    for check in checks:
        status = check.get("status")
        counts[status] = counts.get(status, 0) + 1
        if status == "fail":
            failures.append(check)
    return {"counts": counts, "failures": failures, "valid": True}


def is_perfect(evaluation: dict[str, Any], *, summarize: Callable[..., Any] = summarize_checks) -> bool:
    """Checks marked skip are accepted; any failed check rejects the upload."""
    summary = summarize(evaluation)
    return summary["valid"] and summary["counts"].get("fail", 0) == 0


def evaluate_upload(
    session: Any, upload_id: str, services: QualityServices,
) -> dict[str, Any]:
    """Evaluate an existing receipt; quality outages never create a new upload."""
    def evaluation_error(status: int, headers: dict[str, str]) -> Exception:
        # Evaluation responses previously stayed private; preserve that rule
        # while retaining explicit policy headers for retry classification.
        return services.http_upload_error(
            "evaluate", status, f"Avaliação não concluída (HTTP {status}).", headers)

    def request_evaluation() -> tuple[int, str, dict[str, str]]:
        status, text, headers = services.session_request(
            session, "POST", f"/api/v1/uploads/{upload_id}/evaluate")
        if status == -1 or status == 408 or 500 <= status <= 599:
            raise evaluation_error(status, headers)
        return status, text, headers

    (status, text, headers), _attempts = services.with_retry(
        request_evaluation, max_retries=3, retry_delay=2.0,
        retry_backoff=2.0, label="evaluate")
    if status != 200:
        raise evaluation_error(status, headers)
    try:
        evaluation = services.decode_json_state(text)
    except services.json_state_error:
        raise services.upload_error("Avaliação devolveu uma resposta inválida.",
                                    transient=False, phase="evaluate") from None
    if (not isinstance(evaluation, dict) or evaluation.get("upload_id") != upload_id
            or not services.summarize_checks(evaluation)["valid"]
            or any(not isinstance(check.get("id"), str)
                   or not isinstance(check.get("label"), str)
                   or "detail" not in check
                   or (check["detail"] is not None and not isinstance(check["detail"], str))
                   for check in evaluation.get("checks", []))):
        raise services.upload_error(
            "Avaliação incompleta ou referente a outro vídeo; precisa de revisão.",
            transient=False, phase="evaluate")
    return evaluation


def require_perfect_upload(
    session: Any, upload_id: str, org_key: str, session_id: str,
    services: QualityServices, *, fail_on_error: bool = True, delete_on_fail: bool = True,
) -> dict[str, Any]:
    """Apply perfect-only policy and preserve existing best-effort cleanup behavior."""
    evaluation = services.evaluate_upload(session, upload_id)
    summary = services.summarize_checks(evaluation)
    perfect = services.is_perfect(evaluation)
    result: dict[str, Any] = {
        "perfect": perfect,
        "evaluation": evaluation,
        "failed_ids": [check.get("id") for check in summary["failures"]],
        "deleted": False,
        "deleted_session": False,
    }
    if perfect:
        return result
    error_msg = "reprovado no evaluate: " + ", ".join(
        check.get("id", "?") for check in summary["failures"])
    if fail_on_error:
        try:
            services.fail_upload(session, upload_id, error_msg)
        except services.upload_error:
            pass
    if delete_on_fail:
        try:
            result["deleted"] = services.delete_upload(session, upload_id)
        except services.upload_error:
            result["deleted"] = False
        try:
            result["deleted_session"] = services.delete_session(session, org_key, session_id)
        except services.upload_error:
            result["deleted_session"] = False
    return result


__all__ = ["QualityServices", "evaluate_upload", "is_perfect", "require_perfect_upload", "summarize_checks"]

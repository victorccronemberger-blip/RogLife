"""A reviewed local interruption must never imply remote delivery confirmation."""
from moneymin.web.runner import _public_event


def test_local_interruption_notice_is_static_and_does_not_claim_remote_success():
    event = _public_event("campaign_interrupted_local", {
        "error": "PRIVATE-DIAGNOSTIC", "password": "PRIVATE-PASSWORD",
        "delivery_confirmed": True, "ok_sends": 99,
    })
    assert event["level"] == "warning"
    assert "resultado remoto não foi confirmado" in event["detail"]
    assert "nenhum envio foi repetido automaticamente" in event["detail"]
    assert "PRIVATE" not in str(event)
    assert "99" not in str(event)

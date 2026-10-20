"""Local App Check JWT shape/expiry inspection; this does not verify signatures."""
from __future__ import annotations
import json
import math

def bearer_left_s(token: str, *, clock) -> float | None:
    """Remaining seconds of a compact JWT exp; None when shape/exp is unusable."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    if any(ord(c) <= 32 or ord(c) >= 127 for c in token) or len(token) > 16384:
        return None
    try:
        import base64
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if type(exp) not in (int, float):
        return None
    try:
        expiry = float(exp)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(expiry):
        return None
    return expiry - clock()


def bearer_still_valid(token: str, *, clock) -> bool:
    """Accept only compact JWTs with a future exp; signature is server-verified."""
    left = bearer_left_s(token, clock=clock)
    return left is not None and left > 30

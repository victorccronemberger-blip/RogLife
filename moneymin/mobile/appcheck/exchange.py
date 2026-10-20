"""Administrator-authorized Firebase debug-token exchange; transport is injected."""
from __future__ import annotations
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from .errors import AppCheckExchangeError

def exchange(project, app, api_key, secret, *, urlopen, diagnostic):
    path = '/'.join(urllib.parse.quote(part, safe='') for part in (project, app))
    project_path, app_path = path.split('/')
    url = (f"https://firebaseappcheck.googleapis.com/v1/projects/{project_path}"
           f"/apps/{app_path}:exchangeDebugToken?key={urllib.parse.quote(api_key, safe='')}")
    req = urllib.request.Request(url, method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"debugToken": secret}).encode())
    try:
        with urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise ValueError("exchange failed")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized response")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise AppCheckExchangeError(**diagnostic(exc)) from None
    token, ttl = data.get("token"), data.get("ttl")
    if (not isinstance(token, str) or not token or len(token) > 16384
            or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
            or not isinstance(ttl, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,9})?s", ttl)):
        raise ValueError("invalid token response")
    seconds = float(ttl[:-1])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid lifetime")
    return token, seconds

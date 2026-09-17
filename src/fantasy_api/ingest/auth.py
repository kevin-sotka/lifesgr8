"""
Yahoo OAuth 2.0: one-time authorization, then silent refresh.

Access tokens last an hour. The refresh token persists, and Yahoo may hand back
a new one on every refresh, so it is written back to storage each time.

A note on POST: exchanging a code for a token is a POST to Yahoo's login
service, because that is how OAuth works. It is the only POST in the project, it
can only reach the fixed token URL below, and it carries no fantasy data. The
Fantasy Sports client in client.py has no way to send anything but GET.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from ..config import TOKEN_FILE, Settings

AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# Refresh a little before Yahoo's one hour expiry rather than on the boundary.
EXPIRY_MARGIN_SECONDS = 120


class AuthError(Exception):
    pass


def authorize_url(settings: Settings) -> str:
    query = urllib.parse.urlencode({
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "language": "en-us",
    })
    return "%s?%s" % (AUTHORIZE_URL, query)


def _token_request(settings: Settings, form: Dict[str, str]) -> Dict[str, Any]:
    basic = base64.b64encode(
        ("%s:%s" % (settings.client_id, settings.client_secret)).encode()).decode()
    body = urllib.parse.urlencode(dict(form, redirect_uri=settings.redirect_uri)).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # Yahoo's error body names the problem and never echoes the secret back.
        detail = exc.read().decode(errors="ignore")[:300]
        raise AuthError("Yahoo rejected the token request (%s): %s" % (exc.code, detail))
    if "access_token" not in payload:
        raise AuthError("Token response had no access token.")
    return payload


def _save(payload: Dict[str, Any], previous_refresh: str = "") -> Dict[str, Any]:
    record = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or previous_refresh,
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        "guid": payload.get("xoauth_yahoo_guid"),
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    # Owner read/write only, created that way rather than chmod'ed afterwards.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(record, fh)
    os.replace(tmp, TOKEN_FILE)
    return record


def exchange_code(settings: Settings, code: str) -> None:
    payload = _token_request(settings, {"grant_type": "authorization_code", "code": code.strip()})
    _save(payload)


def load_tokens() -> Dict[str, Any]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    # The scheduled GitHub Actions build has no token file, only a repository
    # secret. Seeding from it with an already-expired access token forces a
    # refresh on first use, and the refreshed pair lands in the runner's
    # throwaway data/ directory.
    seeded = os.environ.get("YAHOO_REFRESH_TOKEN", "").strip()
    if seeded:
        return {"access_token": "", "refresh_token": seeded, "expires_at": 0}
    raise AuthError("Not authorized yet. Run: ./fantasy-api auth")


def refresh(settings: Settings) -> Dict[str, Any]:
    current = load_tokens()
    if not current.get("refresh_token"):
        raise AuthError("No refresh token stored. Run: ./fantasy-api auth")
    payload = _token_request(settings, {
        "grant_type": "refresh_token", "refresh_token": current["refresh_token"]})
    return _save(payload, previous_refresh=current["refresh_token"])


def access_token(settings: Settings, force_refresh: bool = False) -> str:
    tokens = load_tokens()
    if force_refresh or time.time() >= tokens.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS:
        tokens = refresh(settings)
    return tokens["access_token"]

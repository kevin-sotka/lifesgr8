"""
The Yahoo Fantasy Sports client. It reads. That is all it can do.

There is no post(), put(), or delete() here, and there is no generic request()
that takes a method, so there is nothing to call by accident, behind a flag, or
because a feature would be easier with it. tests/test_readonly.py fails the
build if that ever changes.

Every response is written to data/raw/ as it arrived, before it is parsed.
Yahoo changes response shapes without notice, and data that only survives in
parsed form is data that eventually gets lost.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..config import RAW, Settings
from . import auth

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2/"

log = logging.getLogger(__name__)


class YahooError(Exception):
    pass


class PermissionProblem(YahooError):
    """The token is valid but the app is not allowed to read Fantasy Sports."""


class OutOfScope(YahooError):
    """A request named a league other than Life's Gr8, or asked for account-wide data."""


LEAGUE_KEY = re.compile(r"\b\d+\.l\.\d+\b")


def _snapshot_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._=-]+", "_", path).strip("_")[:160] or "root"


class YahooClient:
    MAX_ATTEMPTS = 4

    def __init__(self, settings: Settings, raw_dir: Path = RAW,
                 token_provider: Optional[Callable[..., str]] = None,
                 opener: Optional[Callable[..., Any]] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 allowed_leagues: Iterable[str] = (),
                 allow_discovery: bool = False) -> None:
        self._settings = settings
        # This account belongs to several leagues. Only Life's Gr8 is ever read:
        # any path naming a league key must name one on this list, and the
        # account-wide users; resource is refused unless discovery was explicitly
        # asked for.
        self._allowed = set(allowed_leagues)
        self.request_count = 0
        self._allow_discovery = allow_discovery
        self._raw_dir = raw_dir
        self._token = token_provider or (lambda force=False: auth.access_token(settings, force))
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    def get(self, path: str, snapshot_as: Optional[str] = None) -> Any:
        """GET a Fantasy Sports resource, snapshot the raw body, return parsed JSON."""
        path = path.lstrip("/")
        self._check_scope(path)
        sep = "&" if "?" in path else "?"
        url = "%s%s%sformat=json" % (BASE_URL, path, sep)

        refreshed = False
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            req = urllib.request.Request(url, method="GET", headers={
                "Authorization": "Bearer " + self._token(refreshed),
                "Accept": "application/json",
            })
            self.request_count += 1
            try:
                with self._open(req, timeout=45) as resp:
                    body = resp.read()
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="ignore")[:400]
                if exc.code == 401 and not refreshed:
                    log.info("access token rejected, refreshing once")
                    refreshed = True
                    continue
                if exc.code in (401, 403):
                    raise PermissionProblem(
                        "Yahoo returned %s for %s. If authorization succeeded, the app "
                        "almost certainly lacks the Fantasy Sports permission. %s"
                        % (exc.code, path, detail))
                if exc.code == 429 or exc.code >= 500:
                    if attempt == self.MAX_ATTEMPTS:
                        raise YahooError("Yahoo kept failing (%s) for %s" % (exc.code, path))
                    wait = min(60.0, 2.0 ** attempt)
                    log.warning("Yahoo %s on %s, retrying in %.0fs", exc.code, path, wait)
                    self._sleep(wait)
                    continue
                raise YahooError("Yahoo returned %s for %s: %s" % (exc.code, path, detail))
            except urllib.error.URLError as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise YahooError("Could not reach Yahoo: %s" % exc.reason)
                self._sleep(min(60.0, 2.0 ** attempt))
        else:  # pragma: no cover - loop always breaks or raises
            raise YahooError("Exhausted retries for %s" % path)

        self._write_raw(snapshot_as or path, body)
        return json.loads(body.decode())

    def allow_league(self, league_key: str) -> None:
        self._allowed.add(league_key)

    def _check_scope(self, path: str) -> None:
        if path.startswith("users") and not self._allow_discovery:
            raise OutOfScope("Refusing account-wide request %r. It returns every league "
                             "on the account, not just Life's Gr8." % path)
        for key in LEAGUE_KEY.findall(path):
            if key not in self._allowed:
                raise OutOfScope("Refusing %r: league %s is not Life's Gr8." % (path, key))
        if (path.startswith("league/") or path.startswith("team/")) and not LEAGUE_KEY.search(path):
            raise OutOfScope("Refusing %r: no league key to check it against." % path)

    def _write_raw(self, resource: str, body: bytes) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = self._raw_dir / _snapshot_name(resource)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("%s.json" % stamp)
        target.write_bytes(body)
        return target

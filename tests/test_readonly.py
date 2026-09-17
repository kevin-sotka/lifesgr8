"""
Guards for CLAUDE.md non-negotiables 1, 3, and 8.

Run with: python3 -m unittest discover -s tests
"""

import ast
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fantasy_api.config import Settings  # noqa: E402
from fantasy_api.ingest.client import PermissionProblem, YahooClient  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "fantasy_api"
SETTINGS = Settings("id-xyz", "secret-shh", "oob", "123")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ReadOnly(unittest.TestCase):
    def test_client_has_no_write_methods(self):
        for name in ("post", "put", "patch", "delete", "request", "send"):
            self.assertFalse(hasattr(YahooClient, name), "YahooClient.%s must not exist" % name)

    def test_only_the_oauth_token_call_uses_post(self):
        offenders = []
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "method":
                    value = getattr(node.value, "value", None)
                    if value != "GET" and path.name != "auth.py":
                        offenders.append("%s: method=%r" % (path.name, value))
        self.assertEqual(offenders, [])

    def test_auth_post_can_only_reach_the_token_url(self):
        text = (SRC / "ingest" / "auth.py").read_text()
        self.assertEqual(text.count('method="POST"'), 1)
        self.assertNotIn("fantasysports.yahooapis.com", text)

    def test_every_request_the_client_sends_is_a_get(self):
        seen = []

        def opener(req, timeout):
            seen.append(req.get_method())
            return FakeResponse(b'{"fantasy_content": {}}')

        with tempfile.TemporaryDirectory() as tmp:
            client = YahooClient(SETTINGS, raw_dir=Path(tmp), allowed_leagues={"461.l.1"},
                                 token_provider=lambda force=False: "tok", opener=opener)
            client.get("league/461.l.1/standings")
            client.get("league/461.l.1/transactions;types=waiver;team_key=461.l.1.t.1")
        self.assertEqual(seen, ["GET", "GET"])


class Snapshots(unittest.TestCase):
    def test_raw_body_is_written_before_parsing(self):
        body = b'{"fantasy_content": {"league": "not parsed yet"}}'
        with tempfile.TemporaryDirectory() as tmp:
            client = YahooClient(SETTINGS, raw_dir=Path(tmp), allowed_leagues={"461.l.1"},
                                 token_provider=lambda force=False: "tok",
                                 opener=lambda req, timeout: FakeResponse(body))
            client.get("league/461.l.1/standings")
            files = list(Path(tmp).rglob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), body)

    def test_unparseable_body_is_still_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = YahooClient(SETTINGS, raw_dir=Path(tmp), allowed_leagues={"461.l.1"},
                                 token_provider=lambda force=False: "tok",
                                 opener=lambda req, timeout: FakeResponse(b"<html>oops"))
            with self.assertRaises(json.JSONDecodeError):
                client.get("league/461.l.1/standings")
            self.assertEqual(len(list(Path(tmp).rglob("*.json"))), 1)


class Failures(unittest.TestCase):
    def _http_error(self, code):
        return urllib.error.HTTPError("u", code, "no", {}, io.BytesIO(b'{"error":"denied"}'))

    def test_expired_token_refreshes_once_then_succeeds(self):
        calls, forced = [], []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise self._http_error(401)
            return FakeResponse(b"{}")

        def token(force=False):
            forced.append(force)
            return "tok"

        with tempfile.TemporaryDirectory() as tmp:
            YahooClient(SETTINGS, raw_dir=Path(tmp), token_provider=token,
                        opener=opener).get("game/nfl")
        self.assertEqual(forced, [False, True])

    def test_forbidden_is_reported_as_a_permission_problem(self):
        def opener(req, timeout):
            raise self._http_error(403)

        with tempfile.TemporaryDirectory() as tmp:
            client = YahooClient(SETTINGS, raw_dir=Path(tmp),
                                 token_provider=lambda force=False: "tok", opener=opener)
            with self.assertRaises(PermissionProblem):
                client.get("game/nfl")

    def test_rate_limit_backs_off_and_retries(self):
        calls, sleeps = [], []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise self._http_error(429)
            return FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as tmp:
            YahooClient(SETTINGS, raw_dir=Path(tmp), token_provider=lambda force=False: "tok",
                        opener=opener, sleep=sleeps.append).get("game/nfl")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_a_response_cut_off_mid_body_is_retried_and_not_snapshotted(self):
        import http.client
        calls, sleeps = [], []

        class Truncated(FakeResponse):
            def read(self, *a):
                raise http.client.IncompleteRead(b'{"fantasy_content": {"lea')

        def opener(req, timeout):
            calls.append(1)
            return Truncated(b"") if len(calls) == 1 else FakeResponse(b'{"ok": 1}')

        with tempfile.TemporaryDirectory() as tmp:
            data = YahooClient(SETTINGS, raw_dir=Path(tmp), token_provider=lambda force=False: "tok",
                               opener=opener, sleep=sleeps.append).get("game/nfl")
            files = list(Path(tmp).rglob("*.json"))
            self.assertEqual(data, {"ok": 1})
            self.assertEqual(len(files), 1)                  # only the complete body
            self.assertEqual(files[0].read_bytes(), b'{"ok": 1}')
        self.assertEqual((len(calls), sleeps), (2, [2.0]))

    def test_settings_repr_hides_secrets(self):
        self.assertNotIn("secret-shh", repr(SETTINGS))
        self.assertNotIn("id-xyz", repr(SETTINGS))


class Discovery(unittest.TestCase):
    def test_finds_leagues_in_yahoos_numbered_collections(self):
        from fantasy_api.ingest import fetch
        raw = {"fantasy_content": {"users": {"0": {"user": [
            {"guid": "G"},
            {"games": {"0": {"game": [
                {"game_key": "461", "season": "2026", "code": "nfl"},
                {"leagues": {"0": {"league": [{
                    "league_key": "461.l.884422", "league_id": "884422",
                    "name": "Life's Gr8", "num_teams": 16, "scoring_type": "head",
                    "season": "2026"}]}, "count": 1}}]}, "count": 1}}]}, "count": 1}}}

        class Stub:
            def get(self, path, snapshot_as=None):
                return raw

        leagues = fetch.my_nfl_leagues(Stub())
        self.assertEqual(leagues[0]["league_key"], "461.l.884422")
        self.assertEqual(leagues[0]["name"], "Life's Gr8")
        self.assertEqual(leagues[0]["game_key"], "461")

    def test_roster_player_keys_and_owned_team(self):
        from fantasy_api import cli
        roster = {"fantasy_content": {"team": [[{"team_key": "461.l.1.t.3"}], {"roster": {
            "0": {"players": {
                "0": {"player": [[{"player_key": "461.p.100"}, {"name": {"full": "A"}}],
                                 {"selected_position": [{"position": "LB"}]}]},
                "1": {"player": [[{"player_key": "461.p.200"}],
                                 {"selected_position": [{"position": "BN"}]}]},
                "count": 2}}}}]}}
        self.assertEqual(cli._player_keys(roster), ["461.p.100", "461.p.200"])

        teams = {"fantasy_content": {"league": [{"league_key": "461.l.1"}, {"teams": {
            "0": {"team": [[{"team_key": "461.l.1.t.1"}, {"name": "Other"}]]},
            "1": {"team": [[{"team_key": "461.l.1.t.2"}, {"is_owned_by_current_login": 1}]]},
            "count": 2}}]}}
        self.assertEqual(cli._my_team_key(teams), "461.l.1.t.2")


class TokenBootstrap(unittest.TestCase):
    def test_ci_seeds_from_refresh_token_secret(self):
        import os
        from unittest import mock
        from fantasy_api.ingest import auth
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(auth, "TOKEN_FILE", Path(tmp) / "none.json"), \
                mock.patch.dict(os.environ, {"YAHOO_REFRESH_TOKEN": "rt-123"}):
            tokens = auth.load_tokens()
        self.assertEqual(tokens["refresh_token"], "rt-123")
        self.assertEqual(tokens["expires_at"], 0)

    def test_no_file_and_no_secret_asks_for_auth(self):
        import os
        from unittest import mock
        from fantasy_api.ingest import auth
        env = {k: v for k, v in os.environ.items() if k != "YAHOO_REFRESH_TOKEN"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(auth, "TOKEN_FILE", Path(tmp) / "none.json"), \
                mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(auth.AuthError):
                auth.load_tokens()


class LeagueScope(unittest.TestCase):
    def _client(self, tmp, sent, **kw):
        def opener(req, timeout):
            sent.append(req.full_url)
            return FakeResponse(b"{}")
        return YahooClient(SETTINGS, raw_dir=Path(tmp), opener=opener,
                           token_provider=lambda force=False: "tok", **kw)

    def test_other_leagues_are_refused_before_any_request(self):
        from fantasy_api.ingest.client import OutOfScope
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp, sent, allowed_leagues={"470.l.33009"})
            c.get("league/470.l.33009/standings")
            c.get("team/470.l.33009.t.14/roster;week=2")
            for bad in ("league/470.l.676709/standings", "team/470.l.676709.t.1/roster"):
                with self.assertRaises(OutOfScope):
                    c.get(bad)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all("33009" in u for u in sent))

    def test_account_wide_discovery_needs_explicit_opt_in(self):
        from fantasy_api.ingest.client import OutOfScope
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OutOfScope):
                self._client(tmp, sent).get("users;use_login=1/games;game_codes=nfl/leagues")
            self._client(tmp, sent, allow_discovery=True).get("users;use_login=1/games")
        self.assertEqual(len(sent), 1)

    def test_league_path_without_a_key_is_refused(self):
        from fantasy_api.ingest.client import OutOfScope
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OutOfScope):
                self._client(tmp, []).get("league/whatever/standings")

    def test_season_chain_only_follows_renew_pointers(self):
        from fantasy_api.ingest import fetch
        leagues = {
            "470.l.33009": {"name": "Lifes Gr8", "season": "2026", "renew": "461_87487"},
            "461.l.87487": {"name": "Lifes Gr8", "season": "2025", "renew": ""},
        }
        sent = []

        def opener(req, timeout):
            key = LEAGUE_RE.search(req.full_url).group(0)
            sent.append(key)
            meta = dict(leagues[key], league_key=key, num_teams=16)
            return FakeResponse(json.dumps({"fantasy_content": {"league": [meta]}}).encode())

        with tempfile.TemporaryDirectory() as tmp:
            c = YahooClient(SETTINGS, raw_dir=Path(tmp), opener=opener,
                            token_provider=lambda force=False: "tok", allowed_leagues={"470.l.33009"})
            chain = fetch.season_chain(c, "470.l.33009")
        self.assertEqual([l["season"] for l in chain], ["2026", "2025"])
        self.assertEqual(sent, ["470.l.33009", "461.l.87487"])


import re  # noqa: E402
LEAGUE_RE = re.compile(r"\d+\.l\.\d+")


if __name__ == "__main__":
    unittest.main()

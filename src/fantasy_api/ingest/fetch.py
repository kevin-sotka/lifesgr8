"""
Raw fetchers, one per resource in CLAUDE.md's endpoint table.

Each returns Yahoo's JSON untouched. Interpretation happens in snapshot/, which
does not exist yet: it gets written against real snapshots rather than guessed
shapes, which is the whole reason these land on disk first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..yahoo_shapes import items
from .client import YahooClient


def _collection(node: Any, name: str) -> List[Dict[str, Any]]:
    """
    Pull the entries out of one of Yahoo's numbered collections.

    Yahoo encodes lists as {"0": {...}, "1": {...}, "count": 2}. This is the only
    shape knowledge ingest carries, and it exists solely so discovery can find a
    league key without the snapshot layer.
    """
    out: List[Dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_collection(item, name))
        return out
    if not isinstance(node, dict):
        return out
    if name in node and isinstance(node[name], dict):
        block = node[name]
        for key, value in block.items():
            if key.isdigit():
                out.append(value)
        return out
    for value in node.values():
        out.extend(_collection(value, name))
    return out


def _flatten(entry: Any) -> Dict[str, Any]:
    """Yahoo wraps an object's fields in a list of single-key dicts."""
    flat: Dict[str, Any] = {}
    stack = [entry]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, (list, dict)) and key in ("game", "league", "team"):
                    stack.append(value)
                elif not isinstance(value, (list, dict)):
                    flat.setdefault(key, value)
    return flat


# --- discovery ----------------------------------------------------------------

def my_nfl_leagues(client: YahooClient) -> List[Dict[str, Any]]:
    """Every NFL league the authorized account belongs to, newest season first."""
    raw = client.get("users;use_login=1/games;game_codes=nfl/leagues",
                     snapshot_as="discovery/my_nfl_leagues")
    leagues = []
    for game in _collection(raw, "games"):
        g = _flatten(game.get("game", game))
        for league in _collection(game, "leagues"):
            lg = _flatten(league.get("league", league))
            leagues.append({
                "season": lg.get("season") or g.get("season"),
                "game_key": g.get("game_key"),
                "league_key": lg.get("league_key"),
                "league_id": lg.get("league_id"),
                "name": lg.get("name"),
                "num_teams": lg.get("num_teams"),
                "scoring_type": lg.get("scoring_type"),
            })
    leagues.sort(key=lambda l: str(l.get("season")), reverse=True)
    return leagues


def current_game_key(client: YahooClient) -> str:
    """Game keys change every season, so resolve rather than hardcode."""
    raw = client.get("game/nfl", snapshot_as="discovery/game_nfl")
    game = _flatten(raw.get("fantasy_content", {}).get("game", {}))
    if not game.get("game_key"):
        raise ValueError("Yahoo did not return a current NFL game key")
    return str(game["game_key"])


def league_meta(client: YahooClient, league_key: str) -> Dict[str, Any]:
    raw = client.get("league/%s" % league_key)
    return _flatten(raw.get("fantasy_content", {}).get("league", {}))


def season_chain(client: YahooClient, league_key: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Life's Gr8 and its own prior seasons, found by following the league's renew
    pointer backwards. Only leagues reached through that pointer are ever
    requested, so no other league on the account gets touched.
    """
    chain = []
    key: Optional[str] = league_key
    while key and len(chain) < limit:
        client.allow_league(key)
        meta = league_meta(client, key)
        chain.append({"season": meta.get("season"), "league_key": key,
                      "name": meta.get("name"), "num_teams": meta.get("num_teams")})
        renew = str(meta.get("renew") or "")
        key = renew.replace("_", ".l.", 1) if "_" in renew else None
    return chain


# --- league resources ---------------------------------------------------------

def settings(client: YahooClient, league_key: str) -> Any:
    return client.get("league/%s/settings" % league_key)


def draft_results(client: YahooClient, league_key: str) -> Any:
    return client.get("league/%s/draftresults" % league_key)


def standings(client: YahooClient, league_key: str) -> Any:
    return client.get("league/%s/standings" % league_key)


def teams(client: YahooClient, league_key: str) -> Any:
    return client.get("league/%s/teams" % league_key)


def transactions(client: YahooClient, league_key: str) -> Any:
    return client.get("league/%s/transactions" % league_key)


def pending_transactions(client: YahooClient, league_key: str, team_key: str) -> Dict[str, Any]:
    """
    Pending waiver claims and pending trades.

    These never appear in a plain transactions fetch, and Yahoo only returns them
    for a team the authorized account manages. In practice that means the Blotter
    can show pending activity for Kevin's own team, not for the whole league.
    """
    return {
        "waiver": client.get("league/%s/transactions;types=waiver;team_key=%s"
                             % (league_key, team_key)),
        "pending_trade": client.get("league/%s/transactions;types=pending_trade;team_key=%s"
                                    % (league_key, team_key)),
    }


def scoreboard(client: YahooClient, league_key: str, week: int) -> Any:
    return client.get("league/%s/scoreboard;week=%d" % (league_key, week))


def roster(client: YahooClient, team_key: str, week: int) -> Any:
    return client.get("team/%s/roster;week=%d" % (team_key, week))


def player_week_stats(client: YahooClient, league_key: str,
                      player_keys: Sequence[str], week: int, batch: int = 25) -> List[Any]:
    """Batched, because Yahoo's rate limits are undocumented and low."""
    out = []
    keys = list(player_keys)
    for i in range(0, len(keys), batch):
        chunk = ",".join(keys[i:i + batch])
        out.append(client.get(
            "league/%s/players;player_keys=%s/stats;type=week;week=%d"
            % (league_key, chunk, week),
            snapshot_as="league/%s/player_stats/week_%d" % (league_key, week)))
    return out


def player_season_stats(client: YahooClient, league_key: str,
                        player_keys: Sequence[str], batch: int = 25) -> List[Any]:
    """Full-season totals in that season's league context, so points use that year's scoring."""
    out = []
    keys = list(player_keys)
    for i in range(0, len(keys), batch):
        out.append(client.get(
            "league/%s/players;player_keys=%s/stats;type=season"
            % (league_key, ",".join(keys[i:i + batch])),
            snapshot_as="league/%s/player_stats/season" % league_key))
    return out


def team_keys(teams_raw: Any) -> List[str]:
    return [t for t in (_flatten(e.get("team", e)).get("team_key")
                        for e in _collection(teams_raw, "teams")) if t]


def current_week(settings_or_league_raw: Any) -> Optional[int]:
    league = _flatten(settings_or_league_raw.get("fantasy_content", {}).get("league", {}))
    week = league.get("current_week")
    return int(week) if week not in (None, "") else None


def scoreboard_status(scoreboard_raw: Any) -> Tuple[List[str], Optional[str]]:
    """Matchup statuses for a week (preevent, midevent, postevent) and its end date."""
    league = scoreboard_raw.get("fantasy_content", {}).get("league", [])
    if len(league) < 2:
        return [], None
    matchups = [m.get("matchup", {}) for m in
                items(league[1].get("scoreboard", {}).get("0", {}).get("matchups", {}))]
    ends = [m.get("week_end") for m in matchups if m.get("week_end")]
    return [m.get("status", "") for m in matchups], (max(ends) if ends else None)


def draft_player_keys(draft_raw: Any) -> List[str]:
    league = draft_raw.get("fantasy_content", {}).get("league", [])
    if len(league) < 2:
        return []
    return [d["draft_result"]["player_key"] for d in items(league[1].get("draft_results", {}))
            if d.get("draft_result", {}).get("player_key")]

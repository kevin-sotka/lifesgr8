"""
nflverse: public NFL play-by-play, FTN charting, and expected fantasy points.

A second read-only source, used only for the weekly stat headline. It is plain
file download from GitHub releases: GET only, a fixed list of repositories and
release tags, and nothing is ever sent anywhere.

Files land in data/raw/nflverse/<source>/ named by the upstream update time, so a
file that has not changed upstream is not downloaded again, and every version
that was ever used stays on disk, same as the Yahoo snapshots.

Attribution: FTN charting data is provided by FTNFantasy.com/data, and the site
credits it wherever the First Read Share stat appears.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ..config import RAW

log = logging.getLogger(__name__)

API = "https://api.github.com/repos/%s/releases/tags/%s"

# (repository, release tag, asset name). The only files this module can fetch.
SOURCES: Dict[str, Tuple[str, str, str]] = {
    "pbp": ("nflverse/nflverse-data", "pbp", "play_by_play_{season}.csv.gz"),
    "ftn": ("nflverse/nflverse-data", "ftn_charting", "ftn_charting_{season}.csv"),
    "ep_weekly": ("ffverse/ffopportunity", "latest-data", "ep_weekly_{season}.csv"),
    # Names, positions, and teams for rookies the id map has not caught up with yet.
    "players": ("nflverse/nflverse-data", "players", "players.csv.gz"),
}

# Yahoo to NFL GSIS id map. nflverse itself loads this file for load_ff_playerids();
# nflverse's own player table carries no Yahoo ids. Versioned by git blob sha.
ID_MAP = ("dynastyprocess/data", "files/db_playerids.csv")
CONTENTS_API = "https://api.github.com/repos/%s/contents/%s"
ALLOWED_DOWNLOAD_HOSTS = ("https://github.com/", "https://objects.githubusercontent.com/",
                          "https://release-assets.githubusercontent.com/",
                          "https://raw.githubusercontent.com/dynastyprocess/data/")

NFLVERSE_DIR = RAW / "nflverse"


class NflverseError(Exception):
    pass


def _get(url: str, opener: Callable = urllib.request.urlopen, accept: str = "*/*") -> bytes:
    api_ok = url.startswith("https://api.github.com/repos/nflverse/nflverse-data/") or \
        url.startswith("https://api.github.com/repos/ffverse/ffopportunity/") or \
        url.startswith("https://api.github.com/repos/dynastyprocess/data/contents/files/")
    if not (api_ok or url.startswith(ALLOWED_DOWNLOAD_HOSTS)):
        raise NflverseError("Refusing to fetch %s: not an nflverse release URL" % url)
    headers = {"Accept": accept, "User-Agent": "lifesgr8-league-site"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = "Bearer " + token  # only raises the rate limit in CI
    req = urllib.request.Request(url, method="GET", headers=headers)
    with opener(req, timeout=120) as resp:
        return resp.read()


def latest_local(source: str) -> Optional[Path]:
    files = sorted((NFLVERSE_DIR / source).glob("*"))
    return files[-1] if files else None


def fetch(source: str, season: int, opener: Callable = urllib.request.urlopen) -> Tuple[Path, bool]:
    """Download one source if upstream has a newer copy. Returns (path, downloaded)."""
    if source not in SOURCES:
        raise NflverseError("Unknown nflverse source %r" % source)
    repo, tag, pattern = SOURCES[source]
    name = pattern.format(season=season)
    release = json.loads(_get(API % (repo, tag), opener, "application/vnd.github+json"))
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if asset is None:
        raise NflverseError("%s has no asset %s yet" % (tag, name))
    stamp = re.sub(r"[^0-9TZ]", "", asset["updated_at"])
    target = NFLVERSE_DIR / source / ("%s__%s" % (stamp, name))
    if target.exists():
        return target, False
    body = _get(asset["browser_download_url"], opener)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(target)
    log.info("nflverse %s: %s (%.0f KB)", source, name, len(body) / 1024)
    return target, True


def fetch_id_map(opener: Callable = urllib.request.urlopen) -> Tuple[Path, bool]:
    repo, path = ID_MAP
    meta = json.loads(_get(CONTENTS_API % (repo, path), opener, "application/vnd.github+json"))
    target = NFLVERSE_DIR / "ids" / ("%s__db_playerids.csv" % meta["sha"][:12])
    if target.exists():
        return target, False
    body = _get(meta["download_url"], opener)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(target)
    log.info("nflverse ids: db_playerids.csv (%.0f KB)", len(body) / 1024)
    return target, True


def fetch_all(season: int, opener: Callable = urllib.request.urlopen) -> Dict[str, Tuple[Path, bool]]:
    out = {source: fetch(source, season, opener) for source in SOURCES}
    out["ids"] = fetch_id_map(opener)
    return out

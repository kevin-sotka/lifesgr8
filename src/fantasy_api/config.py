"""
Settings from .env, with no third-party loader.

Secrets are read here and passed to the modules that need them. Nothing in this
module, or anywhere else, logs or prints a secret value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
DATA = ROOT / "data"
RAW = DATA / "raw"
TOKEN_FILE = DATA / ".yahoo_tokens.json"
INGEST_STATE = DATA / "ingest_state.json"
LEAGUE_TOML = ROOT / "league.toml"
RACES_TOML = ROOT / "races.toml"


def read_toml(path: Path) -> Dict[str, Any]:
    """
    Reader for the flat shape league.toml and races.toml use: [table],
    [[array of tables]], and scalar values. tomllib only exists on 3.11+.
    """
    doc: Dict[str, Any] = {}
    current: Optional[Dict[str, Any]] = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[["):
            current = {}
            doc.setdefault(line[2:-2].strip(), []).append(current)
        elif line.startswith("["):
            current = doc.setdefault(line[1:-1].strip(), {})
        elif "=" in line and current is not None:
            key, value = (x.strip() for x in line.split("=", 1))
            if value.startswith('"'):
                current[key] = value.strip('"')
            elif value in ("true", "false"):
                current[key] = value == "true"
            else:
                current[key] = float(value) if "." in value else int(value)
    return doc


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    league_id: Optional[str]

    def __repr__(self) -> str:  # never let a secret reach a log line by accident
        return "Settings(client_id=<set>, client_secret=<set>, redirect_uri=%r, league_id=%r)" % (
            self.redirect_uri, self.league_id)


class ConfigError(Exception):
    pass


def load_settings() -> Settings:
    file_values = _read_env_file(ENV_FILE)

    def get(name: str) -> str:
        return os.environ.get(name) or file_values.get(name, "")

    missing = [n for n in ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET") if not get(n)]
    if missing:
        raise ConfigError(
            "Missing %s. Copy .env.example to .env and fill in the values from your "
            "Yahoo app page." % ", ".join(missing))
    return Settings(
        client_id=get("YAHOO_CLIENT_ID"),
        client_secret=get("YAHOO_CLIENT_SECRET"),
        redirect_uri=get("YAHOO_REDIRECT_URI") or "oob",
        league_id=get("LEAGUE_ID") or None,
    )

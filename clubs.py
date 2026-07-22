"""
Registr klubů – umožňuje jednomu codebase obsluhovat víc klubů.

Každý klub má v config/clubs.json záznam s cestami k soupisu hráčů,
datové složce, příjemcem e-mailu a přepínači (generate_post, …).

MGNJ zůstává na původních cestách (config/players.json, data/2026.json),
aby se nerozbil dashboard ani historie. Další kluby mají vlastní podsložky.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
CLUBS_FILE = ROOT / "config" / "clubs.json"
DEFAULT_CLUB = "mgnj"

_REQUIRED = ("name", "players_file", "data_dir", "recipient_email")


def load_clubs() -> dict:
    with open(CLUBS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_club(club_id: str | None = None) -> dict:
    """Vrátí konfiguraci klubu s doplněnými defaulty a absolutními cestami.

    club_id None → výchozí klub (mgnj, kvůli zpětné kompatibilitě).
    """
    club_id = club_id or DEFAULT_CLUB
    clubs = load_clubs()
    if club_id not in clubs:
        raise ValueError(
            f"Neznámý klub '{club_id}'. Dostupné: {', '.join(sorted(clubs))}"
        )
    cfg = dict(clubs[club_id])
    cfg["id"] = club_id

    for key in _REQUIRED:
        if not cfg.get(key):
            raise ValueError(f"Klub '{club_id}': chybí povinné pole '{key}' v clubs.json")

    # Defaulty
    cfg.setdefault("short_name", cfg["name"])
    cfg.setdefault("emoji", "🥏")
    cfg.setdefault("generate_post", True)
    cfg.setdefault("dashboard_url", None)

    # Absolutní cesty
    cfg["players_path"] = ROOT / cfg["players_file"]
    cfg["data_path"] = ROOT / cfg["data_dir"]

    return cfg


def load_players(club: dict) -> list:
    with open(club["players_path"], encoding="utf-8") as f:
        return json.load(f)

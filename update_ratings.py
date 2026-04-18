#!/usr/bin/env python3
"""
Měsíční aktualizace PDGA ratingů
=================================
Spouštět každou 2. středu v měsíci (po finalizaci PDGA ratingů v 2. úterý).

Co dělá:
  1. Pro každý PDGA turnaj v data/{year}.json znovu stáhne výsledky
     a aktualizuje round_ratings (po finalizaci PDGA ratingů).
  2. Stáhne aktuální PDGA ratingy hráčů a uloží je pod klíč aktuálního měsíce.

Použití:
  python update_ratings.py           # aktuální rok
  python update_ratings.py --year 2026
  python update_ratings.py --year 2026 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from scrapers.pdga import PDGAScraper
from accumulator import Accumulator

LOG_FILE = Path(__file__).parent / "gators_agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("update_ratings")


def load_players() -> list:
    path = Path(__file__).parent / "config" / "players.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_event_id(url_pdga: str) -> int | None:
    """Extrahuje číslo PDGA eventu z URL."""
    if not url_pdga:
        return None
    m = re.search(r"/event/(\d+)", url_pdga)
    return int(m.group(1)) if m else None


def update_tournament_round_ratings(
    tournament: dict, scraper: PDGAScraper
) -> tuple[int, int]:
    """
    Znovu stáhne výsledky z PDGA eventu a aktualizuje round_ratings
    u všech hráčů v turnaji.

    Vrátí (počet aktualizovaných hráčů, počet hráčů v turnaji).
    """
    event_id = extract_event_id(tournament.get("url_pdga", ""))
    if not event_id:
        return 0, 0

    fresh_players, _ = scraper._get_our_players_in_event(
        event_id, tournament.get("name", "")
    )
    if not fresh_players:
        logger.warning(
            f"Turnaj '{tournament['name']}' (event #{event_id}): "
            f"žádní hráči znovu nenalezeni, přeskakuji."
        )
        return 0, 0

    # Map {pdga_number: fresh_data}
    fresh_by_pdga = {str(p["pdga"]): p for p in fresh_players if p.get("pdga")}

    updated = 0
    for result in tournament.get("results", []):
        pdga_key = str(result.get("pdga", ""))
        if pdga_key not in fresh_by_pdga:
            continue

        fresh = fresh_by_pdga[pdga_key]
        old_ratings = result.get("round_ratings", [])
        new_ratings = fresh.get("round_ratings", [])

        if new_ratings and new_ratings != old_ratings:
            result["round_ratings"] = new_ratings
            updated += 1
            logger.debug(
                f"  {result['player_name']}: {old_ratings} → {new_ratings}"
            )

    return updated, len(tournament.get("results", []))


def run(year: int, dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info(f"Moravian Gators – aktualizace PDGA ratingů pro rok {year}")
    logger.info("=" * 60)

    players = load_players()
    logger.info(f"Načteno {len(players)} členů klubu")

    acc = Accumulator(year=year)
    data = acc.load()

    if not data.get("tournaments"):
        logger.warning(f"Žádné turnaje v data/{year}.json, končím.")
        return

    scraper = PDGAScraper(players)

    # 1. Aktualizace round ratingů u PDGA turnajů
    logger.info("--- Aktualizace round ratingů ---")
    pdga_tournaments = [
        t for t in data["tournaments"] if t.get("url_pdga")
    ]
    logger.info(f"Nalezeno {len(pdga_tournaments)} PDGA turnajů k aktualizaci")

    total_updated = 0
    total_players = 0
    for i, t in enumerate(pdga_tournaments, 1):
        logger.info(
            f"[{i}/{len(pdga_tournaments)}] {t['name']} ({t.get('date','')})"
        )
        updated, count = update_tournament_round_ratings(t, scraper)
        total_updated += updated
        total_players += count
        if updated:
            logger.info(
                f"  → aktualizováno {updated}/{count} hráčů"
            )
        time.sleep(1)  # netlačíme PDGA server

    logger.info(
        f"Celkem aktualizováno {total_updated} hráčů "
        f"(z {total_players} záznamů v {len(pdga_tournaments)} turnajích)"
    )

    # 2. Aktualizace PDGA ratingů hráčů pro aktuální měsíc
    logger.info("--- Aktualizace PDGA ratingů hráčů ---")
    month_key = date.today().strftime("%Y-%m")
    try:
        pdga_ratings = scraper.get_player_ratings()
        if pdga_ratings:
            data = acc.update_ratings(pdga_ratings, month_key, data)
            logger.info(
                f"Uloženo {len(pdga_ratings)} ratingů pro měsíc {month_key}"
            )
    except Exception as e:
        logger.error(f"Stažení ratingů selhalo: {e}", exc_info=True)

    # 3. Uložení
    if dry_run:
        logger.info("--dry-run: data se NEukládají.")
        return

    acc.save(data)
    logger.info("Hotovo.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Moravian Gators – aktualizace PDGA ratingů"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="Rok k aktualizaci (default: aktuální)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Neukládat změny do data/{year}.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(year=args.year, dry_run=args.dry_run)

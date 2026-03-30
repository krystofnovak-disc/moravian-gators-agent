#!/usr/bin/env python3
"""
Moravian Gators Tournament Results Agent
=========================================
Spouštěj každé pondělí v 8:00 (viz README – nastavení cron jobu).

Co dělá:
  1. Zjistí datum uplynulého víkendu (sobota + neděle)
  2. Stáhne výsledky z idiscgolf.cz a pdga.com
  3. Najde turnaje, kde startovali naši hráči
  4. Vygeneruje příspěvek na FB/Instagram pomocí Claude API
  5. Pošle příspěvek e-mailem ke schválení

Spuštění:
  python main.py               # normální spuštění (uplynulý víkend)
  python main.py --dry-run     # jen scraping, bez generování a e-mailu
  python main.py --date 2026-03-14  # konkrétní sobota víkendu
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Načteme .env ze složky skriptu
load_dotenv(Path(__file__).parent / ".env", override=True)

from scrapers.idiscgolf import IDGScraper
from scrapers.pdga import PDGAScraper
from generator.post import PostGenerator
from delivery.email import EmailSender

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = Path(__file__).parent / "gators_agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------

def get_last_weekend(reference: date | None = None) -> tuple[date, date]:
    """
    Vrátí (sobota, neděle) posledního víkendu před daným dnem.
    Výchozí: dnešní datum.
    """
    today = reference or date.today()
    # weekday(): Monday=0 … Sunday=6
    # Chceme vždy minulou sobotu a neděli (ne aktuální víkend)
    days_back_to_sunday = (today.weekday() + 1) % 7 or 7   # dny zpět na nejbližší min. neděli
    last_sunday = today - timedelta(days=days_back_to_sunday)
    last_saturday = last_sunday - timedelta(days=1)
    return last_saturday, last_sunday


def load_players() -> list:
    """Načte databázi hráčů ze souboru config/players.json."""
    path = Path(__file__).parent / "config" / "players.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merge_results(idg: list, pdga: list) -> list:
    """
    Sloučí výsledky z obou zdrojů.
    Pokud stejný turnaj existuje v obou (PDGA turnaj v ČR se zobrazí
    i na idiscgolf), upřednostní idiscgolf verzi (má česky psaný název
    a ČADG-based výsledky).
    """
    merged = list(idg)  # idiscgolf jako základ
    idg_names_norm = {t["name"].lower().strip() for t in idg}

    for t in pdga:
        if t["name"].lower().strip() not in idg_names_norm:
            merged.append(t)

    return merged


def save_results_json(results: list, saturday: date, sunday: date) -> None:
    """Uloží surová data výsledků jako JSON (pro debugování)."""
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    fname = out_dir / f"results_{saturday}_{sunday}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Výsledky uloženy do {fname}")


def save_post_txt(post: str, saturday: date, sunday: date) -> None:
    """Uloží vygenerovaný příspěvek jako textový soubor."""
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    fname = out_dir / f"post_{saturday}_{sunday}.txt"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(post)
    logger.info(f"Příspěvek uložen do {fname}")


# ---------------------------------------------------------------------------
# Hlavní logika
# ---------------------------------------------------------------------------

def run(saturday: date, sunday: date, dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("Moravian Gators Agent – start")
    logger.info(f"Víkend: {saturday} (So) – {sunday} (Ne)")
    logger.info("=" * 60)

    # 1. Načti hráče
    players = load_players()
    logger.info(f"Načteno {len(players)} členů klubu")

    # 2. Scraping idiscgolf.cz
    logger.info("--- idiscgolf.cz ---")
    try:
        idg = IDGScraper(players).get_weekend_results(saturday, sunday)
    except Exception as e:
        logger.error(f"idiscgolf scraper selhal: {e}", exc_info=True)
        idg = []

    # 3. Scraping pdga.com
    logger.info("--- pdga.com ---")
    try:
        pdga = PDGAScraper(players).get_weekend_results(saturday, sunday)
    except Exception as e:
        logger.error(f"PDGA scraper selhal: {e}", exc_info=True)
        pdga = []

    # 4. Merge
    results = merge_results(idg, pdga)
    save_results_json(results, saturday, sunday)

    tournaments_with_us = [t for t in results if t.get("our_players")]
    logger.info(
        f"Celkem nalezeno turnajů: {len(results)}, "
        f"z toho s našimi hráči: {len(tournaments_with_us)}"
    )

    if not tournaments_with_us:
        logger.info("Žádní naši hráči na žádném turnaji. Příspěvek se negeneruje.")
        return

    if dry_run:
        logger.info("--dry-run: přeskakuji generování a odeslání e-mailu.")
        print("\nNalezené turnaje:")
        for t in tournaments_with_us:
            print(f"  • {t['name']} ({t.get('date','')}) – {len(t['our_players'])} hráčů")
        return

    # 5. Generování příspěvku
    logger.info("--- Generování příspěvku ---")
    generator = PostGenerator()
    post = generator.generate(tournaments_with_us, saturday, sunday)
    save_post_txt(post, saturday, sunday)

    print("\n" + "=" * 60)
    print("VYGENEROVANÝ PŘÍSPĚVEK:")
    print("=" * 60)
    print(post)
    print("=" * 60 + "\n")

    # 6. Odeslání e-mailem
    logger.info("--- Odeslání e-mailem ---")
    try:
        EmailSender().send(post, saturday, sunday, tournament_results=tournaments_with_us)
    except Exception as e:
        logger.error(f"Odeslání e-mailu selhalo: {e}", exc_info=True)
        logger.info("Příspěvek byl uložen lokálně v output/")

    logger.info("Agent dokončen.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Moravian Gators Tournament Results Agent"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Jen scraping, bez generování příspěvku a bez e-mailu",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Datum soboty konkrétního víkendu (default: minulý víkend)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.date:
        try:
            saturday = datetime.strptime(args.date, "%Y-%m-%d").date()
            sunday = saturday + timedelta(days=1)
        except ValueError:
            print(f"Chybný formát data: {args.date}. Použij YYYY-MM-DD.")
            sys.exit(1)
    else:
        saturday, sunday = get_last_weekend()

    run(saturday, sunday, dry_run=args.dry_run)

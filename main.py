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
from accumulator import Accumulator

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


import re as _re_norm
# Tier prefixy které se občas přidávají před název ("CDGT: Grabštejn" vs "Grabštejn").
# Pro porovnání v merge_results je strip-neme, aby PDGA verze "Grabštejn Open 2026"
# matchla idg verzi "CDGT: Grabštejn Open 2026".
_TIER_PREFIX_RE = _re_norm.compile(
    r"^(CDGT|PCT|ADGL|NJDGT|HDGT|PvDGT|DGPT|UDGL|JMDGL|SZDL|PADL|PDGL|"
    r"OPDK|JDL|TUTA|MMMASO|VAKAVA|BBDL|MCR|M[CČ]R)\s*:?\s*",
    _re_norm.IGNORECASE,
)


def _norm_name(name: str) -> str:
    """Normalizuje jméno turnaje pro porovnání: strip tier prefix + lowercase
    + bez diakritiky + bez bílých znaků navíc."""
    import unicodedata
    s = _TIER_PREFIX_RE.sub("", name or "").strip()
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


# Tier značky, které znamenají "tohle je PDGA-sanctioned turnaj"
# – pokud idg jen registruje, výsledky doindexuje PDGA. Můžeme ho z idg
# ignorovat, dokud ho PDGA scraper nepřinese kompletní.
_PDGA_TIERS = {"PCT", "PDGA", "MČR", "MCR", "A-TIER", "B-TIER", "C-TIER", "MAJOR", "ELITE"}


def _player_key(p: dict):
    """Klíč hráče pro deduplikaci napříč zdroji (cadg > pdga > jméno)."""
    if p.get("cadg"):
        return ("cadg", str(p["cadg"]))
    if p.get("pdga"):
        return ("pdga", str(p["pdga"]))
    return ("name", f"{p.get('first_name','')} {p.get('last_name','')}".lower())


def merge_results(idg: list, pdga: list) -> list:
    """
    Sloučí výsledky z obou zdrojů. PDGA je primární – pokud má stejný
    turnaj, jeho data (place, round ratingy) vítězí.

    Pokud je turnaj v OBOU zdrojích, sloučíme i seznam hráčů: PDGA verze
    je základ, z iDG doplníme hráče, které PDGA nemá (typicky členové bez
    PDGA čísla – přesně ty, kvůli kterým iDG scrapujeme). Tím se u smíšených
    turnajů (např. HDGT s PDGA sankcí) neztratí nečlenové PDGA.

    Turnaje jen v iDG (lokální ADGL/NJDGT/… bez PDGA) se přidají celé.

    Porovnání jmen: lowercase + bez diakritiky + strip tier prefixu
    (aby "PCT: Budišov" matchlo "Budišov").
    """
    merged = []
    idg_by_name = {_norm_name(t["name"]): t for t in idg}

    for pt in pdga:
        name_key = _norm_name(pt["name"])
        it = idg_by_name.get(name_key)
        if it:
            # Union hráčů: PDGA základ + iDG hráči, které PDGA nemá
            have = {_player_key(p) for p in pt.get("our_players", [])}
            extra = [
                p for p in it.get("our_players", [])
                if _player_key(p) not in have
            ]
            if extra:
                pt = {**pt, "our_players": pt.get("our_players", []) + extra}
                logger.info(
                    f"  Turnaj '{pt['name']}': doplněno {len(extra)} hráčů "
                    f"z iDG, které PDGA nemá (nečlenové PDGA)."
                )
        merged.append(pt)

    pdga_names = {_norm_name(t["name"]) for t in pdga}
    for it in idg:
        if _norm_name(it["name"]) not in pdga_names:
            merged.append(it)

    return merged


def filter_pending_pdga(results: list) -> list:
    """Vyhodí z idg výstupu turnaje, které jsou PDGA-tier a *nezfinalizované*.

    Důvod: u PCT/A-tier/PDGA turnajů idg často jen vystaví seznam registrovaných
    bez výsledků (PDGA je doplní za den-dva po). PDGA scraper je odtamtud
    spolehlivě doindexuje. Necháváme ten záznam vypadnout, místo aby blokoval
    odeslání e-mailu kvůli "incomplete results".
    """
    keep = []
    dropped = []
    for t in results:
        is_pdga_tier = (t.get("tier") or "").upper() in _PDGA_TIERS
        if t.get("source") == "idiscgolf" and is_pdga_tier:
            players = t.get("our_players", [])
            missing = sum(
                1 for p in players
                if (p.get("place") is None or p.get("place") == 0) and not p.get("_fulltext")
            )
            if players and missing == len(players):
                dropped.append(t)
                continue
        keep.append(t)
    for t in dropped:
        logger.info(
            f"Vyřazuji PDGA-tier turnaj '{t['name']}' z idg (jen registrace, "
            f"PDGA ho doindexuje): {len(t.get('our_players', []))} hráčů bez umístění."
        )
    return keep


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

class IncompleteResultsError(Exception):
    """Výjimka: některé turnaje nemají finalizované výsledky (chybí umístění)."""
    pass


def check_results_completeness(results: list) -> list:
    """
    Zkontroluje, zda všechny turnaje mají kompletní výsledky (umístění).
    Vrátí seznam turnajů s chybějícími umístěními.

    Fulltext matches (_fulltext=True) jsou nespolehlivé – ignorujeme je
    pro kontrolu úplnosti (mohlo by jít o zmínku v sponzor textu apod.).
    """
    incomplete = []
    for t in results:
        # Zrušené turnaje nemají umístění z principu – přeskakujeme.
        name_upper = (t.get("name") or "").upper()
        if "ZRUŠEN" in name_upper or "ZRUSEN" in name_upper or "CANCEL" in name_upper:
            continue
        missing = [
            p for p in t.get("our_players", [])
            if (p.get("place") is None or p.get("place") == 0)
            and not p.get("_fulltext")
        ]
        if missing:
            incomplete.append({
                "name": t["name"],
                "missing_count": len(missing),
                "total_count": len(t["our_players"]),
                "missing_players": [
                    f"{p['first_name']} {p['last_name']}" for p in missing
                ],
            })
    return incomplete


def run(saturday: date, sunday: date, dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("Moravian Gators Agent – start")
    logger.info(f"Víkend: {saturday} (So) – {sunday} (Ne)")
    logger.info("=" * 60)

    # 1. Načti hráče
    players = load_players()
    logger.info(f"Načteno {len(players)} členů klubu")

    # Varování o neúplných datech (selhání scraperů) → surfujeme do e-mailu.
    warnings: list[str] = []

    # 2. Scraping pdga.com (PRIMÁRNÍ – spolehlivější, má round ratingy)
    logger.info("--- pdga.com ---")
    pdga_scraper = PDGAScraper(players)
    try:
        pdga = pdga_scraper.get_weekend_results(saturday, sunday)
    except Exception as e:
        logger.error(f"PDGA scraper selhal: {e}", exc_info=True)
        pdga = []
        warnings.append(
            "PDGA: scraper zcela selhal (pdga.com nedostupné) – "
            "PDGA turnaje v přehledu chybí."
        )
    warnings.extend(pdga_scraper.errors)

    # 3. Scraping idiscgolf.cz / ceskydiscgolf.cz (DOPLŇUJÍCÍ – jen co PDGA nemá)
    logger.info("--- idiscgolf.cz ---")
    idg_scraper = IDGScraper(players)
    try:
        idg = idg_scraper.get_weekend_results(saturday, sunday)
    except Exception as e:
        logger.error(f"idiscgolf scraper selhal: {e}", exc_info=True)
        idg = []
        warnings.append(
            "iDG: scraper zcela selhal (ceskydiscgolf.cz nedostupné) – "
            "iDG turnaje v přehledu chybí."
        )
    warnings.extend(idg_scraper.errors)

    if warnings:
        for w in warnings:
            logger.warning(f"⚠️  {w}")

    # 4. Merge + filtr PDGA-tier registrací
    results = merge_results(idg, pdga)
    results = filter_pending_pdga(results)
    save_results_json(results, saturday, sunday)

    def _is_canceled(t):
        n = (t.get("name") or "").upper()
        return "ZRUŠEN" in n or "ZRUSEN" in n or "CANCEL" in n

    tournaments_with_us = [
        t for t in results if t.get("our_players") and not _is_canceled(t)
    ]
    canceled = [t for t in results if t.get("our_players") and _is_canceled(t)]
    for t in canceled:
        logger.info(f"Přeskakuji zrušený turnaj: {t['name']}")
    logger.info(
        f"Celkem nalezeno turnajů: {len(results)}, "
        f"z toho s našimi hráči: {len(tournaments_with_us)}"
        + (f" ({len(canceled)} zrušených přeskočeno)" if canceled else "")
    )

    if not tournaments_with_us:
        logger.info("Žádní naši hráči na žádném turnaji. Příspěvek se negeneruje.")
        return

    # 4b. Kontrola kompletnosti výsledků
    # Incomplete turnaj = víc-denní PCT/Major který ještě dobíhá, nebo idg
    # nestihl nahrát výsledky. Pokud aspoň 1 turnaj kompletní, pošleme e-mail
    # s ním a neúplné vyřadíme z postu (warning). Pouze pokud VŠECHNY jsou
    # neúplné, vyhodíme exit 75 → workflow ho v dalším cron slotu zkusí znovu.
    incomplete = check_results_completeness(tournaments_with_us)
    if incomplete:
        for t in incomplete:
            logger.warning(
                f"Turnaj '{t['name']}': {t['missing_count']}/{t['total_count']} "
                f"hráčů nemá umístění: {', '.join(t['missing_players'])}"
            )

        incomplete_names = {t["name"] for t in incomplete}
        complete = [t for t in tournaments_with_us if t["name"] not in incomplete_names]

        if not complete:
            raise IncompleteResultsError(
                f"{len(incomplete)} turnaj(ů) nemá finalizované výsledky a žádný jiný "
                f"není kompletní. Spusťte workflow znovu později."
            )

        logger.warning(
            f"Pokračuji s {len(complete)} kompletními turnaji; "
            f"{len(incomplete)} neúplných vyřazeno z postu "
            f"(dotáhnete ručně, až idg/PDGA nahraje výsledky)."
        )
        tournaments_with_us = complete

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
        EmailSender().send(post, saturday, sunday,
                           tournament_results=tournaments_with_us,
                           warnings=warnings)
    except Exception as e:
        logger.error(f"Odeslání e-mailu selhalo: {e}", exc_info=True)
        logger.info("Příspěvek byl uložen lokálně v output/")

    # 7. Kumulativní ukládání výsledků do data/{year}.json
    logger.info("--- Akumulace výsledků ---")
    try:
        acc = Accumulator(year=saturday.year)
        data = acc.load()
        data = acc.add_tournaments(tournaments_with_us, data)

        # 7b. Aktualizace PDGA ratingů (jednou za měsíc stačí, ale spouštíme vždy)
        month_key = saturday.strftime("%Y-%m")
        if month_key not in data.get("ratings", {}):
            logger.info("--- Stahování PDGA ratingů pro %s ---", month_key)
            try:
                pdga_ratings = PDGAScraper(players).get_player_ratings()
                if pdga_ratings:
                    data = acc.update_ratings(pdga_ratings, month_key, data)
            except Exception as e:
                logger.error(f"Stažení ratingů selhalo: {e}", exc_info=True)

        acc.save(data)
    except Exception as e:
        logger.error(f"Akumulace výsledků selhala: {e}", exc_info=True)

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

    try:
        run(saturday, sunday, dry_run=args.dry_run)
    except IncompleteResultsError as e:
        logger.error(f"NEÚPLNÉ VÝSLEDKY: {e}")
        sys.exit(75)  # EX_TEMPFAIL – spustit znovu později

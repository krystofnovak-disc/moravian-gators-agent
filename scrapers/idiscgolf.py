"""
Scraper pro iDG (ČADG) – od 14. 7. 2026 běží na novém webu
ceskydiscgolf.cz/idg s veřejným JSON API na api.ceskydiscgolf.cz.

Nahrazuje původní HTML scraping starého idiscgolf.cz. Data tahá přímo
z REST API (žádné parsování HTML, žádný Playwright):

  * Seznam turnajů:  GET /api/tournaments/            (celý seznam, filtr v Pythonu)
  * Detail turnaje:  GET /api/tournaments/{uuid}/     (tier / liga / datum)
  * Výsledky:        GET /api/tournaments/{uuid}/results/

Hráče párujeme na řádky výsledků přes:
  1. pdga_number   (spolehlivé, pokud hráč PDGA číslo má a je vyplněné)
  2. user_id       (nové iDG ID – mapované z config/players.json → "idg_id")

Staré CADG číslo ("cadg") odpovídá poli "old_id" v novém API; mapping
na nové "idg_id" je předpočítaný v config/players.json.
"""

from __future__ import annotations

import requests
from datetime import date, datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

API_BASE = "https://api.ceskydiscgolf.cz/api"
WEB_BASE = "https://ceskydiscgolf.cz/idg"

# Známé ligové/tour tagy (první token z main_league_name). Pokud liga
# odpovídá jednomu z nich, použijeme ho jako tier. Jinak main_league token.
_KNOWN_TIER_TAGS = {
    "ADGL", "NJDGT", "HDGT", "CDGT", "DGPT", "PvDGT", "PCT",
    "UDGL", "JMDGL", "PDGL", "SZDL", "JDL", "TUTA", "BBDL",
}


class IDGScraper:
    def __init__(self, players: list):
        self.players = players

        # Indexy pro párování řádků výsledků na naše hráče
        self.idg_id_to_player = {
            p["idg_id"]: p for p in players if p.get("idg_id")
        }
        self.pdga_to_player = {
            str(p["pdga"]): p for p in players if p.get("pdga")
        }

        # Chyby, které mohou znamenat neúplná data (surfují se do e-mailu).
        self.errors: list[str] = []

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
        })

    # ------------------------------------------------------------------
    # HTTP helper – delší timeout + retry (API bývá občas pomalé)
    # ------------------------------------------------------------------
    def _get_json(self, url: str, *, timeout: int = 60, retries: int = 2):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                last_exc = e
                if attempt < retries:
                    logger.warning(
                        f"  Pokus {attempt + 1}/{retries + 1} selhal pro {url} "
                        f"({e}), zkouším znovu…"
                    )
                    time.sleep(2)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Veřejné API
    # ------------------------------------------------------------------

    def get_weekend_results(self, saturday: date, sunday: date) -> list:
        """
        Vrátí seznam turnajů z uplynulého víkendu, kde byli naši hráči.
        Každý prvek: {name, date, id, url, our_players, tier, source}
        """
        tournaments = self._find_weekend_tournaments(saturday, sunday)
        logger.info(
            f"iDG: nalezeno {len(tournaments)} turnajů pro víkend "
            f"{saturday}–{sunday}"
        )

        results = []
        failed = []
        for t in tournaments:
            time.sleep(0.5)  # netlačíme API
            logger.info(f"  Kontroluji turnaj {t['id']}: {t['name']}")
            our_players = self._get_our_players(t["id"])
            if our_players is None:  # tvrdá chyba načtení výsledků
                failed.append(t["name"])
                continue
            if our_players:
                results.append({
                    "name": t["name"],
                    "date": t["date"],
                    "id": t["id"],
                    "url": f"{WEB_BASE}/turnaje/{t['id']}",
                    "our_players": our_players,
                    "tier": self._extract_tier(t),
                    "source": "idiscgolf",
                })

        if failed:
            self.errors.append(
                f"iDG: u {len(failed)} turnajů se nepodařilo načíst výsledky "
                f"({', '.join(failed[:3])}{'…' if len(failed) > 3 else ''})."
            )
        return results

    # ------------------------------------------------------------------
    # Hledání turnajů pro daný víkend
    # ------------------------------------------------------------------

    def _find_weekend_tournaments(self, saturday: date, sunday: date) -> list:
        """Stáhne celý seznam turnajů a vyfiltruje ty, které se překrývají
        s víkendem. Zahrnuje i pátek – vícedenní turnaje (PCT, MČR, CDGT)
        často začínají v pátek nebo běží So–Po (státní svátky).
        """
        friday = saturday - timedelta(days=1)

        try:
            data = self._get_json(f"{API_BASE}/tournaments/")
        except Exception as e:
            logger.error(f"Nepodařilo se načíst seznam turnajů: {e}")
            self.errors.append(
                "iDG: nepodařilo se načíst seznam turnajů z ceskydiscgolf.cz "
                "– iDG turnaje mohou v přehledu zcela chybět."
            )
            return []

        found = []
        for t in data:
            if not t.get("is_enabled", True):
                continue
            start = self._parse_iso(t.get("start_date"))
            end = self._parse_iso(t.get("end_date")) or start
            if not start:
                continue
            # Překryv intervalu turnaje [start, end] s [pátek, neděle]
            if start <= sunday and end >= friday:
                found.append({
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "date": t.get("start_date", ""),
                    # tier metadata rovnou z listu (nemusíme tahat detail)
                    "main_league_name": t.get("main_league_name") or "",
                    "pdga_tier": t.get("pdga_tier") or "",
                    "is_pdga": t.get("is_pdga", False),
                    "is_association": t.get("is_association", False),
                    "division_cadg": t.get("division_cadg") or "",
                })
        return found

    # ------------------------------------------------------------------
    # Výsledky konkrétního turnaje
    # ------------------------------------------------------------------

    def _get_our_players(self, tid: str) -> list | None:
        """Stáhne výsledky turnaje a vrátí naše hráče s umístěním.

        Prochází divize → řádky, páruje na naše hráče přes pdga_number
        nebo user_id (idg_id). Hráči, kteří jsou jen registrovaní bez
        odehraného kola, dostanou place=None (řeší kontrola úplnosti v main).

        Vrací None při tvrdé chybě načtení (odlišení od "žádní naši hráči").
        """
        url = f"{API_BASE}/tournaments/{tid}/results/"
        try:
            data = self._get_json(url)
        except Exception as e:
            logger.error(f"Nepodařilo se načíst výsledky turnaje {tid}: {e}")
            return None

        our_players = []
        for division in data.get("divisions", []):
            div_abbr = division.get("division_abbr") or division.get("division") or ""
            for row in division.get("rows", []):
                player = self._match_player(row)
                if player is None:
                    continue

                place = row.get("place")
                if row.get("is_dnf"):
                    place = None  # DNF nemá relevantní umístění

                our_players.append({
                    "first_name": player["first_name"],
                    "last_name": player["last_name"],
                    "cadg": player.get("cadg"),
                    "pdga": player.get("pdga"),
                    "role": player.get("role", ""),
                    "note": player.get("note", ""),
                    "division": div_abbr,
                    "place": place,
                    "score": self._format_score(row.get("to_par")),
                    "round_ratings": [],
                })
        return our_players

    def _match_player(self, row: dict) -> dict | None:
        """Napáruje řádek výsledku na našeho hráče.

        Priorita:
          1. pdga_number (pokud řádek i náš hráč ho mají)
          2. user_id → idg_id
        """
        pdga_num = str(row.get("pdga_number") or "").strip()
        if pdga_num and pdga_num in self.pdga_to_player:
            return self.pdga_to_player[pdga_num]

        user_id = row.get("user_id")
        if user_id is not None and user_id in self.idg_id_to_player:
            return self.idg_id_to_player[user_id]

        return None

    # ------------------------------------------------------------------
    # Tier / liga
    # ------------------------------------------------------------------

    def _extract_tier(self, t: dict) -> str:
        """Odvodí tier z metadat turnaje (main_league_name / pdga_tier / název).

        Vrací tagy konzistentní se starými daty: ADGL, NJDGT, HDGT, CDGT,
        PCT, PvDGT, DGPT, MČR, … nebo "local".
        """
        name_upper = (t.get("name") or "").upper()

        # 1. MČR má přednost (z názvu)
        if any(k in name_upper for k in (
            "MISTROVSTVÍ ČR", "MČR", "MISTROVSTVÍ ČESKÉ REPUBLIKY"
        )):
            return "MČR"

        # 2. Liga z metadat: první token main_league_name, pokud je to
        #    rozpoznaný tour/ligový tag. Obskurní lokální ligy (REKY26,
        #    JUDIT, …) spadají pod "local".
        league = (t.get("main_league_name") or "").strip()
        if league:
            first = league.split()[0].upper()
            if first in _KNOWN_TIER_TAGS:
                return first

        # 3. Detekce tagu z názvu turnaje (např. "PCT: …", "CDGT: …")
        for tag in _KNOWN_TIER_TAGS:
            if tag in name_upper:
                return tag

        # 4. PDGA-sanctioned bez rozpoznané ligy → PDGA X-tier
        pdga_tier = (t.get("pdga_tier") or "").strip().upper()
        if pdga_tier in ("A", "B", "C", "M"):
            return {"M": "PDGA Major"}.get(pdga_tier, f"PDGA {pdga_tier}-tier")

        return "local"

    # ------------------------------------------------------------------
    # Pomocné
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso(value) -> date | None:
        """'2026-07-17' → date; None při chybě."""
        if not value:
            return None
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _format_score(to_par) -> str:
        """int to_par → '-5' / '+3' / 'E' (even par). None → ''."""
        if to_par is None:
            return ""
        try:
            v = int(to_par)
        except (ValueError, TypeError):
            return ""
        if v == 0:
            return "E"
        return f"{v:+d}"

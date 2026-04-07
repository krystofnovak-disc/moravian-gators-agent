"""
Scraper pro PDGA.com – hledá výsledky hráčů Moravian Gators
z uplynulého víkendu na mezinárodních turnajích.

Přístup:
  1. Projdeme profily hráčů s PDGA číslem a najdeme jejich turnaje z víkendu
  2. Pro každý nalezený event stáhneme výsledky a hledáme naše PDGA čísla
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
import logging
import time
import re
import unicodedata

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pdga.com"

# Kategorie PDGA
DIVISIONS = [
    "MPO", "FPO",
    "MA1", "MA2", "MA3", "MA4",
    "FA1", "FA2", "FA3", "FA4",
    "MP40", "MP50", "MP60",
    "FP40", "FP50",
    "MJ10", "MJ12", "MJ15", "MJ18",
    "FJ10", "FJ12", "FJ15", "FJ18",
]


def normalize(text: str) -> str:
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("utf-8").lower().strip()


class PDGAScraper:
    def __init__(self, players: list):
        self.players = players
        self.players_with_pdga = [p for p in players if p.get("pdga")]

        self.pdga_set = {str(p["pdga"]) for p in self.players_with_pdga}
        self.pdga_to_player = {str(p["pdga"]): p for p in self.players_with_pdga}

        # Fallback jménový index
        self.norm_name_to_player = {}
        for p in players:
            key = normalize(f"{p['first_name']} {p['last_name']}")
            self.norm_name_to_player[key] = p

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,cs;q=0.8",
        })

    # ------------------------------------------------------------------
    # Veřejné API
    # ------------------------------------------------------------------

    def get_weekend_results(self, saturday: date, sunday: date) -> list:
        """
        Vrátí seznam PDGA turnajů z uplynulého víkendu, kde byli naši hráči.
        """
        events = self._find_weekend_events(saturday, sunday)
        logger.info(f"pdga.com: nalezeno {len(events)} eventů pro víkend {saturday}–{sunday}")

        results = []
        seen_ids = set()
        for ev in events:
            if ev["id"] in seen_ids:
                continue
            seen_ids.add(ev["id"])
            time.sleep(1.5)
            logger.info(f"  Kontroluji PDGA event #{ev['id']}: {ev['name']}")
            our_players, tier = self._get_our_players_in_event(ev["id"], ev["name"])
            if our_players:
                results.append({
                    "name": ev["name"],
                    "date": ev.get("date", ""),
                    "id": ev["id"],
                    "url": f"{BASE_URL}/tour/event/{ev['id']}",
                    "our_players": our_players,
                    "tier": tier,
                    "source": "pdga",
                })
        return results

    # ------------------------------------------------------------------
    # Hledání eventů přes profily hráčů
    # ------------------------------------------------------------------

    def _find_weekend_events(self, saturday: date, sunday: date) -> list:
        """Najde PDGA eventy z víkendu přes profily hráčů."""
        found = {}
        checked = 0
        for p in self.players_with_pdga:
            try:
                time.sleep(3)  # PDGA rate limit – max ~20 req/min
                events = self._player_recent_events(p["pdga"])
                for ev in events:
                    if self._dates_overlap_weekend(ev.get("dates_raw", ""), saturday, sunday):
                        if ev["id"] not in found:
                            found[ev["id"]] = ev
                            logger.info(f"  PDGA event nalezen přes hráče {p['first_name']} {p['last_name']}: {ev['name']}")
                checked += 1
            except Exception as e:
                logger.warning(f"Profil PDGA #{p['pdga']}: {e}")

        logger.info(f"Zkontrolováno {checked} PDGA profilů, nalezeno {len(found)} eventů")
        return list(found.values())

    def _player_recent_events(self, pdga_number: int) -> list:
        """Stáhne hlavní profil hráče a vrátí seznam turnajů z aktuální sezóny."""
        url = f"{BASE_URL}/player/{pdga_number}"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 429:
                logger.warning(f"PDGA rate limit, čekám 30s…")
                time.sleep(30)
                resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            events = []

            # PDGA profil má tabulky s turnajovými výsledky
            # Sloupce: Place, Points, Tournament, Tier, Dates
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if not rows:
                    continue
                header = [c.get_text(strip=True).lower() for c in rows[0].find_all(["td", "th"])]
                if "tournament" not in header or "dates" not in header:
                    continue

                tourn_idx = header.index("tournament")
                dates_idx = header.index("dates")

                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) <= max(tourn_idx, dates_idx):
                        continue

                    # Najdi odkaz na event
                    link = cells[tourn_idx].find("a", href=re.compile(r"/tour/event/\d+"))
                    if not link:
                        continue

                    m = re.search(r"/tour/event/(\d+)", link["href"])
                    if not m:
                        continue

                    dates_raw = cells[dates_idx].get_text(strip=True)
                    events.append({
                        "id": int(m.group(1)),
                        "name": link.get_text(strip=True),
                        "date": dates_raw,
                        "dates_raw": dates_raw,
                    })

            return events
        except Exception as e:
            logger.warning(f"_player_recent_events PDGA #{pdga_number}: {e}")
            return []

    # ------------------------------------------------------------------
    # Parsování výsledků eventu
    # ------------------------------------------------------------------

    def _get_our_players_in_event(self, event_id: int, event_name: str = "") -> tuple:
        """Stáhne výsledky eventu a vrátí (naše hráče, tier)."""
        url = f"{BASE_URL}/tour/event/{event_id}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            players = self._parse_event_results(soup)
            tier = self._extract_tier(soup, event_name)
            return players, tier
        except Exception as e:
            logger.error(f"Nepodařilo se načíst PDGA event #{event_id}: {e}")
            return [], "PDGA"

    def _parse_event_results(self, soup: BeautifulSoup) -> list:
        """
        Parsuje stránku výsledků PDGA eventu.
        PDGA stránky mají výsledky po divizích – sekce označené H2/H3.
        Každý hráč má odkaz na profil /player/{pdga_number}.
        """
        our_players = []
        current_div = None

        for element in soup.find_all(True):
            tag = element.name.lower()

            # Detekce divize z nadpisů (formát: "MPO · Mixed Pro Open(12)")
            if tag in ("h2", "h3", "h4"):
                text = element.get_text(strip=True).upper()
                for div in DIVISIONS:
                    if re.search(rf"\b{div}\b", text):
                        current_div = div
                        break
                # Pokud není standardní divize, zkusíme vzít kód před "·"
                if not current_div:
                    m = re.match(r"([A-Z0-9]+)\s*·", text)
                    if m:
                        current_div = m.group(1)

            # Řádky tabulky
            if tag == "tr":
                # Hledáme odkaz na hráčský profil
                for link in element.find_all("a", href=re.compile(r"/player/\d+")):
                    m = re.search(r"/player/(\d+)", link.get("href", ""))
                    if m and m.group(1) in self.pdga_set:
                        p = self.pdga_to_player[m.group(1)]
                        cells = element.find_all(["td", "th"])
                        our_players.append({
                            "first_name": p["first_name"],
                            "last_name": p["last_name"],
                            "cadg": p.get("cadg"),
                            "pdga": p["pdga"],
                            "role": p.get("role", ""),
                            "note": p.get("note", ""),
                            "place": self._extract_place(cells),
                            "division": current_div,
                            "score": self._extract_score(cells),
                            "round_ratings": self._extract_round_ratings(cells),
                        })

        return our_players

    def _extract_tier(self, soup: BeautifulSoup, event_name: str) -> str:
        """Extrahuje PDGA tier z event stránky nebo názvu."""
        # 1. Hledáme tier v metadatech stránky
        page_text = soup.get_text(" ", strip=True)
        tier_match = re.search(r"Tier:\s*([A-Z])", page_text)
        if tier_match:
            tier_letter = tier_match.group(1)
            tier_map = {"A": "PDGA A-tier", "B": "PDGA B-tier", "C": "PDGA C-tier", "M": "PDGA Major"}
            base_tier = tier_map.get(tier_letter, f"PDGA {tier_letter}-tier")
        else:
            base_tier = "PDGA"

        # 2. Detekce z názvu (nadřazuje základní tier)
        name_upper = event_name.upper()
        if "DGPT" in name_upper and "EUROTOUR" in name_upper:
            return "DGPT EuroTour"
        if "DGPT" in name_upper:
            return "DGPT"
        if "PCT" in name_upper:
            return "PCT"

        return base_tier

    @staticmethod
    def _extract_round_ratings(cells) -> list:
        """Pokusí se najít round ratings v buňkách řádku (typicky 3-4 místná čísla)."""
        # Round ratings jsou obvykle ve sloupcích za skóre, hodnoty 600-1100
        ratings = []
        for cell in cells:
            text = cell.get_text(strip=True)
            if re.match(r"^[6-9]\d{2}$|^1[01]\d{2}$", text):
                ratings.append(int(text))
        return ratings

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    def get_player_ratings(self) -> dict:
        """
        Stáhne aktuální PDGA rating pro všechny hráče s PDGA číslem.

        Returns
        -------
        dict : ``{cadg_str: {"name": str, "pdga_rating": int|None, "idg_rating": None}}``
        """
        ratings = {}
        pdga_players = [(p, p.get("pdga")) for p in self.players if p.get("pdga")]
        logger.info("Stahuji PDGA ratingy pro %d hráčů...", len(pdga_players))

        for p, pdga_num in pdga_players:
            cadg = str(p.get("cadg", ""))
            name = f"{p['first_name']} {p['last_name']}"
            try:
                url = f"{BASE_URL}/player/{pdga_num}"
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                rating_el = soup.find(class_="current-rating")
                if rating_el:
                    m = re.search(r"(\d{3,4})", rating_el.get_text())
                    if m:
                        ratings[cadg] = {
                            "name": name,
                            "pdga_rating": int(m.group(1)),
                            "idg_rating": None,
                        }
                        logger.debug("  %s: PDGA %s", name, m.group(1))
            except Exception as e:
                logger.warning("  Rating pro %s selhal: %s", name, e)
            time.sleep(2)

        logger.info("Staženo %d PDGA ratingů.", len(ratings))
        return ratings

    # ------------------------------------------------------------------
    # Pomocné metody
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_place(cells) -> int | None:
        for cell in cells[:3]:
            text = cell.get_text(strip=True)
            m = re.match(r"^(\d+)\.?$", text)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_score(cells) -> str:
        for cell in reversed(cells):
            text = cell.get_text(strip=True)
            if re.match(r"^[+-]?\d+$", text):
                return text
        return ""

    @staticmethod
    def _parse_pdga_date(date_str: str) -> date | None:
        """Parsuje PDGA formát data, např. '08-Mar-2026' nebo '26-Feb-2026'."""
        for fmt in ["%d-%b-%Y", "%d %b %Y", "%b %d, %Y"]:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def _dates_overlap_weekend(cls, dates_raw: str, saturday: date, sunday: date) -> bool:
        """
        Kontroluje zda se PDGA datum/rozsah překrývá s víkendem.
        Zahrnuje i pátek – vícedenní turnaje často začínají v pátek.
        Formáty: '08-Mar-2026', '26-Feb to 28-Feb-2026', '28-Feb to 01-Mar-2026'
        """
        if not dates_raw:
            return False

        friday = saturday - timedelta(days=1)

        # Jednoduchý datum
        single = cls._parse_pdga_date(dates_raw)
        if single:
            return friday <= single <= sunday

        # Rozsah: "26-Feb to 28-Feb-2026"
        m = re.match(r"(\d{1,2}-\w+)(?:-\d{4})?\s+to\s+(\d{1,2}-\w+-\d{4})", dates_raw)
        if m:
            end_date = cls._parse_pdga_date(m.group(2))
            if end_date:
                # Rok z koncového data
                year = end_date.year
                start_str = m.group(1)
                if not re.search(r"\d{4}", start_str):
                    start_str += f"-{year}"
                start_date = cls._parse_pdga_date(start_str)
                if start_date and end_date:
                    # Překryv: turnaj probíhá v rozsahu, víkend je pá–ne
                    return start_date <= sunday and end_date >= friday

        return False

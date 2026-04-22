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
        """Najde PDGA eventy z víkendu dvěma cestami:

        1) Přes PDGA tour calendar – zachytí i "Live" eventy, které ještě
           nejsou finalizované v profilech hráčů (PDGA přidává event do
           profilu až s propočteným tier ratingem, typicky v 2. úterý měsíce).
        2) Přes profily hráčů – záchyt nedávných turnajů, kde byl alespoň
           jeden z našich hráčů s PDGA číslem.

        Obě cesty se slučují přes event ID.
        """
        found = {}

        # 1) PDGA tour calendar – rychlá cesta, najde i Live eventy
        try:
            for ev in self._tour_calendar_events(saturday, sunday):
                if ev["id"] not in found:
                    found[ev["id"]] = ev
                    logger.info(f"  PDGA tour calendar: {ev['name']} (#{ev['id']})")
        except Exception as e:
            logger.warning(f"PDGA tour calendar selhal: {e}", exc_info=True)

        # 2) Profily hráčů – záložní cesta + potvrzení účasti
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

        logger.info(f"Zkontrolováno {checked} PDGA profilů, celkem eventů: {len(found)}")
        return list(found.values())

    # Země, odkud naši členové pravidelně vyjíždí na PDGA turnaje.
    # Pokud by v budoucnu startovali jinde, stačí seznam rozšířit.
    _TARGET_COUNTRIES = ("Czech Republic", "Slovakia", "Poland", "Austria", "Germany", "Hungary")

    def _tour_calendar_events(self, saturday: date, sunday: date) -> list:
        """Najde PDGA eventy přes tour calendar (/tour?year=YYYY).

        Důvod existence: hráčské profily (``_player_recent_events``) obsahují
        jen **finalizované** turnaje – PDGA je do profilu zapíše až
        s propočteným tier ratingem (typicky 2. úterý následujícího měsíce).
        Eventy ve stavu "Live" těsně po víkendu ještě v profilech nejsou.

        Postup:
          1) Stáhneme `/tour?year={YYYY}` – vrátí eventy za celý rok pro
             všechny země (cca 1100 řádků).
          2) Filtr v Pythonu: datum (pátek–neděle daného víkendu) + země
             ze seznamu ``_TARGET_COUNTRIES``.
          3) Vrátí seznam kandidátů; účast našich hráčů se ověří později
             v ``_get_our_players_in_event``.
        """
        friday = saturday - timedelta(days=1)
        weekend_days = {friday, saturday, sunday}
        year = saturday.year

        url = f"{BASE_URL}/tour?year={year}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"PDGA tour calendar nedostupný: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        events = []

        for tr in soup.select("table tr"):
            link = tr.find("a", href=re.compile(r"/tour/event/\d+"))
            if not link:
                continue
            m = re.search(r"/tour/event/(\d+)", link["href"])
            if not m:
                continue
            event_id = int(m.group(1))

            # Strukturované buňky: OfficialName | DateRange | StatusIcons | Classification | Tier | Location
            name_cell = tr.select_one(".views-field-OfficialName")
            date_cell = tr.select_one(".views-field-DateRange")
            loc_cell = tr.select_one(".views-field-Location")
            if not (name_cell and date_cell and loc_cell):
                continue

            name = name_cell.get_text(" ", strip=True)
            date_text = date_cell.get_text(" ", strip=True)
            location = loc_cell.get_text(" ", strip=True)

            # Filtr podle země (rychlá cesta – odfiltruje ~95 % řádků)
            if not any(c in location for c in self._TARGET_COUNTRIES):
                continue

            event_dates = self._parse_calendar_dates(date_text, year)
            if not event_dates:
                continue

            # Pá–Ne víkend musí překrývat datum konání eventu
            if weekend_days.intersection(event_dates):
                events.append({
                    "id": event_id,
                    "name": name,
                    "date": min(event_dates).isoformat(),
                    "dates_raw": date_text,
                })

        return events

    @staticmethod
    def _parse_calendar_dates(date_text: str, year: int) -> set:
        """Parsuje PDGA tour calendar datumové texty (např. "Apr 17-19 Fri-Sun"
        nebo "Mar 29-Apr 2 Sun-Thu") a vrátí set ``date`` objektů pokrývajících
        všechny dny konání.
        """
        from datetime import datetime as _dt

        if not date_text:
            return set()

        # Samostatný den: "Apr 18"
        m = re.match(r"([A-Za-z]{3})\s+(\d{1,2})\s*(?:[A-Za-z]{3})?$", date_text.strip())
        if m:
            try:
                d = _dt.strptime(f"{m.group(1)} {m.group(2)} {year}", "%b %d %Y").date()
                return {d}
            except ValueError:
                return set()

        # Rozsah ve stejném měsíci: "Apr 17-19 Fri-Sun"
        m = re.match(r"([A-Za-z]{3})\s+(\d{1,2})-(\d{1,2})\b", date_text.strip())
        if m:
            try:
                start = _dt.strptime(f"{m.group(1)} {m.group(2)} {year}", "%b %d %Y").date()
                end = _dt.strptime(f"{m.group(1)} {m.group(3)} {year}", "%b %d %Y").date()
                return {start + timedelta(days=i) for i in range((end - start).days + 1)}
            except ValueError:
                return set()

        # Rozsah přes měsíce: "Mar 29-Apr 2 Sun-Thu"
        m = re.match(r"([A-Za-z]{3})\s+(\d{1,2})-([A-Za-z]{3})\s+(\d{1,2})\b", date_text.strip())
        if m:
            try:
                start = _dt.strptime(f"{m.group(1)} {m.group(2)} {year}", "%b %d %Y").date()
                end = _dt.strptime(f"{m.group(3)} {m.group(4)} {year}", "%b %d %Y").date()
                return {start + timedelta(days=i) for i in range((end - start).days + 1)}
            except ValueError:
                return set()

        return set()

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
        PDGA stránky mají výsledky po divizích v tabulkách.
        Každý hráč má odkaz na profil /player/{pdga_number}.

        Typická hlavička:
        Place | Name | PDGA# | Rating | Par | Rd1 | (rr) | Rd2 | (rr) | ... | Total
        """
        our_players = []
        current_div = None
        current_header = []

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

            # Parsování hlavičky tabulky
            if tag == "tr":
                cells = element.find_all(["td", "th"])
                texts = [c.get_text(strip=True).lower() for c in cells]
                if "place" in texts and "name" in texts:
                    current_header = texts
                    continue

                # Hledáme odkaz na hráčský profil
                for link in element.find_all("a", href=re.compile(r"/player/\d+")):
                    m = re.search(r"/player/(\d+)", link.get("href", ""))
                    if m and m.group(1) in self.pdga_set:
                        p = self.pdga_to_player[m.group(1)]
                        score, round_ratings = self._extract_score_and_ratings(
                            cells, current_header
                        )
                        our_players.append({
                            "first_name": p["first_name"],
                            "last_name": p["last_name"],
                            "cadg": p.get("cadg"),
                            "pdga": p["pdga"],
                            "role": p.get("role", ""),
                            "note": p.get("note", ""),
                            "place": self._extract_place(cells),
                            "division": current_div,
                            "score": score,
                            "round_ratings": round_ratings,
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
        for tag in ["CDGT", "NJDGT", "HDGT", "ADGL", "PCT", "PvDGT"]:
            if tag in name_upper:
                return tag

        return base_tier

    @staticmethod
    def _extract_score_and_ratings(cells, header: list) -> tuple:
        """
        Extract par-relative score and round ratings from a PDGA result row.

        Uses header column names to identify:
        - "par" column → score (e.g. "-28")
        - columns after "par" with round rating values (600-1100) → round_ratings
        Skips the "rating" column (player's PDGA rating) and "total" column.

        Returns
        -------
        tuple : (score: str, round_ratings: list[int])
        """
        score = ""
        round_ratings = []

        # Find key column indices from header
        par_idx = None
        rating_idx = None
        total_idx = None
        for i, h in enumerate(header):
            if h == "par":
                par_idx = i
            elif h == "rating":
                rating_idx = i
            elif h == "total":
                total_idx = i

        if par_idx is not None and par_idx < len(cells):
            score = cells[par_idx].get_text(strip=True)

        # Round ratings: cells after "par", skip "rating" and "total" columns
        # PDGA layout: ... Par | Rd1 score | Rd1 rating | Rd2 score | Rd2 rating | ... | Total
        # Round ratings are 600-1100 range values in odd-offset columns after Par
        skip = {rating_idx, total_idx, par_idx}
        start = (par_idx or 4) + 1
        for i in range(start, len(cells)):
            if i in skip:
                continue
            text = cells[i].get_text(strip=True)
            if re.match(r"^[6-9]\d{2}$|^1[01]\d{2}$", text):
                round_ratings.append(int(text))

        return score, round_ratings

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

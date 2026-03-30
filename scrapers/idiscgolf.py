"""
Scraper pro idiscgolf.cz – stahuje výsledky turnajů z uplynulého víkendu
a filtruje hráče Moravian Gators.

Poznámka: pokud idiscgolf.cz renderuje obsah přes JavaScript (React/Vue),
může být potřeba přepnout na Playwright. Viz komentáře níže.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from datetime import date
import logging
import time
import re
import unicodedata

logger = logging.getLogger(__name__)

BASE_URL = "https://idiscgolf.cz"

# Kategorie/divize v českém disc golfu
DIVISIONS = [
    "MPO", "FPO",
    "MA1", "MA2", "MA3", "MA4", "MA40", "MA50",
    "FA1", "FA2", "FA3", "FA4",
    "MP40", "MP50", "MP60",
    "FP40", "FP50",
    "MJ10", "MJ12", "MJ15", "MJ18",
    "FJ10", "FJ12", "FJ15", "FJ18",
]


def normalize(text: str) -> str:
    """Odstraní diakritiku a převede na lowercase pro fuzzy matching."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("utf-8").lower().strip()


class IDGScraper:
    def __init__(self, players: list):
        self.players = players

        # Lookup struktury pro rychlé hledání
        self.cadg_set = {str(p["cadg"]) for p in players if p.get("cadg")}
        self.cadg_to_player = {str(p["cadg"]): p for p in players if p.get("cadg")}
        self.pdga_set = {str(p["pdga"]) for p in players if p.get("pdga")}
        self.pdga_to_player = {str(p["pdga"]): p for p in players if p.get("pdga")}

        # Indexy pro hledání podle jména (s a bez diakritiky)
        # Hodnota je seznam hráčů (kvůli jmenovcům, např. otec/syn)
        self.name_to_players = {}
        self.norm_name_to_players = {}
        for p in players:
            full = f"{p['first_name']} {p['last_name']}"
            self.name_to_players.setdefault(full.lower(), []).append(p)
            self.norm_name_to_players.setdefault(normalize(full), []).append(p)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
        })

    # ------------------------------------------------------------------
    # Veřejné API
    # ------------------------------------------------------------------

    def get_weekend_results(self, saturday: date, sunday: date) -> list:
        """
        Vrátí seznam turnajů z uplynulého víkendu, kde byli naši hráči.
        Každý prvek: {name, date, id, url, our_players}
        """
        tournaments = self._find_weekend_tournaments(saturday, sunday)
        logger.info(f"idiscgolf: nalezeno {len(tournaments)} turnajů pro víkend {saturday}–{sunday}")

        results = []
        for t in tournaments:
            time.sleep(1)  # netlačíme server
            logger.info(f"  Kontroluji turnaj #{t['id']}: {t['name']}")
            our_players = self._get_our_players(t["id"])
            if our_players:
                results.append({
                    "name": t["name"],
                    "date": t["date"],
                    "id": t["id"],
                    "url": f"{BASE_URL}/turnaje/{t['id']}",
                    "our_players": our_players,
                    "source": "idiscgolf",
                })
        return results

    # ------------------------------------------------------------------
    # Hledání turnajů pro daný víkend
    # ------------------------------------------------------------------

    def _find_weekend_tournaments(self, saturday: date, sunday: date) -> list:
        """Pokusí se najít turnaje z daného víkendu na stránce přehledu."""
        for url in [f"{BASE_URL}/turnaje", f"{BASE_URL}/prehled-turnaju"]:
            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                tournaments = self._parse_tournament_list(resp.text, saturday, sunday)
                if tournaments:
                    return tournaments
            except Exception as e:
                logger.warning(f"Nepodařilo se načíst {url}: {e}")

        # Fallback: prohledáme nedávné ID (posledních ~20 turnajů)
        logger.warning("Přehled turnajů nedostupný, zkouším prohledat nejnovější ID…")
        return self._probe_recent_ids(saturday, sunday)

    def _parse_tournament_list(self, html: str, saturday: date, sunday: date) -> list:
        """Parsuje HTML stránky s přehledem turnajů a filtruje víkendové."""
        soup = BeautifulSoup(html, "html.parser")
        tournaments = []

        # Datum formáty, které hledáme
        date_patterns = [
            saturday.strftime("%d.%m.%Y"),
            sunday.strftime("%d.%m.%Y"),
            saturday.strftime("%-d.%-m.%Y"),   # bez leading zeros
            sunday.strftime("%-d.%-m.%Y"),
            saturday.strftime("%Y-%m-%d"),
            sunday.strftime("%Y-%m-%d"),
        ]

        # Hledáme linky na konkrétní turnaje (datum bývá v jiné buňce řádku)
        for link in soup.find_all("a", href=re.compile(r"/turnaje/\d+")):
            href = link.get("href", "")
            m = re.search(r"/turnaje/(\d+)", href)
            if not m:
                continue

            # Kontext: celý řádek tabulky (tr) nebo rodičovský element
            tr = link.find_parent("tr")
            context = (tr or link.parent or link).get_text(" ", strip=True)

            if any(dp in context for dp in date_patterns):
                tournaments.append({
                    "id": int(m.group(1)),
                    "name": link.get_text(strip=True) or f"Turnaj #{m.group(1)}",
                    "date": self._extract_date_from_text(context),
                })

        # Deduplikace podle ID
        seen = set()
        unique = []
        for t in tournaments:
            if t["id"] not in seen:
                seen.add(t["id"])
                unique.append(t)
        return unique

    def _probe_recent_ids(self, saturday: date, sunday: date, probe_count: int = 25) -> list:
        """
        Fallback: stáhne přehled turnajů a najde poslední ID, pak zkontroluje
        několik turnajů zpětně. Funguje i pokud přehled nenačte datum.
        """
        # Zkusíme nejdřív zjistit nejvyšší existující ID z přehledu
        max_id = self._get_latest_tournament_id()
        if not max_id:
            logger.error("Nepodařilo se zjistit poslední ID turnaje.")
            return []

        tournaments = []
        for tid in range(max_id, max_id - probe_count, -1):
            time.sleep(0.5)
            t = self._get_tournament_meta(tid)
            if t and self._is_weekend_date(t.get("date", ""), saturday, sunday):
                tournaments.append(t)

        return tournaments

    def _get_latest_tournament_id(self) -> int | None:
        """Zjistí ID posledního turnaje z přehledové stránky."""
        try:
            resp = self.session.get(f"{BASE_URL}/turnaje", timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            ids = []
            for link in soup.find_all("a", href=re.compile(r"/turnaje/\d+")):
                m = re.search(r"/turnaje/(\d+)", link["href"])
                if m:
                    ids.append(int(m.group(1)))
            return max(ids) if ids else None
        except Exception as e:
            logger.error(f"_get_latest_tournament_id failed: {e}")
            return None

    def _get_tournament_meta(self, tid: int) -> dict | None:
        """Načte stránku turnaje a vrátí základní metadata (bez parsování výsledků)."""
        try:
            resp = self.session.get(f"{BASE_URL}/turnaje/{tid}", timeout=15)
            if resp.status_code == 404:
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            name = (soup.find("h1") or soup.find("title") or soup.find("h2"))
            name_text = name.get_text(strip=True) if name else f"Turnaj #{tid}"
            date_text = self._extract_date_from_text(soup.get_text(" "))
            return {"id": tid, "name": name_text, "date": date_text}
        except Exception as e:
            logger.warning(f"Turnaj #{tid}: {e}")
            return None

    # ------------------------------------------------------------------
    # Parsování výsledků konkrétního turnaje
    # ------------------------------------------------------------------

    def _get_our_players(self, tid: int) -> list:
        """Stáhne stránku turnaje a vrátí naše hráče s výsledky."""
        url = f"{BASE_URL}/turnaje/{tid}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            return self._parse_results(soup)
        except Exception as e:
            logger.error(f"Nepodařilo se načíst turnaj #{tid}: {e}")
            return []

    def _parse_results(self, soup: BeautifulSoup) -> list:
        """
        Parsuje stránku výsledků:
        1. Registrační tabulka → mapa hráč→kategorie
        2. Výsledkové tabulky s detekcí sloupců ČADG/PDGA#
        3. Full-text match jako fallback
        """
        # --- Krok 1: Sestav mapu z registrační tabulky ---
        reg_map = self._build_registration_map(soup)

        our_players = []
        current_div = None
        cadg_col_idx = None
        pdga_col_idx = None
        is_registration_table = False

        # --- Krok 2: procházení DOM stromem ---
        for element in soup.find_all(True):
            tag = element.name.lower()

            # Detekce hlavičky divize (h2, h3, h4, th nebo div s textem divize)
            if tag in ("h2", "h3", "h4", "th", "div", "span"):
                text = element.get_text(strip=True).upper()
                for div in DIVISIONS:
                    if text == div or text.startswith(div + " ") or text.startswith(div + "\n"):
                        current_div = div
                        break

            # Řádky tabulky
            if tag == "tr":
                cells = element.find_all(["td", "th"])
                if len(cells) < 2:
                    continue

                # Detekce hlavičkového řádku
                header_texts = [c.get_text(strip=True).upper() for c in cells]
                if "#" in header_texts and ("HRÁČ" in header_texts or "HRAC" in header_texts):
                    # Registrační tabulka má sloupce Status, Zaplaceno, Klub apod.
                    is_registration_table = (
                        "STATUS" in header_texts or "ZAPLACENO" in header_texts
                        or "KLUB" in header_texts
                    )
                    cadg_col_idx = next(
                        (i for i, h in enumerate(header_texts) if h in ("ČADG", "CADG")),
                        None,
                    )
                    pdga_col_idx = next(
                        (i for i, h in enumerate(header_texts) if h in ("PDGA#", "PDGA")),
                        None,
                    )
                    continue

                # Přeskočíme registrační tabulku – nechceme z ní brát výsledky
                if is_registration_table:
                    continue

                # Zkus detekovat divizi z prvního sloupce / rowspanu
                row_text = " ".join(c.get_text(strip=True) for c in cells)
                for div in DIVISIONS:
                    if re.search(rf"\b{div}\b", row_text.upper()):
                        current_div = div
                        break

                player = self._match_player_in_cells(cells, cadg_col_idx, pdga_col_idx)
                if player:
                    entry = dict(player)
                    player_key = f"{entry['first_name']} {entry['last_name']}".lower()
                    matched_via = entry.pop("_matched_via", "name")

                    # Ověření identity: pokud hráč není registrován jako MGNJ,
                    # zkontrolujeme shodu ČADG čísla z registrace s naší DB
                    reg_info = reg_map.get(player_key)
                    if not reg_info:
                        # Zkus varianty se suffixem ml./st.
                        note = (entry.get("note") or "").lower()
                        if "mladší" in note:
                            reg_info = reg_map.get(f"{player_key} ml.")
                        elif "starší" in note:
                            reg_info = reg_map.get(f"{player_key} st.")

                    if reg_info:
                        klub = reg_info.get("klub", "")
                        reg_cadg = reg_info.get("cadg", "")
                        our_cadg = str(entry.get("cadg", ""))
                        is_mgnj = "MGNJ" in klub

                        # Pokud není MGNJ a ČADG se neshoduje → jiná osoba, přeskočit
                        if not is_mgnj and reg_cadg and our_cadg and reg_cadg != our_cadg:
                            logger.debug(
                                f"Přeskakuji {player_key}: klub={klub}, "
                                f"ČADG turnaj={reg_cadg} ≠ DB={our_cadg}"
                            )
                            continue

                        # Pokud není MGNJ, ČADG v registraci chybí a match byl
                        # jen podle jména (ne ČADG/PDGA sloupce) → nelze ověřit
                        if not is_mgnj and not reg_cadg and matched_via == "name":
                            logger.debug(
                                f"Přeskakuji {player_key}: klub={klub}, "
                                f"ČADG v registraci chybí, nelze ověřit identitu"
                            )
                            continue

                    # Hráč s jmenovcem (ml./st.) matchnutý jen jménem bez
                    # ověření identity → raději přeskočit
                    if not reg_info and matched_via == "name":
                        note = (entry.get("note") or "").lower()
                        if "mladší" in note or "starší" in note:
                            logger.debug(
                                f"Přeskakuji {player_key}: jmenovec bez "
                                f"registrace, nelze ověřit identitu"
                            )
                            continue

                    # Divize z registrační mapy nebo z DOM struktury
                    div_from_map = reg_info.get("kategorie") if reg_info else None
                    entry["division"] = div_from_map or current_div
                    entry["place"] = self._extract_place(cells)
                    entry["score"] = self._extract_score(cells)
                    our_players.append(entry)

        # Deduplikace – preferujeme záznam se skóre +/- (výsledky)
        seen = {}
        for entry in our_players:
            key = entry.get("cadg") or f"{entry['first_name']}_{entry['last_name']}"
            if key not in seen:
                seen[key] = entry
            else:
                score = str(entry.get("score", ""))
                if score.startswith("+") or score.startswith("-"):
                    seen[key] = entry
        our_players = list(seen.values())

        # --- Krok 3: full-text fallback ---
        if not our_players:
            our_players = self._fulltext_fallback(soup)

        return our_players

    def _build_registration_map(self, soup: BeautifulSoup) -> dict:
        """
        Najde registrační tabulku a vrátí mapu {jméno_lower: {kategorie, cadg, klub}}.
        Slouží pro:
        1. Zjištění divize/kategorie hráče
        2. Ověření identity hráče (ČADG číslo) u jmenovců / nečlenů MGNJ
        """
        reg_map = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            header_cells = rows[0].find_all(["td", "th"])
            headers = [c.get_text(strip=True).upper() for c in header_cells]
            if "HRÁČ" not in headers or "KATEGORIE" not in headers:
                continue
            name_idx = headers.index("HRÁČ")
            cat_idx = headers.index("KATEGORIE")
            cadg_idx = next((i for i, h in enumerate(headers) if h in ("ČADG", "CADG")), None)
            klub_idx = next((i for i, h in enumerate(headers) if h == "KLUB"), None)
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(name_idx, cat_idx):
                    continue
                name = cells[name_idx].get_text(strip=True)
                name = re.sub(r'\s*"[^"]*"\s*', ' ', name).strip()
                name = re.sub(r'\s+', ' ', name)
                category = cells[cat_idx].get_text(strip=True)
                cadg = cells[cadg_idx].get_text(strip=True) if cadg_idx and cadg_idx < len(cells) else ""
                klub = cells[klub_idx].get_text(strip=True) if klub_idx and klub_idx < len(cells) else ""
                reg_map[name.lower()] = {
                    "kategorie": category,
                    "cadg": cadg,
                    "klub": klub.upper(),
                }
        return reg_map

    def _match_player_in_cells(self, cells, cadg_col_idx=None, pdga_col_idx=None) -> dict | None:
        """Pokusí se v buňkách řádku najít shodu s naším hráčem."""
        cell_texts = [c.get_text(strip=True) for c in cells]
        row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
        row_norm = normalize(row_text)

        # Priorita 1: ČADG číslo – jen ve sloupci ČADG (pokud známe index)
        if cadg_col_idx is not None and cadg_col_idx < len(cell_texts):
            cadg_val = cell_texts[cadg_col_idx].strip()
            if cadg_val and cadg_val in self.cadg_set:
                p = self.cadg_to_player[cadg_val]
                result = self._player_result_base(p)
                result["_matched_via"] = "cadg"
                return result

        # Priorita 1b: PDGA číslo – jen ve sloupci PDGA# (pokud známe index)
        if pdga_col_idx is not None and pdga_col_idx < len(cell_texts):
            pdga_val = cell_texts[pdga_col_idx].strip()
            if pdga_val and pdga_val in self.pdga_set:
                p = self.pdga_to_player[pdga_val]
                result = self._player_result_base(p)
                result["_matched_via"] = "pdga"
                return result

        # Priorita 2: plné jméno (s diakritikou) – word boundaries
        for name, players_list in self.name_to_players.items():
            if re.search(rf"\b{re.escape(name)}\b", row_text, re.IGNORECASE):
                p = self._disambiguate(players_list, cell_texts, pdga_col_idx, row_text)
                result = self._player_result_base(p)
                result["_matched_via"] = "name"
                return result

        # Priorita 3: normalizované jméno (bez diakritiky) – word boundaries
        for norm_name, players_list in self.norm_name_to_players.items():
            if re.search(rf"\b{re.escape(norm_name)}\b", row_norm):
                p = self._disambiguate(players_list, cell_texts, pdga_col_idx, row_text)
                result = self._player_result_base(p)
                result["_matched_via"] = "name"
                return result

        return None

    def _fulltext_fallback(self, soup: BeautifulSoup) -> list:
        """Prohledá celý text stránky pro naše hráče (word boundaries)."""
        page_text = soup.get_text(" ")
        page_norm = normalize(page_text)
        found = []
        seen_cadg = set()

        for norm_name, players_list in self.norm_name_to_players.items():
            if not re.search(rf"\b{re.escape(norm_name)}\b", page_norm):
                continue
            for p in players_list:
                cadg_key = p.get("cadg")
                if cadg_key in seen_cadg:
                    continue
                found.append(self._player_result_base(p))
                seen_cadg.add(cadg_key)

        return found

    # ------------------------------------------------------------------
    # Pomocné metody
    # ------------------------------------------------------------------

    def _disambiguate(self, players_list: list, cell_texts: list,
                      pdga_col_idx: int | None, row_text: str) -> dict:
        """Rozliší jmenovce (např. otec/syn) podle PDGA# nebo suffixu ml./st."""
        if len(players_list) == 1:
            return players_list[0]

        # Zkus rozlišit podle PDGA# v buňce
        if pdga_col_idx is not None and pdga_col_idx < len(cell_texts):
            pdga_val = cell_texts[pdga_col_idx].strip()
            if pdga_val:
                for p in players_list:
                    if str(p.get("pdga", "")) == pdga_val:
                        return p

        # Zkus rozlišit podle suffixu ml./st./mladší/starší v textu řádku
        row_lower = row_text.lower()
        for p in players_list:
            note = (p.get("note") or "").lower()
            if "mladší" in note or "ml." in note:
                if "ml." in row_lower or "mladší" in row_lower:
                    return p
            if "starší" in note or "st." in note:
                if "st." in row_lower or "starší" in row_lower:
                    return p

        # Fallback: vrať prvního
        return players_list[0]

    @staticmethod
    def _player_result_base(p: dict) -> dict:
        return {
            "first_name": p["first_name"],
            "last_name": p["last_name"],
            "cadg": p.get("cadg"),
            "pdga": p.get("pdga"),
            "role": p.get("role", ""),
            "note": p.get("note", ""),
            "place": None,
            "division": None,
            "score": "",
        }

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
    def _extract_date_from_text(text: str) -> str:
        m = re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", text)
        return m.group(0) if m else ""

    @staticmethod
    def _is_weekend_date(date_str: str, saturday: date, sunday: date) -> bool:
        if not date_str:
            return False
        for d in [saturday, sunday]:
            for fmt in ["%d.%m.%Y", "%-d.%-m.%Y"]:
                try:
                    if date_str == d.strftime(fmt):
                        return True
                except ValueError:
                    pass
        return False

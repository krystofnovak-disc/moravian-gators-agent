"""
Accumulator – správa kumulativních výsledků turnajů Moravian Gators za rok.

Čte a zapisuje JSON soubory v adresáři data/{year}.json.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)


def _normalize_date(date_str: str) -> str:
    """Convert Czech date format (DD.MM.YYYY) to ISO (YYYY-MM-DD) if needed."""
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", date_str.strip())
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return date_str  # already ISO or unknown format


def _normalize_name(name: str) -> str:
    """Strip diacritics, lowercase, remove non-alphanumeric chars (except spaces)."""
    nfkd = unicodedata.normalize("NFD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9 ]", "", ascii_only.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _name_similarity(a: str, b: str) -> float:
    """Return similarity ratio (0-1) between two normalized strings."""
    return SequenceMatcher(None, a, b).ratio()


def _names_match(name_a: str, name_b: str) -> bool:
    """
    Two tournament names match if their normalized forms have >80% similarity
    or one contains the other.
    """
    na = _normalize_name(name_a)
    nb = _normalize_name(name_b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    return _name_similarity(na, nb) > 0.80


def _empty_data() -> dict:
    """Return a fresh empty data structure."""
    return {"tournaments": [], "ratings": {}}


class Accumulator:
    """Manages cumulative yearly tournament results stored as JSON."""

    def __init__(self, year: int | None = None):
        if year is None:
            year = date.today().year
        self.year = year
        self.data_dir = Path(__file__).parent / "data"
        self.file = self.data_dir / f"{year}.json"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Load existing data from disk, or return an empty structure."""
        if not self.file.exists():
            logger.info("Datový soubor %s neexistuje, začínám s prázdnou strukturou.", self.file)
            return _empty_data()
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Načteno %d turnajů z %s.", len(data.get("tournaments", [])), self.file)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Chyba při čtení %s: %s – začínám s prázdnou strukturou.", self.file, exc)
            return _empty_data()

    def save(self, data: dict) -> None:
        """Write data dict to the JSON file, creating directories as needed."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.file)
            logger.info("Uloženo %d turnajů do %s.", len(data.get("tournaments", [])), self.file)
        except OSError:
            logger.exception("Nepodařilo se uložit %s.", self.file)
            raise

    # ------------------------------------------------------------------
    # Tournament accumulation
    # ------------------------------------------------------------------

    def add_tournaments(self, tournaments: list, data: dict | None = None) -> dict:
        """
        Merge new tournament results into cumulative data.

        Parameters
        ----------
        tournaments : list
            Items from scrapers, each having keys:
            name, date, url, source, our_players, and optionally tier / id.
        data : dict or None
            Existing cumulative data. Loaded from disk when *None*.

        Returns
        -------
        dict  – updated data structure (not yet saved to disk).
        """
        if data is None:
            data = self.load()

        existing = data.setdefault("tournaments", [])

        added = 0
        replaced = 0

        for t in tournaments:
            t_name = t.get("name", "")
            t_date = t.get("date", "")
            t_source = t.get("source", "")

            # Look for a duplicate among existing tournaments
            match_idx = self._find_matching_tournament(existing, t_name, t_date)

            if match_idx is not None:
                existing_t = existing[match_idx]
                existing_source = existing_t.get("source") or self._infer_source(existing_t)

                # PDGA is the preferred source (more detailed data, round ratings)
                if existing_source == "pdga":
                    logger.debug(
                        "Turnaj '%s' (%s) již existuje z PDGA – přeskakuji.",
                        t_name, t_date,
                    )
                    continue
                if t_source == "pdga":
                    logger.info(
                        "Nahrazuji turnaj '%s' (%s) daty z PDGA.",
                        t_name, t_date,
                    )
                    existing[match_idx] = self._convert_tournament(t)
                    replaced += 1
                    continue
                # Both from same non-pdga source – skip duplicate
                logger.debug(
                    "Turnaj '%s' (%s) již existuje – přeskakuji.",
                    t_name, t_date,
                )
                continue

            # No match – add new tournament
            existing.append(self._convert_tournament(t))
            added += 1

        # Sort tournaments by date
        existing.sort(key=lambda x: x.get("date", ""))

        logger.info("Přidáno %d nových turnajů, nahrazeno %d.", added, replaced)
        return data

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    def update_ratings(self, ratings_update: dict, month: str, data: dict | None = None) -> dict:
        """
        Update player ratings for *month* (``"YYYY-MM"``).

        Parameters
        ----------
        ratings_update : dict
            ``{cadg_str: {"name": str, "pdga_rating": int|None, "idg_rating": int|None}}``
        month : str
            e.g. ``"2026-03"``

        Returns
        -------
        dict – updated data structure.
        """
        if data is None:
            data = self.load()

        ratings = data.setdefault("ratings", {})
        month_ratings = ratings.setdefault(month, {})
        month_ratings.update(ratings_update)

        logger.info("Aktualizováno %d hráčů pro měsíc %s.", len(ratings_update), month)
        return data

    def get_latest_ratings(self, data: dict | None = None) -> dict:
        """Return the ratings dict from the most recent month, or ``{}``."""
        if data is None:
            data = self.load()

        ratings = data.get("ratings", {})
        if not ratings:
            return {}

        latest_month = max(ratings.keys())
        return ratings[latest_month]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_matching_tournament(self, existing: list, name: str, t_date: str) -> int | None:
        """Return the index of a matching existing tournament, or None."""
        norm_date = _normalize_date(t_date)
        for idx, et in enumerate(existing):
            if _normalize_date(et.get("date", "")) != norm_date:
                continue
            if _names_match(et.get("name", ""), name):
                return idx
        return None

    @staticmethod
    def _infer_source(tournament: dict) -> str:
        """Guess the source from the URL when no explicit source field exists."""
        url_idg = tournament.get("url_idg") or ""
        url_pdga = tournament.get("url_pdga") or ""
        if url_idg:
            return "idiscgolf"
        if url_pdga:
            return "pdga"
        return ""

    @staticmethod
    def _convert_tournament(t: dict) -> dict:
        """
        Convert a scraper-format tournament dict to the storage format.

        Scraper format keys: name, date, url, source, our_players, tier, id
        Storage format keys: name, date, url_idg, url_pdga, tier, source, results
        """
        source = t.get("source", "")
        url = t.get("url", "")

        url_idg = url if source == "idiscgolf" else None
        url_pdga = url if source == "pdga" else None

        results = []
        for p in t.get("our_players", []):
            results.append({
                "player_name": " ".join(
                    filter(None, [p.get("first_name", ""), p.get("last_name", "")])
                ) or p.get("player_name", ""),
                "cadg": p.get("cadg"),
                "pdga": p.get("pdga"),
                "division": p.get("division"),
                "place": p.get("place"),
                "score": p.get("score", ""),
                "round_ratings": p.get("round_ratings", []),
            })

        return {
            "name": t.get("name", ""),
            "date": _normalize_date(t.get("date", "")),
            "url_idg": url_idg,
            "url_pdga": url_pdga,
            "tier": t.get("tier"),
            "source": source,
            "results": results,
        }

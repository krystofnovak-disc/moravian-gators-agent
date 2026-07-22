#!/usr/bin/env python3
"""
Doplní "idg_id" (nové iDG user ID z ceskydiscgolf.cz) hráčům v
config/players.json, kterým chybí.

Nové iDG API má u každého uživatele pole "old_id", které odpovídá
starému CADG číslu. Skript hráče najde přes ?search=Jméno Příjmení
a vybere záznam, kde old_id == jeho cadg (spolehlivě řeší i jmenovce).

Použití:
  python resolve_idg_ids.py            # doplní jen chybějící idg_id
  python resolve_idg_ids.py --all      # přeověří/přepíše všechny
  python resolve_idg_ids.py --dry-run  # jen vypíše, nezapisuje

Zachovává kompaktní formát players.json (jeden hráč na řádek).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_USERS = "https://api.ceskydiscgolf.cz/api/user/users/"
KEY_ORDER = ["first_name", "last_name", "cadg", "pdga", "idg_id", "role", "note"]


def search_users(name: str) -> list:
    url = API_USERS + "?search=" + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("results", [])


def resolve(player: dict) -> int | None:
    """Vrátí nové idg_id pro hráče (old_id == cadg), nebo None."""
    name = f"{player['first_name']} {player['last_name']}"
    try:
        results = search_users(name)
    except Exception as e:
        print(f"  ! chyba při hledání {name}: {e}", file=sys.stderr)
        return None
    hit = next((u for u in results if u.get("old_id") == player.get("cadg")), None)
    return hit["id"] if hit else None


def dump_compact(players: list) -> str:
    lines = ["["]
    for i, p in enumerate(players):
        keys = [k for k in KEY_ORDER if k in p] + [k for k in p if k not in KEY_ORDER]
        parts = [json.dumps(k, ensure_ascii=False) + ": " + json.dumps(p[k], ensure_ascii=False) for k in keys]
        lines.append("  {" + ", ".join(parts) + "}" + ("," if i < len(players) - 1 else ""))
    lines.append("]")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Doplní idg_id do players.json")
    ap.add_argument("--club", default="mgnj", help="Klub z config/clubs.json (default: mgnj)")
    ap.add_argument("--all", action="store_true", help="Přeověřit i hráče, kteří idg_id už mají")
    ap.add_argument("--dry-run", action="store_true", help="Jen vypsat, nezapisovat")
    args = ap.parse_args()

    from clubs import get_club
    players_path = get_club(args.club)["players_path"]

    players = json.loads(players_path.read_text(encoding="utf-8"))

    todo = [p for p in players if args.all or not p.get("idg_id")]
    if not todo:
        print("Všichni hráči už mají idg_id. Nic k doplnění.")
        return

    print(f"Řeším {len(todo)} hráčů…")
    resolved, unresolved = 0, []
    for p in todo:
        new_id = resolve(p)
        name = f"{p['first_name']} {p['last_name']}"
        if new_id:
            old = p.get("idg_id")
            if old and old != new_id:
                print(f"  ~ {name}: idg_id {old} → {new_id}")
            else:
                print(f"  + {name} (cadg={p['cadg']}) → idg_id={new_id}")
            p["idg_id"] = new_id
            resolved += 1
        else:
            unresolved.append(f"{name} (cadg={p['cadg']})")
        time.sleep(0.15)

    print(f"\nVyřešeno: {resolved}/{len(todo)}")
    if unresolved:
        print(f"Nevyřešeno ({len(unresolved)}) – zkontroluj ručně:")
        for u in unresolved:
            print(f"  - {u}")

    if args.dry_run:
        print("\n--dry-run: nezapisuji.")
        return
    players_path.write_text(dump_compact(players), encoding="utf-8")
    print(f"\nZapsáno do {players_path}.")


if __name__ == "__main__":
    main()

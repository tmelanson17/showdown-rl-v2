"""Scrape base stats from pokemondb.net/pokedex by Pokémon name.

Names follow Showdown convention: 'Garchomp', 'Gengar-Mega', 'Charizard-Mega-X'.
The name is split at the first hyphen whose right-hand part begins with a
recognized form keyword (Mega, Alola, Galar, etc.).  Naturally hyphenated
species names like Ho-Oh, Jangmo-o, and Kommo-o are therefore left intact.
"""

import sys
import os

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
from datatypes import Stat

_BASE_URL = "https://pokemondb.net/pokedex"

_STAT_NAME_MAP = {
    "HP": "hp",
    "Attack": "atk",
    "Defense": "defn",
    "Sp. Atk": "spatk",
    "Sp. Def": "spdef",
    "Speed": "speed",
}

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "Mozilla/5.0"

# Hyphen-separated segments that mark the start of a form suffix.
_FORM_STARTERS = {
    "mega",
    "gmax",
    "gigantamax",
    "alola",
    "alolan",
    "galar",
    "galarian",
    "hisui",
    "hisuian",
    "paldea",
    "paldean",
    "origin",
    "therian",
    "primal",
    "crowned",
    "eternamax",
    "rapid",
    "low",
    "dusk",
    "dawn",
    "midday",
    "midnight",
}


def _parse_name(name: str) -> tuple[str, str | None]:
    """Return (url_slug, form_keyword) from a Showdown-style name.

    Scans left-to-right for the first segment that is a known form keyword,
    so naturally hyphenated names (Ho-Oh, Kommo-o) are not split.
    """
    parts = name.split("-")
    for i in range(1, len(parts)):
        if parts[i].lower() in _FORM_STARTERS:
            base = "-".join(parts[:i]).lower()
            form = " ".join(parts[i:])
            return base, form
    return name.lower(), None


def _stats_from_table(table) -> dict[str, int]:
    stats: dict[str, int] = {}
    for row in table.select("tr"):
        th = row.find("th")
        if th is None:
            continue
        label = th.get_text(strip=True)
        key = _STAT_NAME_MAP.get(label)
        if key is None:
            continue
        td = row.find("td", class_="cell-num")
        if td is None:
            continue
        stats[key] = int(td.get_text(strip=True))
    return stats


def _find_stats_in_panel(panel) -> Stat:
    """Search all vitals-tables in a panel until we find one with all 6 stats."""
    for table in panel.select("table.vitals-table"):
        stats = _stats_from_table(table)
        if len(stats) == 6:
            return Stat(**stats)
    raise ValueError("Base stats table not found in panel")


def get_base_stats(name: str) -> Stat:
    """Return base Stat for the given Pokémon name (e.g. 'Garchomp', 'Gengar-Mega')."""
    base, form = _parse_name(name)
    url = f"{_BASE_URL}/{base}"
    resp = _SESSION.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tabset = soup.select_one("div.tabset-basics")

    if form is None or tabset is None:
        panel = tabset or soup
        return _find_stats_in_panel(panel)

    tabs = tabset.select("a.sv-tabs-tab")
    panels = tabset.select("div.sv-tabs-panel")
    if len(tabs) != len(panels):
        raise ValueError(
            f"Tab/panel count mismatch for {name}: {len(tabs)} tabs, {len(panels)} panels"
        )

    form_words = set(form.lower().split())
    for tab, panel in zip(tabs, panels):
        tab_words = set(tab.get_text(strip=True).lower().split())
        if form_words.issubset(tab_words):
            return _find_stats_in_panel(panel)

    available = [t.get_text(strip=True) for t in tabs]
    raise ValueError(f"Form '{form}' not found for {name}. Available tabs: {available}")


if __name__ == "__main__":
    tests = [
        ("Garchomp", Stat(hp=108, atk=130, defn=95, spatk=80, spdef=85, speed=102)),
        ("Archaludon", Stat(hp=90, atk=105, defn=130, spatk=125, spdef=65, speed=85)),
        ("Gengar-Mega", Stat(hp=60, atk=65, defn=80, spatk=170, spdef=95, speed=130)),
        (
            "Charizard-Mega-Y",
            Stat(hp=78, atk=104, defn=78, spatk=159, spdef=115, speed=100),
        ),
        ("Kommo-o", Stat(hp=75, atk=110, defn=125, spatk=100, spdef=105, speed=85)),
        ("Floette-Mega", Stat(hp=74, atk=85, defn=87, spatk=155, spdef=148, speed=102)),
    ]

    all_passed = True
    for pokemon_name, expected in tests:
        try:
            result = get_base_stats(pokemon_name)
            ok = result == expected
            status = "PASS" if ok else "FAIL"
            print(f"{status} {pokemon_name}")
            if not ok:
                print(f"  expected: {expected}")
                print(f"  got:      {result}")
                all_passed = False
        except Exception as exc:
            print(f"ERROR {pokemon_name}: {exc}")
            all_passed = False

    sys.exit(0 if all_passed else 1)

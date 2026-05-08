"""
Estimate the offensive power of the top 100 VGC Pokemon by calculating the
average damage each of their top moves deals against a neutral Mew
(??? type, no EVs, Docile nature, level 50) using the Showdown calculator.

For each (Pokemon, ability, move) triple, three investment modes are evaluated:
  Uninvested – 0 EVs, neutral nature
  Invested   – 252 EVs in the relevant offensive stat, neutral nature
  Max        – 252 EVs in the relevant offensive stat + boosting nature

Abilities come from the same Smogon usage stats that produced the move list,
so only abilities that actually appeared in tournament play are tested.
Moves with scaled power (e.g. Last Respects) are expanded into KO-tier
variants automatically.

EXTRA_FIELD_CONTEXTS lets you specify additional weather/terrain conditions to
test for specific Pokemon (e.g. Typhlosion-Hisui in Sun set by a teammate).
These are tested alongside the normal ability-derived conditions.

Output: power_levels.csv sorted by average damage percentage, min → max.
"""

import csv
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
INPUT_JSON = SCRIPT_DIR / "champions_top100_doubles_1500.json"
INPUT_CSV  = SCRIPT_DIR / "champions_top100_doubles_1500.csv"
OUTPUT_CSV = SCRIPT_DIR / "power_levels.csv"
CALC_SCRIPT = SCRIPT_DIR / "showdown_calc.js"
ABILITIES_CACHE = SCRIPT_DIR / "abilities_cache.json"

# Abilities that implicitly set a weather or terrain condition, mirroring the
# Node.js wrapper's ABILITY_TO_WEATHER / ABILITY_TO_TERRAIN tables.  Used here
# only to populate the "weather" column in the output CSV.
ABILITY_TO_WEATHER: dict[str, str] = {
    "Drought": "Sun",
    "Desolate Land": "Sun",
    "Drizzle": "Rain",
    "Primordial Sea": "Rain",
    "Sand Stream": "Sand",
    "Snow Warning": "Snow",
    "Orichalcum Pulse": "Sun",
}
ABILITY_TO_TERRAIN: dict[str, str] = {
    "Hadron Engine": "Electric",
    "Electric Surge": "Electric",
    "Grassy Surge": "Grassy",
    "Misty Surge": "Misty",
    "Psychic Surge": "Psychic",
}

# Extra field conditions to test for specific Pokemon beyond what their own
# ability sets.  Each entry is a dict with optional "weather" and/or "terrain"
# keys.  Every listed context is run with every one of the Pokemon's abilities.
EXTRA_FIELD_CONTEXTS: dict[str, list[dict[str, str]]] = {
    "Typhlosion-Hisui": [{"weather": "Sun"}],
}


# ---------------------------------------------------------------------------
# Ability data from Smogon moveset stats
# ---------------------------------------------------------------------------

def _fetch_moveset_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "offensive-power-estimator/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_abilities(text: str) -> dict[str, list[str]]:
    """Return {pokemon_name: [ability, ...]} extracted from Smogon moveset text.

    Uses the same block-splitting logic as champions_top100.py: blocks are
    delimited by lines matching /^\\+-+\\+$/.  Each block is classified by
    its first content line.
    """
    SECTION_HEADERS = {
        "Abilities", "Items", "Moves", "Spreads", "Teammates",
        "Checks and Counters", "Tera Types", "Happiness",
    }

    # Split file into blocks (content between +---+ delimiters).
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*\+-+\+\s*$", line):
            if current:
                blocks.append(current)
                current = []
        else:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                stripped = stripped[1:-1].strip()
            if stripped:
                current.append(stripped)
    if current:
        blocks.append(current)

    abilities: dict[str, list[str]] = {}
    current_name: str | None = None

    for block in blocks:
        if not block:
            continue
        first = block[0]

        if first.startswith("Raw count:"):
            continue  # stats block – skip

        if first in SECTION_HEADERS:
            if first != "Abilities" or current_name is None:
                continue
            for entry in block[1:]:
                m = re.match(r"^(.+?)\s+([\d.]+)\s*%\s*$", entry)
                if m:
                    name = m.group(1).strip()
                    if name != "Other":
                        abilities.setdefault(current_name, []).append(name)
            continue

        # Single-line block → new Pokemon name
        if len(block) == 1:
            current_name = first

    return abilities


def fetch_top_abilities() -> dict[str, list[str]]:
    """Return {pokemon_name: [ability, ...]} with caching."""
    if ABILITIES_CACHE.exists():
        with open(ABILITIES_CACHE, encoding="utf-8") as fh:
            return json.load(fh)

    with open(INPUT_JSON, encoding="utf-8") as fh:
        meta = json.load(fh)["metadata"]
    moveset_url = meta["moveset_url"]

    print(f"Fetching ability data from {moveset_url} …")
    text = _fetch_moveset_text(moveset_url)
    abilities = _parse_abilities(text)

    with open(ABILITIES_CACHE, "w", encoding="utf-8") as fh:
        json.dump(abilities, fh, indent=2)

    return abilities


# ---------------------------------------------------------------------------
# Damage calculation via Node.js wrapper
# ---------------------------------------------------------------------------

def _read_csv() -> list[dict]:
    with open(INPUT_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _extract_moves(row: dict) -> list[str]:
    moves = []
    for i in range(1, 11):
        name = row.get(f"move_{i}", "").strip()
        if name and name != "Other":
            moves.append(name)
    return moves


def _call_calc(
    pokemon_name: str,
    moves: list[str],
    ability: str,
    weather: str = "",
    terrain: str = "",
) -> dict:
    payload: dict = {"pokemon_name": pokemon_name, "moves": moves, "ability": ability}
    if weather:
        payload["weather"] = weather
    if terrain:
        payload["terrain"] = terrain

    result = subprocess.run(
        ["node", str(CALC_SCRIPT), json.dumps(payload)],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
    )
    if result.returncode != 0:
        print(
            f"  [warn] calc error for {pokemon_name} / {ability}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"  [warn] bad JSON from calc for {pokemon_name}: {exc}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    rows = _read_csv()
    print(f"Loaded {len(rows)} Pokemon from {INPUT_CSV.name}")

    top_abilities = fetch_top_abilities()

    power_set: list[tuple[float, dict]] = []

    for row in rows:
        pokemon_name = row["pokemon"]
        moves = _extract_moves(row)
        if not moves:
            continue

        abilities = top_abilities.get(pokemon_name, [])
        if not abilities:
            # Fall back to calc species default when stats have no ability data.
            abilities = [None]

        print(f"  {pokemon_name} ({len(abilities)} abilities, {len(moves)} moves)…",
              end=" ", flush=True)
        count = 0

        # Build the list of (ability, weather_override, terrain_override) contexts
        # to test.  Start with each usage-stat ability at its natural conditions,
        # then append any extra field contexts defined in EXTRA_FIELD_CONTEXTS.
        contexts: list[tuple[str, str, str]] = [
            (ab or "", "", "") for ab in abilities
        ]
        for extra in EXTRA_FIELD_CONTEXTS.get(pokemon_name, []):
            for ab in abilities:
                contexts.append((ab or "", extra.get("weather", ""), extra.get("terrain", "")))

        seen_contexts: set[tuple[str, str, str]] = set()
        seen_calcs: set[tuple[str, str, str]] = set()   # (move_key, mode, desc)
        for ability, weather_override, terrain_override in contexts:
            key = (ability, weather_override, terrain_override)
            if key in seen_contexts:
                continue
            seen_contexts.add(key)

            calc_results = _call_calc(
                pokemon_name, moves, ability,
                weather=weather_override, terrain=terrain_override,
            )

            # Determine the effective weather to show in the CSV.
            if weather_override:
                effective_weather = weather_override
            else:
                effective_weather = ABILITY_TO_WEATHER.get(ability, "")
            effective_terrain = terrain_override or ABILITY_TO_TERRAIN.get(ability, "")

            for move_key, move_data in calc_results.items():
                if move_data is None:
                    continue
                for mode_name, mode_result in move_data["modes"].items():
                    if mode_result is None:
                        continue
                    calc_key = (move_key, mode_name, mode_result["desc"])
                    if calc_key in seen_calcs:
                        continue
                    seen_calcs.add(calc_key)
                    avg_pct = mode_result["avg_pct"]
                    power_set.append((
                        avg_pct,
                        {
                            "pokemon":   pokemon_name,
                            "ability":   ability,
                            "weather":   effective_weather,
                            "terrain":   effective_terrain,
                            "mode":      mode_name,
                            "move":      move_key,
                            "category":  move_data["category"],
                            "avg_pct":   round(avg_pct, 2),
                            "desc":      mode_result["desc"],
                        },
                    ))
                    count += 1

        print(f"{count} entries")

    power_set.sort(key=lambda x: x[0])

    fieldnames = ["rank", "avg_pct", "pokemon", "ability", "weather", "terrain", "mode", "move", "category", "desc"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for rank, (_, entry) in enumerate(power_set, 1):
            writer.writerow({"rank": rank, **entry})

    print(f"\nWrote {len(power_set)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

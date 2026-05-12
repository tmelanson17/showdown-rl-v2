"""
Pull top 100 Pokemon and their top 10 moves from Smogon's published usage stats
for Pokemon Champions Reg M-A (doubles), Glicko cutoff 1500.

Smogon publishes monthly stats at:
    https://www.smogon.com/stats/{YYYY-MM}/moveset/{format}-{cutoff}.txt

The moveset file contains, per Pokemon:
  - Raw count and usage %
  - Top abilities
  - Top items
  - Top moves            <-- what we want
  - Top spreads
  - Top teammates
  - Checks and counters

Format slug for Pokemon Champions Reg M-A is `gen9vgc2026regma`
(this is the same slug Showdown uses in /challenge codes).

Output:
  - champions_top100_doubles_1500.csv: flat table with rank, pokemon, usage,
    and 10 (move, pct) columns.
  - champions_top100_doubles_1500.json: structured records with metadata
    block and a list of pokemon, each with a nested top_moves array.
"""

import csv
import json
import re
import sys
import urllib.request
from urllib.error import HTTPError, URLError

# ---------- Config ----------
YEAR_MONTH = "2026-04"  # most recent complete month
FORMAT_SLUG = "gen9championsvgc2026regma"
CUTOFF = 1500
TOP_N_POKEMON = 100
TOP_N_MOVES = 10
TOP_N_SPREADS = 5
OUTPUT_CSV = "champions_top100_doubles_1500.csv"
OUTPUT_JSON = "champions_top100_doubles_1500.json"

BASE = "https://www.smogon.com/stats"

# Fallback slugs to try if the primary one 404s. Smogon occasionally
# uses slight naming variations between formats.
SLUG_CANDIDATES = [
    FORMAT_SLUG,
    "gen9vgc2026regmadoubles",
    "gen9pokemonchampionsregma",
    "gen9championsregma",
]


def fetch(url: str) -> str:
    """GET a URL with a normal user agent. Returns text or raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "champions-stats/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def find_working_url(year_month: str, slugs: list[str], cutoff: int) -> tuple[str, str]:
    """
    Try each candidate slug until one resolves. Returns (moveset_url, slug_used).
    """
    last_err = None
    for slug in slugs:
        url = f"{BASE}/{year_month}/moveset/{slug}-{cutoff}.txt"
        try:
            req = urllib.request.Request(
                url, method="HEAD", headers={"User-Agent": "champions-stats/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return url, slug
        except HTTPError as e:
            last_err = e
            continue
        except URLError as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Could not find a working stats file for {year_month} cutoff {cutoff}. "
        f"Tried slugs: {slugs}. Last error: {last_err}\n"
        f"Browse https://www.smogon.com/stats/{year_month}/moveset/ to find the right slug "
        f"and update FORMAT_SLUG at the top of this script."
    )


def parse_moveset_file(text: str):
    """
    Smogon moveset files are structured as a sequence of blocks delimited
    by '+----+' lines. The actual structure of each Pokemon entry is:

        +----+
        | Sneasler |              <-- name block (1 line)
        +----+
        | Raw count: 12345 |      <-- stats block (multi-line)
        | Avg. weight: 0.5 |
        | Viability Ceiling: 87 |
        +----+
        | Abilities |             <-- section block: header on first line,
        | Unburden 89.809% |          data rows on subsequent lines, ALL
        +----+                        within the same +---+ pair
        | Items |
        | White Herb 77.193% |
        | Focus Sash 20.016% |
        +----+
        | Moves |
        | Close Combat 99.540% |
        | Dire Claw 96.680% |
        ...
        +----+
        | Checks and Counters |
        | Garchomp 45.0 (60.5+/-3.0) |   <-- different format from other sections
        +----+

    So each block is "header line + zero or more data lines". We classify
    the block by its first line.
    """
    SECTION_HEADERS = {
        "Abilities",
        "Items",
        "Moves",
        "Spreads",
        "Teammates",
        "Checks and Counters",
        "Tera Types",
        "Happiness",
    }

    # Split the file into blocks delimited by +---+ lines.
    blocks = []
    current = []
    for line in text.splitlines():
        if re.match(r"^\s*\+-+\+\s*$", line):
            if current:
                blocks.append(current)
                current = []
        else:
            # Strip the leading/trailing "| " that wraps each row
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                stripped = stripped[1:-1].strip()
            if stripped:
                current.append(stripped)
    if current:
        blocks.append(current)

    entries = []
    current_entry = None

    for block in blocks:
        if not block:
            continue
        first = block[0]

        # Stats block (starts with "Raw count:")
        if first.startswith("Raw count:"):
            if current_entry is not None:
                for ln in block:
                    m = re.match(r"Raw count:\s*([\d,]+)", ln)
                    if m:
                        current_entry["raw_count"] = int(m.group(1).replace(",", ""))
            continue

        # Section block (header is one of the known section names)
        if first in SECTION_HEADERS:
            if current_entry is None:
                continue
            section_name = first
            rows = []
            for ln in block[1:]:
                # Match: "<name> <pct>%" possibly with extra spaces
                m = re.match(r"(.+?)\s+([\d.]+)\s*%\s*$", ln)
                if m:
                    rows.append((m.group(1).strip(), float(m.group(2))))
            current_entry["sections"][section_name] = rows
            continue

        # Otherwise treat as a name block (start of a new Pokemon).
        # Single-line block, not a section header, not a stats line.
        if len(block) == 1:
            current_entry = {"name": first, "sections": {}, "raw_count": None}
            entries.append(current_entry)
            continue
        # If we get here it's an unexpected block shape; skip it.

    return entries


def parse_usage_file(text: str):
    """
    Parse the top-level usage file (not the moveset one). It looks like:

      Total battles: 91698
      Avg. weight/team: 0.55
       + ---- + ---------------- + ------- + ------ + ------- + ------ + ------- +
       | Rank | Pokemon          | Usage % | Raw    | Raw %   | Real   | Real %  |
       + ---- + ---------------- + ------- + ------ + ------- + ------ + ------- +
       | 1    | Sneasler         | 55.612% | 12345  | 18.234% | 11000  | 17.901% |
       ...

    Returns dict: {pokemon_name: {"rank": int, "usage_pct": float, "raw_count": int}}
    """
    out = {}
    for line in text.splitlines():
        # Match data rows
        m = re.match(
            r"\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*([\d.]+)%\s*\|\s*(\d+)\s*\|",
            line,
        )
        if m:
            rank = int(m.group(1))
            name = m.group(2).strip()
            usage_pct = float(m.group(3))
            raw_count = int(m.group(4))
            out[name] = {"rank": rank, "usage_pct": usage_pct, "raw_count": raw_count}
    return out


def parse_spread(spread_str: str) -> dict | None:
    """
    Parse a Smogon spread string like 'Modest:252/0/4/252/0/0' into a dict.
    Returns None if the string doesn't match the expected format.
    """
    m = re.match(
        r"([A-Za-z]+):([\d]+)/([\d]+)/([\d]+)/([\d]+)/([\d]+)/([\d]+)",
        spread_str.strip(),
    )
    if not m:
        return None
    return {
        "nature": m.group(1),
        "evs": {
            "hp": int(m.group(2)),
            "atk": int(m.group(3)),
            "def": int(m.group(4)),
            "spa": int(m.group(5)),
            "spd": int(m.group(6)),
            "spe": int(m.group(7)),
        },
    }


def main():
    print(
        f"Looking for Smogon stats: {YEAR_MONTH}, cutoff {CUTOFF}, format {FORMAT_SLUG}"
    )

    moveset_url, slug = find_working_url(YEAR_MONTH, SLUG_CANDIDATES, CUTOFF)
    usage_url = f"{BASE}/{YEAR_MONTH}/{slug}-{CUTOFF}.txt"
    print(f"  Usage file:   {usage_url}")
    print(f"  Moveset file: {moveset_url}")

    print("Downloading usage file...")
    usage_text = fetch(usage_url)
    usage_table = parse_usage_file(usage_text)
    print(f"  Parsed {len(usage_table)} pokemon from usage file.")

    print("Downloading moveset file (this is the larger one)...")
    moveset_text = fetch(moveset_url)
    entries = parse_moveset_file(moveset_text)
    moveset_by_name = {e["name"]: e for e in entries}
    print(f"  Parsed {len(entries)} pokemon blocks from moveset file.")

    # Take top 100 by usage rank
    top_100 = sorted(usage_table.items(), key=lambda kv: kv[1]["rank"])[:TOP_N_POKEMON]

    print(f"Building top {TOP_N_POKEMON} records...")
    records = []
    for name, info in top_100:
        entry = moveset_by_name.get(name)
        sections = entry["sections"] if entry else {}

        moves = sections.get("Moves", [])
        top_moves = [
            {"name": m_name, "usage_pct": pct} for m_name, pct in moves[:TOP_N_MOVES]
        ]

        raw_spreads = sections.get("Spreads", [])
        top_spreads = []
        for spread_str, pct in raw_spreads[:TOP_N_SPREADS]:
            parsed = parse_spread(spread_str)
            if parsed:
                top_spreads.append({**parsed, "usage_pct": pct})

        records.append(
            {
                "rank": info["rank"],
                "pokemon": name,
                "usage_pct": info["usage_pct"],
                "raw_count": info["raw_count"],
                "top_moves": top_moves,
                "top_spreads": top_spreads,
            }
        )

    # ---- CSV output ----
    print(f"Writing {OUTPUT_CSV}...")
    headers = ["rank", "pokemon", "usage_pct", "raw_count"]
    for i in range(1, TOP_N_MOVES + 1):
        headers += [f"move_{i}", f"move_{i}_pct"]
    for i in range(1, TOP_N_SPREADS + 1):
        headers += [
            f"spread_{i}_nature",
            f"spread_{i}_hp",
            f"spread_{i}_atk",
            f"spread_{i}_def",
            f"spread_{i}_spa",
            f"spread_{i}_spd",
            f"spread_{i}_spe",
            f"spread_{i}_pct",
        ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for rec in records:
            row = [rec["rank"], rec["pokemon"], rec["usage_pct"], rec["raw_count"]]
            for i in range(TOP_N_MOVES):
                if i < len(rec["top_moves"]):
                    row += [
                        rec["top_moves"][i]["name"],
                        rec["top_moves"][i]["usage_pct"],
                    ]
                else:
                    row += ["", ""]
            for i in range(TOP_N_SPREADS):
                if i < len(rec["top_spreads"]):
                    s = rec["top_spreads"][i]
                    ev = s["evs"]
                    row += [
                        s["nature"],
                        ev["hp"],
                        ev["atk"],
                        ev["def"],
                        ev["spa"],
                        ev["spd"],
                        ev["spe"],
                        s["usage_pct"],
                    ]
                else:
                    row += ["", "", "", "", "", "", "", ""]
            w.writerow(row)

    # ---- JSON output ----
    print(f"Writing {OUTPUT_JSON}...")
    payload = {
        "metadata": {
            "source": "smogon.com/stats",
            "year_month": YEAR_MONTH,
            "format": slug,
            "cutoff": CUTOFF,
            "format_description": "Pokemon Champions Reg M-A (doubles)",
            "moveset_url": moveset_url,
            "usage_url": usage_url,
            "top_n_pokemon": TOP_N_POKEMON,
            "top_n_moves": TOP_N_MOVES,
            "top_n_spreads": TOP_N_SPREADS,
        },
        "pokemon": records,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Done. {len(records)} pokemon written to {OUTPUT_CSV} and {OUTPUT_JSON}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

"""Debug script to trace the SQuAD dataset logic."""

import json
import sys

sys.path.insert(0, "..")
from create_squad_dataset import (
    extract_team_info_from_log,
    get_active_pokemon_for_turn,
    get_fainted_pokemon_at_turn,
    build_context,
)

# Load the replay
with open("../../scraper/gen3ou_replays/gen3ou-2530797124.json") as f:
    replay = json.load(f)

log = replay["log"]

# Extract team info
p1_team, p2_team, p1_nicknames, p2_nicknames = extract_team_info_from_log(log)

print("=== P2 Team Info (extracted from full log) ===")
for species, data in p2_team.pokemon.items():
    print(f"  {species}: moves={data['moves']}, nickname={data['nickname']}")

print("\n=== Turn 20 Analysis for P2 ===")
turn = 20
active_p2 = get_active_pokemon_for_turn(log, turn, "p2")
fainted_p2 = get_fainted_pokemon_at_turn(log, turn, "p2")
print(f"Active Pokemon at turn {turn}: {active_p2}")
print(f"Fainted Pokemon at turn {turn}: {fainted_p2}")

context_p2 = build_context(p2_team, active_p2, fainted_p2, "p2")
print(f"\nContext:\n{context_p2}")

print("\n=== Check nickname mappings ===")
print(f"P2 nicknames: {p2_nicknames}")

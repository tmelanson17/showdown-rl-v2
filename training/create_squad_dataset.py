"""
create_squad_dataset.py — Create SQuAD-style question-answering dataset from Pokemon Showdown replays.

For each turn N and player I, this script generates:
- Question: The log data up until turn N, followed by "Please choose your next move"
- Context: List of a) "move" + active Pokemon's available moves, b) "switch" + available switch targets
- Answer: The move/switch actually chosen by the player

Moves are parsed from the entire replay log to build complete movesets.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set
from pathlib import Path


@dataclass
class TeamInfo:
    """Complete team information extracted from a replay."""

    username: str
    pokemon: Dict[str, Dict]  # species -> {moves: set, fainted: bool, nickname: str}

    def __post_init__(self):
        if self.pokemon is None:
            self.pokemon = {}


def extract_team_info_from_log(
    log: str,
) -> Tuple[TeamInfo, TeamInfo, Dict[str, str], Dict[str, str]]:
    """
    Pre-parse the entire replay log to extract full team information for both players.

    Returns:
        (p1_team, p2_team, p1_nicknames, p2_nicknames)
    """
    p1_team = TeamInfo(username="Player 1", pokemon={})
    p2_team = TeamInfo(username="Player 2", pokemon={})

    # Track nickname -> species mapping
    p1_nicknames: Dict[str, str] = {}
    p2_nicknames: Dict[str, str] = {}

    lines = log.split("\n")

    for line in lines:
        if not line.startswith("|"):
            continue

        parts = line.split("|")
        if len(parts) < 2:
            continue

        cmd = parts[1]

        # Parse player info
        if cmd == "player":
            if len(parts) >= 4:
                player_slot = parts[2]
                username = parts[3]
                if player_slot == "p1":
                    p1_team.username = username
                elif player_slot == "p2":
                    p2_team.username = username

        # Track switches to learn Pokemon species
        elif cmd in ("switch", "drag"):
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Jirachi"
                pokemon_info = parts[3]  # e.g., "Jirachi" or "Zapdos, M"

                # Extract nickname from slot
                nickname = slot.split(": ", 1)[1] if ": " in slot else slot

                # Extract species from pokemon_info (before any comma for gender)
                species = pokemon_info.split(",")[0].strip()

                if slot.startswith("p1"):
                    p1_nicknames[nickname] = species
                    if species not in p1_team.pokemon:
                        p1_team.pokemon[species] = {
                            "moves": set(),
                            "fainted": False,
                            "nickname": nickname,
                        }
                    else:
                        p1_team.pokemon[species]["nickname"] = nickname
                elif slot.startswith("p2"):
                    p2_nicknames[nickname] = species
                    if species not in p2_team.pokemon:
                        p2_team.pokemon[species] = {
                            "moves": set(),
                            "fainted": False,
                            "nickname": nickname,
                        }
                    else:
                        p2_team.pokemon[species]["nickname"] = nickname

        # Track moves to learn movesets
        elif cmd == "move":
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Jirachi"
                move_name = parts[3]

                # Extract nickname from slot
                nickname = slot.split(": ", 1)[1] if ": " in slot else slot

                if slot.startswith("p1"):
                    species = p1_nicknames.get(nickname, nickname)
                    if species in p1_team.pokemon:
                        p1_team.pokemon[species]["moves"].add(move_name)
                elif slot.startswith("p2"):
                    species = p2_nicknames.get(nickname, nickname)
                    if species in p2_team.pokemon:
                        p2_team.pokemon[species]["moves"].add(move_name)

        # Track faints
        elif cmd == "faint":
            if len(parts) >= 3:
                slot = parts[2]
                nickname = slot.split(": ", 1)[1] if ": " in slot else slot

                if slot.startswith("p1"):
                    species = p1_nicknames.get(nickname, nickname)
                    if species in p1_team.pokemon:
                        p1_team.pokemon[species]["fainted"] = True
                elif slot.startswith("p2"):
                    species = p2_nicknames.get(nickname, nickname)
                    if species in p2_team.pokemon:
                        p2_team.pokemon[species]["fainted"] = True

    return p1_team, p2_team, p1_nicknames, p2_nicknames


def get_log_up_to_turn(log: str, target_turn: int) -> str:
    """Extract the log data up to the START of the specified turn.

    This gives the player context of everything that happened before their decision point.
    """
    lines = log.split("\n")
    result_lines = []
    current_turn = 0

    for line in lines:
        if not line.strip():
            continue

        # Check for turn markers
        if line.startswith("|turn|"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    turn_num = int(parts[2])
                    if turn_num >= target_turn:
                        # Include the turn marker but stop after
                        result_lines.append(line)
                        break
                    current_turn = turn_num
                except ValueError:
                    pass

        # Skip some noisy lines
        if line.startswith("|t:|"):  # timestamps
            continue
        if line.startswith("|inactive|"):  # timer messages
            continue
        if line.startswith("|c|"):  # chat messages
            continue
        if line.startswith("|j|") or line.startswith("|l|"):  # join/leave messages
            continue
        if line.startswith("|raw|"):  # raw HTML
            continue

        result_lines.append(line)

    return "\n".join(result_lines)


def get_active_pokemon_for_turn(
    log: str, target_turn: int, player: str
) -> Optional[str]:
    """Get the active Pokemon species for a player at the START of a given turn.

    This captures what's active when the player makes their decision, before any
    moves or switches happen during that turn.
    """
    lines = log.split("\n")
    active_pokemon = None
    current_turn = 0
    in_target_turn = False

    # Map nicknames to species
    nicknames = {}

    for line in lines:
        if line.startswith("|turn|"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    turn_num = int(parts[2])
                    current_turn = turn_num
                    if turn_num == target_turn:
                        in_target_turn = True
                        # Return the active Pokemon at this point (before any actions in this turn)
                        return active_pokemon
                    elif turn_num > target_turn:
                        break
                except ValueError:
                    pass

        elif line.startswith("|switch|") or line.startswith("|drag|"):
            parts = line.split("|")
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Nickname"
                pokemon_info = parts[3]  # e.g., "Species, M"

                if slot.startswith(player):
                    nickname = slot.split(": ", 1)[1] if ": " in slot else slot
                    species = pokemon_info.split(",")[0].strip()
                    nicknames[nickname] = species
                    active_pokemon = species

    return active_pokemon


def get_fainted_pokemon_at_turn(log: str, target_turn: int, player: str) -> Set[str]:
    """Get set of fainted Pokemon species for a player BEFORE the given turn starts.

    This captures the state when the player makes their decision.
    """
    lines = log.split("\n")
    fainted = set()
    current_turn = 0

    # Map nicknames to species
    nicknames = {}

    for line in lines:
        if line.startswith("|turn|"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    turn_num = int(parts[2])
                    current_turn = turn_num
                    if turn_num >= target_turn:
                        # Stop before processing events of the target turn
                        break
                except ValueError:
                    pass

        elif line.startswith("|switch|") or line.startswith("|drag|"):
            parts = line.split("|")
            if len(parts) >= 4:
                slot = parts[2]
                pokemon_info = parts[3]

                if slot.startswith(player):
                    nickname = slot.split(": ", 1)[1] if ": " in slot else slot
                    species = pokemon_info.split(",")[0].strip()
                    nicknames[nickname] = species

        elif line.startswith("|faint|"):
            parts = line.split("|")
            if len(parts) >= 3:
                slot = parts[2]
                if slot.startswith(player):
                    nickname = slot.split(": ", 1)[1] if ": " in slot else slot
                    species = nicknames.get(nickname, nickname)
                    fainted.add(species)

    return fainted


def build_context(
    team_info: TeamInfo,
    active_pokemon: str,
    fainted_at_turn: Set[str],
    player_prefix: str,
) -> str:
    """
    Build the context string containing available moves and switch options.

    Format:
    - "move <MoveName>" for each move of the active Pokemon
    - "switch <PokemonSpecies>" for each non-fainted, non-active Pokemon
    """
    context_lines = []

    # Add moves for active Pokemon
    if active_pokemon and active_pokemon in team_info.pokemon:
        pokemon_data = team_info.pokemon[active_pokemon]
        moves = pokemon_data.get("moves", set())

        # Add "move <MoveName>" entries
        for move in sorted(moves):  # Sort for consistency
            context_lines.append(f"move {move}")

    # Add switch options (non-fainted, non-active Pokemon)
    for species, data in team_info.pokemon.items():
        if species == active_pokemon:
            continue
        if species in fainted_at_turn:
            continue
        if data.get("fainted", False):
            # This tracks end-of-game fainted status, but we use fainted_at_turn for accuracy
            pass
        context_lines.append(f"switch {species}")

    return "\n".join(context_lines)


def format_answer(action_data: dict) -> str:
    """Format the action as the answer string."""
    if not action_data:
        return None

    action_type = action_data.get("action_type", "")
    label = action_data.get("label", "")

    if action_type == "move":
        return f"move {label}"
    elif action_type == "switch":
        return f"switch {label}"
    else:
        return f"{action_type} {label}"


def create_squad_entry(
    question: str, context: str, answer: str, replay_id: str, turn: int, player: int
) -> dict:
    """Create a single SQuAD-format entry."""
    # Find answer position in context
    answer_start = context.find(answer)
    if answer_start == -1:
        # Answer not found in context verbatim, try case-insensitive
        context_lower = context.lower()
        answer_lower = answer.lower()
        answer_start = context_lower.find(answer_lower)

    return {
        "id": f"{replay_id}_turn{turn}_p{player}",
        "title": replay_id,
        "context": context,
        "question": question,
        "answers": {
            "text": [answer],
            "answer_start": [max(0, answer_start)],  # If not found, use 0
        },
    }


def process_replay(replay_data: dict) -> List[dict]:
    """Process a single replay and return SQuAD-format entries.

    Skips entries where:
    - The player's action is null/unknown (e.g., sleeping, forced switch after KO)
    - The answer doesn't appear in the available context options
    """
    squad_entries = []

    replay_id = replay_data.get("id", "unknown")
    log = replay_data.get("log", "")
    parsed_turns = replay_data.get("parsed_turns", [])

    if not log or not parsed_turns:
        return squad_entries

    # Extract full team info from log
    p1_team, p2_team, p1_nicknames, p2_nicknames = extract_team_info_from_log(log)

    for turn_data in parsed_turns:
        turn_num = turn_data.get("turn", 0)
        p1_action = turn_data.get("p1_action")
        p2_action = turn_data.get("p2_action")

        # Get log up to this turn
        log_context = get_log_up_to_turn(log, turn_num)
        question = f"{log_context}\n\nPlease choose your next move."

        # Process P1's action (skip if null/unknown)
        if p1_action and p1_action.get("action_type") and p1_action.get("label"):
            active_p1 = get_active_pokemon_for_turn(log, turn_num, "p1")
            fainted_p1 = get_fainted_pokemon_at_turn(log, turn_num, "p1")

            context_p1 = build_context(p1_team, active_p1, fainted_p1, "p1")
            answer_p1 = format_answer(p1_action)

            # Only include if context is valid and answer is in context
            if context_p1 and answer_p1 and answer_p1 in context_p1:
                entry = create_squad_entry(
                    question=question,
                    context=context_p1,
                    answer=answer_p1,
                    replay_id=replay_id,
                    turn=turn_num,
                    player=1,
                )
                squad_entries.append(entry)

        # Process P2's action (skip if null/unknown)
        if p2_action and p2_action.get("action_type") and p2_action.get("label"):
            active_p2 = get_active_pokemon_for_turn(log, turn_num, "p2")
            fainted_p2 = get_fainted_pokemon_at_turn(log, turn_num, "p2")

            context_p2 = build_context(p2_team, active_p2, fainted_p2, "p2")
            answer_p2 = format_answer(p2_action)

            # Only include if context is valid and answer is in context
            if context_p2 and answer_p2 and answer_p2 in context_p2:
                entry = create_squad_entry(
                    question=question,
                    context=context_p2,
                    answer=answer_p2,
                    replay_id=replay_id,
                    turn=turn_num,
                    player=2,
                )
                squad_entries.append(entry)

    return squad_entries


def create_squad_dataset(input_path: str, output_path: str = None) -> dict:
    """
    Create a SQuAD-format dataset from a replay JSON file or directory.

    Args:
        input_path: Path to a single JSON file or directory of JSON files
        output_path: Optional path to save the output JSON

    Returns:
        SQuAD-format dataset dictionary
    """
    all_entries = []

    input_path = Path(input_path)

    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = list(input_path.glob("*.json"))
    else:
        raise ValueError(f"Input path does not exist: {input_path}")

    for file_path in files:
        print(f"Processing: {file_path.name}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                replay_data = json.load(f)

            entries = process_replay(replay_data)
            all_entries.extend(entries)
            print(f"  Generated {len(entries)} entries")
        except Exception as e:
            print(f"  Error processing {file_path.name}: {e}")

    # Build SQuAD format
    squad_dataset = {
        "version": "1.1",
        "data": [
            {
                "title": "Pokemon Showdown Battles",
                "paragraphs": [
                    {
                        "context": entry["context"],
                        "qas": [
                            {
                                "id": entry["id"],
                                "question": entry["question"],
                                "answers": [
                                    {
                                        "text": entry["answers"]["text"][0],
                                        "answer_start": entry["answers"][
                                            "answer_start"
                                        ][0],
                                    }
                                ],
                                "is_impossible": False,
                            }
                        ],
                    }
                    for entry in all_entries
                ],
            }
        ],
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(squad_dataset, f, indent=2)
        print(f"\nSaved SQuAD dataset to: {output_path}")
        print(f"Total entries: {len(all_entries)}")

    return squad_dataset


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Create SQuAD-style dataset from Pokemon Showdown replays"
    )
    parser.add_argument(
        "input", help="Path to a replay JSON file or directory of replay files"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="squad_pokemon_dataset.json",
        help="Output path for the SQuAD dataset (default: squad_pokemon_dataset.json)",
    )

    args = parser.parse_args()

    create_squad_dataset(args.input, args.output)


if __name__ == "__main__":
    main()

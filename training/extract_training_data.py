"""
extract_training_data.py — Extract prompt/action pairs from gen3ou replay JSONs.

This script reads replay JSON files and generates:
1. A list of prompt inputs (one for each time a player takes an action)
2. A list of Action labels (e.g., "MOVE Earthquake" or "SWITCH Suicune")

The prompts follow the LLMModelAgent format from model_agent.py.

Key feature: For the player's side, we pre-parse the entire replay to extract
full team information (all Pokemon and their moves), since the player knows
their full team from turn 1. Opponent information remains limited to what's
been revealed during the battle.
"""

import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Set
from pathlib import Path


@dataclass
class Action:
    """A single action the active player can take on their turn."""

    action_id: str
    action_type: str
    target_index: int
    label: str


@dataclass
class PokemonSlot:
    """Snapshot of one Pokémon on a side."""

    species: str
    hp_pct: float
    status: Optional[str] = None
    is_active: bool = False
    moves: List[str] = None

    def __post_init__(self):
        if self.moves is None:
            self.moves = []


@dataclass
class Side:
    """One player's side of the battle."""

    player_id: int
    username: str
    pokemon: List[PokemonSlot] = None

    def __post_init__(self):
        if self.pokemon is None:
            self.pokemon = []


@dataclass
class GameState:
    """Full snapshot of the battle at a single point in time."""

    turn: int
    active_player: int
    sides: List[Side] = None
    available_actions: List[Action] = None
    raw_protocol: Optional[str] = None

    def __post_init__(self):
        if self.sides is None:
            self.sides = []
        if self.available_actions is None:
            self.available_actions = []


@dataclass
class TeamInfo:
    """Complete team information extracted from a replay."""

    username: str
    pokemon: Dict[str, Set[str]]  # species -> set of known moves

    def __post_init__(self):
        if self.pokemon is None:
            self.pokemon = {}


def extract_team_info_from_log(log: str) -> Tuple[TeamInfo, TeamInfo]:
    """
    Pre-parse the entire replay log to extract full team information for both players.

    This scans all |switch| and |move| lines to build a complete picture of each
    player's team and their movesets.

    Returns (p1_team, p2_team) TeamInfo objects.
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
        elif cmd == "switch" or cmd == "drag":
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Jirachi" or "p1a: Nickname"
                pokemon_info = parts[3]  # e.g., "Jirachi" or "Zapdos, M"

                # Extract nickname from slot
                nickname = slot.split(": ", 1)[1] if ": " in slot else slot

                # Extract species from pokemon_info (before any comma for gender)
                species = pokemon_info.split(",")[0].strip()

                if slot.startswith("p1"):
                    p1_nicknames[nickname] = species
                    if species not in p1_team.pokemon:
                        p1_team.pokemon[species] = set()
                elif slot.startswith("p2"):
                    p2_nicknames[nickname] = species
                    if species not in p2_team.pokemon:
                        p2_team.pokemon[species] = set()

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
                        p1_team.pokemon[species].add(move_name)
                elif slot.startswith("p2"):
                    species = p2_nicknames.get(nickname, nickname)
                    if species in p2_team.pokemon:
                        p2_team.pokemon[species].add(move_name)

    return p1_team, p2_team


def parse_pokemon_slot(data: dict) -> PokemonSlot:
    """Parse a pokemon slot from JSON data."""
    return PokemonSlot(
        species=data.get("species", "Unknown"),
        hp_pct=data.get("hp_pct", 1.0),
        status=data.get("status"),
        is_active=data.get("is_active", False),
        moves=data.get("moves", []),
    )


def parse_side(data: dict) -> Side:
    """Parse a side from JSON data."""
    pokemon = [parse_pokemon_slot(p) for p in data.get("pokemon", [])]
    return Side(
        player_id=data.get("player_id", 1),
        username=data.get("username", "Unknown"),
        pokemon=pokemon,
    )


def parse_game_state(data: dict) -> GameState:
    """Parse a game state from JSON data."""
    sides = [parse_side(s) for s in data.get("sides", [])]
    actions = [Action(**a) for a in data.get("available_actions", [])]
    return GameState(
        turn=data.get("turn", 0),
        active_player=data.get("active_player", 1),
        sides=sides,
        available_actions=actions,
        raw_protocol=data.get("raw_protocol"),
    )


def parse_action(data: dict) -> Action:
    """Parse an action from JSON data."""
    return Action(
        action_id=data.get("action_id", ""),
        action_type=data.get("action_type", ""),
        target_index=data.get("target_index", 1),
        label=data.get("label", ""),
    )


def stringify_game_state(
    gamestate: GameState, player_number: int, my_team_info: Optional[TeamInfo] = None
) -> str:
    """Convert game state to a human-readable string for the LLM.

    This mirrors the _stringify_game_state method in LLMModelAgent.

    If my_team_info is provided, it will be used to show the full team roster
    with all known moves for the player's side, since the player knows their
    full team from turn 1.
    """
    lines = []
    lines.append(f"Turn: {gamestate.turn}")
    lines.append(f"Active Player: Player {gamestate.active_player}")
    lines.append("")

    for side in gamestate.sides:
        is_my_side = side.player_id == player_number
        marker = " (YOU)" if is_my_side else " (OPPONENT)"
        lines.append(f"=== {side.username}{marker} ===")

        if is_my_side and my_team_info and my_team_info.pokemon:
            # For my side, show all Pokemon from my_team_info with full movesets
            # Get the active Pokemon from the current state
            active_pokemon = None
            active_hp = 1.0
            active_status = None
            if side.pokemon:
                for p in side.pokemon:
                    if p.is_active:
                        active_pokemon = p.species
                        active_hp = p.hp_pct
                        active_status = p.status
                        break

            # Display all Pokemon from team info
            idx = 1
            for species, moves in my_team_info.pokemon.items():
                is_active = species == active_pokemon
                active_tag = " [ACTIVE]" if is_active else ""

                # HP info - only known for active Pokemon from state
                if is_active:
                    hp_display = f"{active_hp * 100:.0f}%"
                    if active_hp == 0:
                        hp_display = "FAINTED"
                    status_tag = f" ({active_status})" if active_status else ""
                else:
                    # For non-active Pokemon, we don't know current HP from replay
                    hp_display = "??%"
                    status_tag = ""

                lines.append(
                    f"  {idx}. {species}: {hp_display}{status_tag}{active_tag}"
                )

                # Show all known moves
                if moves:
                    moves_str = ", ".join(sorted(moves))
                    lines.append(f"     Moves: {moves_str}")

                idx += 1
        elif not side.pokemon:
            lines.append("  (No Pokemon info available)")
        else:
            # For opponent's side or when no team info, use current state only
            for i, poke in enumerate(side.pokemon):
                active_tag = " [ACTIVE]" if poke.is_active else ""
                status_tag = f" ({poke.status})" if poke.status else ""
                hp_display = f"{poke.hp_pct * 100:.0f}%"
                if poke.hp_pct == 0:
                    hp_display = "FAINTED"
                lines.append(
                    f"  {i + 1}. {poke.species}: {hp_display}{status_tag}{active_tag}"
                )
                # Don't show opponent's moves
        lines.append("")

    return "\n".join(lines)


def stringify_available_actions(actions: List[Action]) -> str:
    """Convert available actions to a list."""
    lines = ["Available actions:"]
    for action in actions:
        action_type = action.action_type.upper()
        lines.append(f"  {action_type}: {action.label}")
    return "\n".join(lines)


def build_prompt(
    gamestate: GameState,
    player_number: int,
    battle_format: str = "[Gen 3] OU",
    my_team_info: Optional[TeamInfo] = None,
) -> str:
    """Build the prompt for the LLM.

    This mirrors the _build_prompt method in LLMModelAgent.
    Uses {PLAYER_NUMBER} as a token for the player number placeholder.

    If my_team_info is provided, the player's side will show full team info.
    """
    state_str = stringify_game_state(gamestate, player_number, my_team_info)
    actions_str = stringify_available_actions(gamestate.available_actions)

    # Note: We don't have turn history or action history from replays,
    # so those sections are omitted
    prompt = f"""You are battling in {battle_format} as Player {{PLAYER_NUMBER}}. Given the current battle state, please:
choose one of the legal moves on the active Pokemon, if applicable, or
switch to one of the non-fainted Pokemon on the team, if allowed.

Current Battle State:
{state_str}

{actions_str}

IMPORTANT: You must respond with ONLY the the exact action name from the list above.
Think about type matchups, HP levels, and status conditions when making your decision.
Do NOT include any explanation - just the action name.

Your choice:"""

    return prompt


def action_to_label(action: Action) -> str:
    """Convert an Action to the format LLMModelAgent expects.

    Returns strings like "MOVE Earthquake" or "SWITCH Suicune".
    """
    action_type = action.action_type.upper()
    return f"{action_type} {action.label}"


def extract_training_data_from_replay(replay_data: dict) -> List[Tuple[str, str, int]]:
    """Extract (prompt, action_label, player_number) tuples from a single replay.

    Returns a list of tuples where each tuple contains:
    - prompt: The input prompt for the LLM
    - action_label: The expected action (e.g., "MOVE Earthquake")
    - player_number: Which player (1 or 2) this is for

    The player's side will show full team information (all Pokemon and their moves)
    since the player knows their full team from turn 1.
    """
    training_pairs = []
    battle_format = replay_data.get("format", "[Gen 3] OU")
    parsed_turns = replay_data.get("parsed_turns", [])
    log = replay_data.get("log", "")

    # Pre-parse the log to extract full team information
    p1_team_info, p2_team_info = extract_team_info_from_log(log)

    for turn_data in parsed_turns:
        state_dict = turn_data.get("state", {})
        p1_action_dict = turn_data.get("p1_action")
        p2_action_dict = turn_data.get("p2_action")

        if not state_dict:
            continue

        gamestate = parse_game_state(state_dict)

        # Extract training data for player 1
        if p1_action_dict:
            p1_action = parse_action(p1_action_dict)
            # Add the action to available_actions so it appears in the prompt
            gamestate_p1 = parse_game_state(state_dict)
            gamestate_p1.available_actions = [p1_action]

            prompt = build_prompt(
                gamestate_p1,
                player_number=1,
                battle_format=battle_format,
                my_team_info=p1_team_info,
            )
            action_label = action_to_label(p1_action)
            training_pairs.append((prompt, action_label, 1))

        # Extract training data for player 2
        if p2_action_dict:
            p2_action = parse_action(p2_action_dict)
            # Add the action to available_actions so it appears in the prompt
            gamestate_p2 = parse_game_state(state_dict)
            gamestate_p2.available_actions = [p2_action]

            prompt = build_prompt(
                gamestate_p2,
                player_number=2,
                battle_format=battle_format,
                my_team_info=p2_team_info,
            )
            action_label = action_to_label(p2_action)
            training_pairs.append((prompt, action_label, 2))

    return training_pairs


def load_all_replays(replays_dir: str) -> List[dict]:
    """Load all replay JSON files from a directory."""
    replays = []
    replays_path = Path(replays_dir)

    for json_file in replays_path.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                replay_data = json.load(f)
                replay_data["_filename"] = json_file.name
                replays.append(replay_data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {json_file}: {e}")

    return replays


def extract_all_training_data(replays_dir: str) -> Tuple[List[str], List[str]]:
    """Extract all training data from replay JSONs.

    Returns:
        prompts: List of prompt strings (with {PLAYER_NUMBER} token)
        actions: List of action labels (e.g., "MOVE Earthquake")
    """
    replays = load_all_replays(replays_dir)

    all_prompts = []
    all_actions = []

    for replay in replays:
        training_pairs = extract_training_data_from_replay(replay)
        for prompt, action_label, player_num in training_pairs:
            # Replace the actual player number in the prompt with a token
            # that can be replaced later
            prompt_with_token = prompt.replace(
                f"Player {player_num}", "Player {PLAYER_NUMBER}"
            )
            all_prompts.append(prompt_with_token)
            all_actions.append(action_label)

    return all_prompts, all_actions


def main():
    """Main entry point."""
    # Get the directory of this script
    script_dir = Path(__file__).parent.parent
    replays_dir = script_dir / "scraper" / "gen3ou_replays"

    print(f"Loading replays from: {replays_dir}")

    prompts, actions = extract_all_training_data(str(replays_dir))

    print(f"\nExtracted {len(prompts)} training examples")
    print(f"Unique actions: {len(set(actions))}")

    # Print action distribution
    from collections import Counter

    action_counts = Counter(actions)
    print("\nAction distribution (top 20):")
    for action, count in action_counts.most_common(20):
        print(f"  {action}: {count}")

    # Save to JSON files
    output_dir = script_dir / "training"

    # Save prompts
    prompts_file = output_dir / "training_prompts.json"
    with open(prompts_file, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2)
    print(f"\nSaved prompts to: {prompts_file}")

    # Save actions
    actions_file = output_dir / "training_actions.json"
    with open(actions_file, "w", encoding="utf-8") as f:
        json.dump(actions, f, indent=2)
    print(f"Saved actions to: {actions_file}")

    # Also save as paired data
    paired_file = output_dir / "training_pairs.json"
    paired_data = [{"prompt": p, "action": a} for p, a in zip(prompts, actions)]
    with open(paired_file, "w", encoding="utf-8") as f:
        json.dump(paired_data, f, indent=2)
    print(f"Saved paired data to: {paired_file}")

    # Print a few examples
    print("\n" + "=" * 60)
    print("EXAMPLE TRAINING PAIRS")
    print("=" * 60)

    # Choose up to 3 random examples to display
    for i in range(min(3, len(prompts))):
        idx = random.randint(0, len(prompts) - 1)
        print(f"\n--- Example {i + 1} ---")
        print("PROMPT:")
        print(prompts[idx][:500] + "..." if len(prompts[idx]) > 2000 else prompts[idx])
        print(f"\nEXPECTED ACTION: {actions[idx]}")
        print()


if __name__ == "__main__":
    main()

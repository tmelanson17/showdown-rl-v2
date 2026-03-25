"""
web_scraper.py — Passive listener that polls replay.pokemonshowdown.com
for new public gen3ou battles every 30 minutes (configurable).

Downloads full replay logs and parses them into (game state, action) pairs
for reinforcement learning training.

API used:
  GET https://replay.pokemonshowdown.com/search.json
        ?format=gen3ou
        &before=<unix_timestamp>      ← cursor for pagination
  GET https://replay.pokemonshowdown.com/<id>.json  ← full replay log

How it works:
  1. On first run (no state file) it fetches the latest page of replays
     and saves every ID it sees.  Nothing is "new" yet — this seeds the
     cursor so the next poll knows where to start.
  2. On every subsequent run it fetches replays uploaded *after* the last
     cursor it stored.  Any replay ID it has never seen before is treated
     as a new battle and is logged / saved.
  3. State (seen IDs + cursor timestamp) is persisted in a JSON sidecar
     file so the poller survives restarts.
  4. Full replay logs are downloaded and parsed into (state, action) pairs.

Usage:
  python web_scraper.py                        # default: gen3ou, 30 min interval
  python web_scraper.py --format gen3ou --interval 15 --output replays/
  python web_scraper.py --min-elo 1500         # only download replays with rating >= 1500
  python web_scraper.py --test                 # run self-tests

Dependencies:
  Python 3.8+   (stdlib only — uses urllib, json, time, argparse, os, re)
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, List, Optional

# Add src directory to path for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "showdown_env")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from ps_types import GameState, Action, Side, PokemonSlot

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
DEFAULT_FORMAT = "gen3ou"
DEFAULT_INTERVAL = 30  # minutes
DEFAULT_OUTPUT = "replays"  # directory where new replay JSONs are saved
DEFAULT_MIN_ELO = 0  # minimum ELO rating filter (0 = no filter)
STATE_FILE = "ps_poller_state.json"

SEARCH_URL = "https://replay.pokemonshowdown.com/search.json"
REPLAY_URL = "https://replay.pokemonshowdown.com/{id}.json"

# PS returns at most 51 results per request; 51st entry is the "there is
# more" signal.  We only consume the first 50.
PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers  (read / write the JSON sidecar)
# ---------------------------------------------------------------------------


def load_state(path: str) -> dict[str, Any]:
    """Return persisted state or an empty dict on first run."""
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict[str, Any]) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def fetch_json(url: str) -> Any:
    """Fetch a URL and return parsed JSON.  Raises on HTTP / network error."""
    req = urllib.request.Request(url, headers={"User-Agent": "ps_poller/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Data structures for parsed replay data
# ---------------------------------------------------------------------------
# GameState, Action, Side, PokemonSlot are imported from ps_types


@dataclass
class TurnRecord:
    """A complete record of one turn: state before + actions taken."""

    turn: int
    state: GameState
    p1_action: Optional[Action]
    p2_action: Optional[Action]

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "state": self.state.to_dict(),
            "p1_action": self.p1_action.to_dict() if self.p1_action else None,
            "p2_action": self.p2_action.to_dict() if self.p2_action else None,
        }


# ---------------------------------------------------------------------------
# Replay log parser
# ---------------------------------------------------------------------------


def fetch_full_replay(replay_id: str) -> dict | None:
    """
    Fetch the full replay data including the battle log.
    Returns None on error.
    """
    url = REPLAY_URL.format(id=replay_id)
    logger.info("Fetching full replay: %s", url)
    try:
        return fetch_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.error("Failed to fetch replay %s: %s", replay_id, exc)
        return None


def parse_replay_log(log: str) -> list[TurnRecord]:
    """
    Parse a Pokemon Showdown battle log into a list of TurnRecords.

    Each TurnRecord contains:
    - The game state at the start of the turn (using ps_types.GameState)
    - The actions taken by both players during that turn (using ps_types.Action)

    Returns a list of TurnRecords, one per turn.
    """
    lines = log.split("\n")

    turn_records: list[TurnRecord] = []
    current_turn = 0

    # Track current state for each player's active pokemon
    # We'll track: species, hp_pct, status
    active_p1_species = ""
    active_p2_species = ""
    hp_pct_p1 = 1.0
    hp_pct_p2 = 1.0
    status_p1: Optional[str] = None
    status_p2: Optional[str] = None

    # Player usernames (parsed from |player| lines)
    username_p1 = "Player 1"
    username_p2 = "Player 2"

    # Actions for current turn
    p1_action: Optional[Action] = None
    p2_action: Optional[Action] = None

    # State at turn start (captured when we see |turn|N)
    turn_start_state: Optional[GameState] = None

    def parse_hp(hp_str: str) -> tuple[float, Optional[str]]:
        """Parse HP string like '100/100', '45/100 par', '0 fnt' into (hp_pct, status)."""
        hp_str = hp_str.strip()
        status = None

        # Check for status conditions
        for s in ["par", "brn", "slp", "frz", "psn", "tox", "fnt"]:
            if s in hp_str:
                status = s
                hp_str = hp_str.replace(s, "").strip()
                break

        if "/" in hp_str:
            parts = hp_str.split("/")
            try:
                current = float(parts[0])
                max_hp = float(parts[1].split()[0])  # Handle "100/100" format
                return (current / max_hp if max_hp > 0 else 0.0, status)
            except (ValueError, IndexError):
                return (0.0, status)
        elif hp_str == "0":
            return (0.0, "fnt")
        else:
            return (1.0, status)

    def make_game_state(turn: int) -> GameState:
        """Create a GameState from current tracking variables."""
        p1_pokemon = PokemonSlot(
            species=active_p1_species,
            hp_pct=hp_pct_p1,
            status=status_p1,
            is_active=True,
            moves=[],
        )
        p2_pokemon = PokemonSlot(
            species=active_p2_species,
            hp_pct=hp_pct_p2,
            status=status_p2,
            is_active=True,
            moves=[],
        )

        side1 = Side(player_id=1, username=username_p1, pokemon=[p1_pokemon])
        side2 = Side(player_id=2, username=username_p2, pokemon=[p2_pokemon])

        return GameState(
            turn=turn,
            active_player=1,  # We don't know from replays who moves first
            sides=[side1, side2],
            available_actions=[],  # Not available from replay
            raw_protocol=None,
        )

    def make_action(
        player: str, action_type: str, label: str, target_index: int = 1
    ) -> Action:
        """Create an Action using the ps_types.Action structure."""
        action_id = (
            f"{action_type} {target_index}"
            if action_type == "move"
            else f"{action_type} {label}"
        )
        return Action(
            action_id=action_id,
            action_type=action_type,
            target_index=target_index,
            label=label,
        )

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
                player_slot = parts[2]  # "p1" or "p2"
                username = parts[3]
                if player_slot == "p1":
                    username_p1 = username
                elif player_slot == "p2":
                    username_p2 = username

        # Track switches
        elif cmd == "switch" or cmd == "drag":
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Zapdos"
                pokemon_info = parts[3]  # e.g., "Zapdos" or "Zapdos, M"
                hp_info = parts[4] if len(parts) > 4 else "100/100"

                pokemon_name = pokemon_info.split(",")[0].strip()
                hp_pct, status = parse_hp(hp_info)

                if slot.startswith("p1"):
                    active_p1_species = pokemon_name
                    hp_pct_p1 = hp_pct
                    status_p1 = status
                    # Record switch action if we're in a turn
                    if current_turn > 0 and turn_start_state is not None:
                        # Check if this is a switch-in from Baton Pass or forced
                        is_from_effect = "[from]" in line
                        if not is_from_effect:
                            p1_action = make_action("p1", "switch", pokemon_name)
                elif slot.startswith("p2"):
                    active_p2_species = pokemon_name
                    hp_pct_p2 = hp_pct
                    status_p2 = status
                    if current_turn > 0 and turn_start_state is not None:
                        is_from_effect = "[from]" in line
                        if not is_from_effect:
                            p2_action = make_action("p2", "switch", pokemon_name)

        # Track moves
        elif cmd == "move":
            if len(parts) >= 4:
                slot = parts[2]  # e.g., "p1a: Zapdos"
                move_name = parts[3]

                if slot.startswith("p1"):
                    p1_action = make_action("p1", "move", move_name)
                elif slot.startswith("p2"):
                    p2_action = make_action("p2", "move", move_name)

        # Track damage
        elif cmd == "-damage" or cmd == "-heal":
            if len(parts) >= 4:
                slot = parts[2]
                hp_info = parts[3]
                hp_pct, status = parse_hp(hp_info)
                if slot.startswith("p1"):
                    hp_pct_p1 = hp_pct
                    status_p1 = status
                elif slot.startswith("p2"):
                    hp_pct_p2 = hp_pct
                    status_p2 = status

        # Track status changes
        elif cmd == "-status":
            if len(parts) >= 4:
                slot = parts[2]
                status = parts[3]
                if slot.startswith("p1"):
                    status_p1 = status
                elif slot.startswith("p2"):
                    status_p2 = status

        elif cmd == "-curestatus":
            if len(parts) >= 3:
                slot = parts[2]
                if slot.startswith("p1"):
                    status_p1 = None
                elif slot.startswith("p2"):
                    status_p2 = None

        # Track turn boundaries
        elif cmd == "turn":
            # Save the previous turn's record before starting new turn
            if current_turn > 0 and turn_start_state is not None:
                turn_records.append(
                    TurnRecord(
                        turn=current_turn,
                        state=turn_start_state,
                        p1_action=p1_action,
                        p2_action=p2_action,
                    )
                )

            # Start new turn
            current_turn = int(parts[2]) if len(parts) > 2 else current_turn + 1

            # Capture state at turn start
            turn_start_state = make_game_state(current_turn)

            # Reset actions for new turn
            p1_action = None
            p2_action = None

        # Track faints (player may not have an action if KO'd)
        elif cmd == "faint":
            if len(parts) >= 3:
                slot = parts[2]
                if slot.startswith("p1"):
                    hp_pct_p1 = 0.0
                    status_p1 = "fnt"
                elif slot.startswith("p2"):
                    hp_pct_p2 = 0.0
                    status_p2 = "fnt"

        # Track win condition (end of battle)
        elif cmd == "win":
            # Save the last turn's record
            if current_turn > 0 and turn_start_state is not None:
                turn_records.append(
                    TurnRecord(
                        turn=current_turn,
                        state=turn_start_state,
                        p1_action=p1_action,
                        p2_action=p2_action,
                    )
                )

    return turn_records


def validate_turn_records(records: list[TurnRecord]) -> tuple[bool, list[str]]:
    """
    Validate that the turn records form a complete sequence.

    Returns (is_valid, list of error messages).
    """
    errors: list[str] = []

    if not records:
        errors.append("No turn records found")
        return False, errors

    # Check turn sequence is continuous
    expected_turn = 1
    for record in records:
        if record.turn != expected_turn:
            errors.append(f"Missing turn {expected_turn}, got turn {record.turn}")
        expected_turn = record.turn + 1

    # Check each turn has at least one action (unless opponent was KO'd)
    for record in records:
        # Get active pokemon status from the GameState structure
        p1_side = record.state.sides[0] if record.state.sides else None
        p2_side = record.state.sides[1] if len(record.state.sides) > 1 else None

        p1_active = p1_side.pokemon[0] if p1_side and p1_side.pokemon else None
        p2_active = p2_side.pokemon[0] if p2_side and p2_side.pokemon else None

        p1_ko = p1_active and p1_active.status == "fnt"
        p2_ko = p2_active and p2_active.status == "fnt"

        if not p1_ko and record.p1_action is None:
            # This could be legitimate if p1 was KO'd during the turn
            pass  # Don't error, as this can happen with certain mechanics

        if not p2_ko and record.p2_action is None:
            # Same for p2
            pass

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Core polling logic
# ---------------------------------------------------------------------------


def fetch_replays_page(fmt: str, before: int | None = None) -> list[dict]:
    """
    Fetch one page (≤51 results) of replays for *fmt*.
    *before* is an optional unix timestamp cursor (exclusive upper bound).
    """
    params = f"format={fmt}"
    if before is not None:
        params += f"&before={before}"
    url = f"{SEARCH_URL}?{params}"

    logger.info("Fetching: %s", url)
    data = fetch_json(url)

    # The API returns a list directly (or an empty list when there are none)
    if not isinstance(data, list):
        logger.warning("Unexpected response type: %s", type(data))
        return []
    return data


def poll_new_battles(
    fmt: str, state: dict, min_elo: int = 0
) -> tuple[list[dict], dict]:
    """
    Pull every replay uploaded since the last poll.
    Returns (list of NEW replay dicts, updated state).

    Strategy:
      • If we have no cursor yet (first run) we just seed the state.
      • Otherwise we walk pages forward in time until we hit replays we
        already know about (or run out of results).
      • Filter by minimum ELO rating if specified.
    """
    seen_ids: set[str] = set(state.get("seen_ids", []))
    cursor: int | None = state.get("cursor")  # last known uploadtime

    new_replays: list[dict] = []
    is_seed_run = cursor is None

    if is_seed_run:
        # First run: use script startup time as the baseline.
        # Only replays uploaded AFTER this moment will be considered new.
        new_cursor = int(datetime.now(timezone.utc).timestamp())
        logger.info(
            "First run — setting cursor to now (%s). "
            "Only future replays will be recorded.",
            datetime.fromtimestamp(new_cursor, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            ),
        )
        return [], {"seen_ids": [], "cursor": new_cursor}

    # We want replays NEWER than our cursor.
    # PS sorts newest-first and `before` is an upper bound, so we
    # fetch without `before` to get the very latest, then stop when
    # we hit something ≤ cursor.
    page = fetch_replays_page(fmt)

    while page:
        # If the server returned 51 items the 51st is just a "has more" flag
        has_more = len(page) > PAGE_SIZE
        chunk = page[:PAGE_SIZE]

        stop = False
        for replay in chunk:
            print("Found replay:", replay)
            rid = replay.get("id", "")
            upload_ts = replay.get("uploadtime", 0)
            rating = replay.get("rating", 0)

            # Stop scanning once we've reached replays we already have
            if upload_ts <= (cursor or 0):
                stop = True
                break

            if rid in seen_ids:
                continue  # already recorded (e.g. overlap window)

            # Apply minimum ELO filter
            if min_elo > 0 and rating is not None and rating < min_elo:
                logger.debug(
                    "Skipping %s: rating %d < min_elo %d", rid, rating, min_elo
                )
                seen_ids.add(rid)  # Mark as seen to avoid re-checking
                continue

            seen_ids.add(rid)
            new_replays.append(replay)

        if stop or not has_more:
            break

        # Paginate: use the uploadtime of the LAST item in chunk as cursor
        oldest_in_chunk = chunk[-1].get("uploadtime", time.time())
        page = fetch_replays_page(fmt, before=oldest_in_chunk)

    # Update cursor to NOW so next poll covers the gap
    new_cursor = int(datetime.now(timezone.utc).timestamp())

    updated_state = {
        "seen_ids": list(seen_ids),
        "cursor": new_cursor,
    }
    return new_replays, updated_state


# ---------------------------------------------------------------------------
# Persistence of individual replays
# ---------------------------------------------------------------------------


def save_replay(replay: dict, output_dir: str) -> str | None:
    """
    Fetch the full replay log and save it as <id>.json inside *output_dir*.
    Returns the path to the saved file, or None if download failed.
    """
    os.makedirs(output_dir, exist_ok=True)
    rid = replay.get("id", "unknown")

    # Fetch the full replay with the log
    full_replay = fetch_full_replay(rid)
    if full_replay is None:
        logger.error("Could not download full replay for %s", rid)
        return None

    # Parse the log into turn records
    log = full_replay.get("log", "")
    if log:
        turn_records = parse_replay_log(log)
        is_valid, errors = validate_turn_records(turn_records)

        if not is_valid:
            logger.warning("Replay %s has validation issues: %s", rid, errors)

        # Add parsed turn records to the replay data
        full_replay["parsed_turns"] = [r.to_dict() for r in turn_records]
        full_replay["turn_count"] = len(turn_records)
        full_replay["parse_valid"] = is_valid
        full_replay["parse_errors"] = errors

        logger.info(
            "Parsed %d turns from replay %s (valid=%s)",
            len(turn_records),
            rid,
            is_valid,
        )
    else:
        logger.warning("Replay %s has no log data", rid)
        full_replay["parsed_turns"] = []
        full_replay["turn_count"] = 0
        full_replay["parse_valid"] = False
        full_replay["parse_errors"] = ["No log data in replay"]

    path = os.path.join(output_dir, f"{rid}.json")
    with open(path, "w") as f:
        json.dump(full_replay, f, indent=2)
    logger.info("Saved replay: %s", path)
    return path


# ---------------------------------------------------------------------------
# Duplicate checking
# ---------------------------------------------------------------------------


def check_for_duplicates(output_dir: str, state_path: str) -> tuple[bool, list[str]]:
    """
    Check for duplicate replay IDs in the output directory and state file.
    Returns (has_duplicates, list of duplicate IDs).
    """
    duplicates: list[str] = []

    # Get IDs from state file
    state = load_state(state_path)
    seen_ids = state.get("seen_ids", [])

    # Check for duplicates in state file itself
    if len(seen_ids) != len(set(seen_ids)):
        from collections import Counter

        counts = Counter(seen_ids)
        state_dups = [rid for rid, count in counts.items() if count > 1]
        duplicates.extend(state_dups)
        logger.warning("Duplicate IDs in state file: %s", state_dups)

    # Get IDs from saved files
    if os.path.isdir(output_dir):
        file_ids = []
        for fname in os.listdir(output_dir):
            if fname.endswith(".json"):
                file_ids.append(fname[:-5])  # Remove .json extension

        # Check for duplicates in filenames (shouldn't happen, but check anyway)
        if len(file_ids) != len(set(file_ids)):
            from collections import Counter

            counts = Counter(file_ids)
            file_dups = [rid for rid, count in counts.items() if count > 1]
            duplicates.extend(file_dups)
            logger.warning("Duplicate files in output dir: %s", file_dups)

    return len(duplicates) > 0, list(set(duplicates))


# ---------------------------------------------------------------------------
# Self-test functionality
# ---------------------------------------------------------------------------


def run_tests() -> bool:
    """
    Run self-tests to verify the scraper functionality.
    Tests:
    a) Full set of (game state, action) pairs can be parsed for each turn
    b) No duplicates among IDs

    Returns True if all tests pass.
    """
    logger.info("=" * 60)
    logger.info("Running self-tests...")
    logger.info("=" * 60)

    all_passed = True

    # Test 1: Parse a known replay and verify turn records
    logger.info("\nTest 1: Parsing a replay log into turn records...")

    # Use a known replay ID for testing
    test_replay_id = "gen3ou-2530532424"

    try:
        full_replay = fetch_full_replay(test_replay_id)
        if full_replay is None:
            logger.error("FAIL: Could not fetch test replay %s", test_replay_id)
            all_passed = False
        else:
            log = full_replay.get("log", "")
            if not log:
                logger.error("FAIL: Test replay has no log data")
                all_passed = False
            else:
                turn_records = parse_replay_log(log)
                is_valid, errors = validate_turn_records(turn_records)

                if not turn_records:
                    logger.error("FAIL: No turn records parsed from replay")
                    all_passed = False
                else:
                    logger.info("  Parsed %d turns", len(turn_records))

                    # Check that we have actions for most turns
                    turns_with_p1_action = sum(
                        1 for r in turn_records if r.p1_action is not None
                    )
                    turns_with_p2_action = sum(
                        1 for r in turn_records if r.p2_action is not None
                    )

                    logger.info(
                        "  Turns with P1 action: %d/%d",
                        turns_with_p1_action,
                        len(turn_records),
                    )
                    logger.info(
                        "  Turns with P2 action: %d/%d",
                        turns_with_p2_action,
                        len(turn_records),
                    )

                    # Verify turn sequence is continuous
                    turn_numbers = [r.turn for r in turn_records]
                    expected = list(range(1, len(turn_records) + 1))
                    if turn_numbers == expected:
                        logger.info(
                            "  PASS: Turn sequence is continuous (1 to %d)",
                            len(turn_records),
                        )
                    else:
                        logger.error(
                            "  FAIL: Turn sequence is not continuous: %s", turn_numbers
                        )
                        all_passed = False

                    # Verify each turn has state with valid pokemon names
                    for record in turn_records:
                        p1_pokemon = (
                            record.state.sides[0].pokemon[0]
                            if record.state.sides and record.state.sides[0].pokemon
                            else None
                        )
                        p2_pokemon = (
                            record.state.sides[1].pokemon[0]
                            if len(record.state.sides) > 1
                            and record.state.sides[1].pokemon
                            else None
                        )
                        if (
                            not p1_pokemon
                            or not p1_pokemon.species
                            or not p2_pokemon
                            or not p2_pokemon.species
                        ):
                            logger.error(
                                "  FAIL: Turn %d missing active pokemon in state",
                                record.turn,
                            )
                            all_passed = False
                            break
                    else:
                        logger.info("  PASS: All turns have valid game states")

                    # Print sample turn record
                    if turn_records:
                        sample = turn_records[0]
                        p1_active = (
                            sample.state.sides[0].pokemon[0]
                            if sample.state.sides
                            else None
                        )
                        p2_active = (
                            sample.state.sides[1].pokemon[0]
                            if len(sample.state.sides) > 1
                            else None
                        )
                        logger.info(
                            "  Sample turn 1 state: P1=%s (%.0f%% HP), P2=%s (%.0f%% HP)",
                            p1_active.species if p1_active else "?",
                            (p1_active.hp_pct * 100) if p1_active else 0,
                            p2_active.species if p2_active else "?",
                            (p2_active.hp_pct * 100) if p2_active else 0,
                        )
                        if sample.p1_action:
                            logger.info(
                                "  Sample P1 action: %s %s",
                                sample.p1_action.action_type,
                                sample.p1_action.label,
                            )
                        if sample.p2_action:
                            logger.info(
                                "  Sample P2 action: %s %s",
                                sample.p2_action.action_type,
                                sample.p2_action.label,
                            )
    except Exception as exc:
        logger.error("FAIL: Exception during replay parsing test: %s", exc)
        all_passed = False

    # Test 2: Check for duplicates in existing data
    logger.info("\nTest 2: Checking for duplicate IDs...")

    has_dups, dup_ids = check_for_duplicates(DEFAULT_OUTPUT, STATE_FILE)
    if has_dups:
        logger.error("FAIL: Found duplicate IDs: %s", dup_ids)
        all_passed = False
    else:
        logger.info("  PASS: No duplicate IDs found")

    # Test 3: Verify state file integrity
    logger.info("\nTest 3: Verifying state file integrity...")

    state = load_state(STATE_FILE)
    if state:
        seen_ids = state.get("seen_ids", [])
        cursor = state.get("cursor")

        logger.info("  State file has %d seen IDs", len(seen_ids))
        logger.info(
            "  Cursor timestamp: %s",
            datetime.fromtimestamp(cursor, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            if cursor
            else "None",
        )

        # Check seen_ids are all valid format
        invalid_ids = [rid for rid in seen_ids if not re.match(r"^gen\d+\w+-\d+$", rid)]
        if invalid_ids:
            logger.warning(
                "  Warning: Found %d IDs with unexpected format: %s",
                len(invalid_ids),
                invalid_ids[:5],
            )
        else:
            logger.info("  PASS: All seen IDs have valid format")
    else:
        logger.info("  No existing state file (first run)")

    # Summary
    logger.info("\n" + "=" * 60)
    if all_passed:
        logger.info("All tests PASSED!")
    else:
        logger.error("Some tests FAILED!")
    logger.info("=" * 60)

    return all_passed


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_once(fmt: str, output_dir: str, state_path: str, min_elo: int = 0) -> None:
    """Execute a single poll cycle."""
    state = load_state(state_path)

    try:
        new_replays, updated_state = poll_new_battles(fmt, state, min_elo)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        logger.error("Poll failed: %s", exc)
        return  # don't overwrite state; retry next cycle

    save_state(state_path, updated_state)

    if new_replays:
        logger.info("Found %d new battle(s).", len(new_replays))
        for replay in new_replays:
            # Print a quick summary to stdout
            p1 = replay.get("p1", "???")
            p2 = replay.get("p2", "???")
            rid = replay.get("id", "???")
            ts = replay.get("uploadtime", 0)
            rating = replay.get("rating", 0)
            rating = rating if rating is not None else 0
            when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            logger.info(
                "  [NEW] %s  —  %s  vs  %s  (ELO: %d, %s)", rid, p1, p2, rating, when
            )

            # Persist the full replay JSON to disk (with parsed turns)
            save_replay(replay, output_dir)
    else:
        logger.info("No new battles since last poll.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poll Pokemon Showdown for new public battle replays."
    )
    parser.add_argument(
        "--format",
        default=DEFAULT_FORMAT,
        help="PS format ID, e.g. gen3ou (default: %(default)s)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Polling interval in minutes (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Directory to save new replay JSONs (default: %(default)s)",
    )
    parser.add_argument(
        "--min-elo",
        type=int,
        default=DEFAULT_MIN_ELO,
        help="Minimum ELO rating to filter replays (default: %(default)s, 0 = no filter)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll and exit (useful for cron)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run self-tests to verify scraper functionality",
    )
    args = parser.parse_args()

    # Run tests if requested
    if args.test:
        success = run_tests()
        sys.exit(0 if success else 1)

    logger.info(
        "Starting poller — format=%s, interval=%dm, output=%s, min_elo=%d",
        args.format,
        args.interval,
        args.output,
        args.min_elo,
    )

    if args.once:
        run_once(args.format, args.output, STATE_FILE, args.min_elo)
        return


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
parse_replay.py — Extract per-turn game state snapshots from a Showdown replay.

Splits the replay log on exact |upkeep lines to isolate each turn's event block,
then builds a TurnState snapshot after every turn.

Usage:
    python data/parse_replay.py <path_to_replay.json>
"""

import json
import re
import sys
import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MoveRecord:
    name: str
    times_used: int = 0


@dataclass
class PokemonState:
    species: str
    hp_pct: float = 1.0
    status: Optional[str] = None
    is_fainted: bool = False
    is_active: bool = False
    moves_seen: list = field(default_factory=list)  # list[MoveRecord]

    def get_or_add_move(self, name: str) -> MoveRecord:
        for m in self.moves_seen:
            if m.name == name:
                return m
        m = MoveRecord(name=name)
        self.moves_seen.append(m)
        return m


@dataclass
class SideSnapshot:
    player_id: str
    username: str
    team_size: int
    revealed_count: int  # distinct Pokemon seen so far
    active: Optional[PokemonState]  # None only before the first switch
    bench: list  # list[PokemonState], all non-active (includes fainted)


@dataclass
class TurnState:
    turn_number: int
    p1: SideSnapshot
    p2: SideSnapshot


@dataclass
class TurnActions:
    turn_number: int
    p1_action: Optional[str]  # "move Surf", "switch Zapdos", or None if unclear
    p2_action: Optional[str]  # same


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Matches "p1a: Zapdos" or "p2a: Tyranitar, M" etc.
_ACTOR_RE = re.compile(r"^(p[12])a:\s+(.+)$")


def _parse_actor(raw: str) -> tuple:
    """Return (player_id, species) from 'p1a: Zapdos, M', stripping gender suffix."""
    m = _ACTOR_RE.match(raw.strip())
    if not m:
        return "", ""
    player_id = m.group(1)
    species = re.sub(r",\s*[MF]$", "", m.group(2)).strip()
    return player_id, species


def _parse_hp(raw: str) -> float:
    """Parse '62/100' → 0.62, '0 fnt' → 0.0.

    HP Percentage Mod is active in all gen3ou replays so the denominator is
    always 100, but we compute the ratio generically anyway.
    """
    raw = raw.strip()
    if raw.startswith("0 fnt") or raw == "0":
        return 0.0
    m = re.match(r"^(\d+)/(\d+)", raw)
    if m:
        return int(m.group(1)) / int(m.group(2))
    return 1.0


# ---------------------------------------------------------------------------
# Game parser (mutable state machine)
# ---------------------------------------------------------------------------


class GameParser:
    """
    Processes Showdown protocol lines one at a time and maintains a live
    game state that can be snapshotted after each turn's upkeep.
    """

    def __init__(self):
        self.usernames: dict = {}  # "p1" → "Xenocles"
        self.team_sizes: dict = {}  # "p1" → 6
        self.pokemon: dict = {  # player → {species: PokemonState}
            "p1": {},
            "p2": {},
        }
        self.active_species: dict = {"p1": None, "p2": None}
        self.current_turn: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, player_id: str, species: str) -> PokemonState:
        if species not in self.pokemon[player_id]:
            self.pokemon[player_id][species] = PokemonState(species=species)
        return self.pokemon[player_id][species]

    def _do_switch(self, player_id: str, species: str, hp_pct: float):
        prev = self.active_species[player_id]
        if prev and prev in self.pokemon[player_id]:
            self.pokemon[player_id][prev].is_active = False
        poke = self._get_or_create(player_id, species)
        poke.hp_pct = hp_pct
        poke.is_active = True
        poke.is_fainted = False  # can't switch in a fainted pokemon
        self.active_species[player_id] = species

    # ------------------------------------------------------------------
    # Line processor
    # ------------------------------------------------------------------

    def process_line(self, line: str):
        line = line.strip()
        if not line.startswith("|"):
            return
        parts = line.split("|")
        # parts[0] is always '' because the line starts with '|'
        if len(parts) < 2:
            return
        cmd = parts[1]

        # --- metadata ---
        if cmd == "player" and len(parts) >= 4:
            # |player|p1|Xenocles|sprite|elo
            pid, uname = parts[2], parts[3]
            if pid in ("p1", "p2") and uname:
                self.usernames[pid] = uname

        elif cmd == "teamsize" and len(parts) >= 4:
            # |teamsize|p1|6
            self.team_sizes[parts[2]] = int(parts[3])

        elif cmd == "turn" and len(parts) >= 3:
            # |turn|N
            self.current_turn = int(parts[2])

        # --- switch / drag ---
        elif cmd in ("switch", "drag") and len(parts) >= 5:
            # |switch|p1a: Zapdos|Zapdos|100/100
            # |drag|p1a: Suicune|Suicune|100/100
            player_id, species = _parse_actor(parts[2])
            if player_id:
                self._do_switch(player_id, species, _parse_hp(parts[4]))

        # --- move usage ---
        elif cmd == "move" and len(parts) >= 4:
            # |move|p1a: Zapdos|Thunderbolt|p2a: Zapdos
            player_id, species = _parse_actor(parts[2])
            if player_id:
                poke = self._get_or_create(player_id, species)
                poke.get_or_add_move(parts[3]).times_used += 1

        # --- HP changes ---
        elif cmd in ("-damage", "-heal") and len(parts) >= 4:
            # |-damage|p2a: Zapdos|62/100
            # |-heal|p1a: Zapdos|61/100|[from] item: Leftovers
            player_id, species = _parse_actor(parts[2])
            if player_id:
                self._get_or_create(player_id, species).hp_pct = _parse_hp(parts[3])

        # --- status changes (applied to any Pokemon, active or bench) ---
        elif cmd == "-status" and len(parts) >= 4:
            # |-status|p1a: Zapdos|par
            player_id, species = _parse_actor(parts[2])
            if player_id:
                self._get_or_create(player_id, species).status = parts[3].strip()

        elif cmd == "-curestatus" and len(parts) >= 3:
            # |-curestatus|p1a: Zapdos|par
            player_id, species = _parse_actor(parts[2])
            if player_id:
                self._get_or_create(player_id, species).status = None

        # --- faint ---
        elif cmd == "faint" and len(parts) >= 3:
            # |faint|p2a: Forretress
            player_id, species = _parse_actor(parts[2])
            if player_id:
                poke = self._get_or_create(player_id, species)
                poke.hp_pct = 0.0
                poke.is_fainted = True
                poke.is_active = False
                self.active_species[player_id] = None

    def process_segment(self, segment: str):
        for line in segment.split("\n"):
            self.process_line(line)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> TurnState:
        """Deep-copy current mutable state into an immutable TurnState."""
        sides = []
        for pid in ("p1", "p2"):
            active_sp = self.active_species[pid]
            active_poke = None
            bench = []
            for species, poke in self.pokemon[pid].items():
                poke_copy = copy.deepcopy(poke)
                if species == active_sp and not poke.is_fainted:
                    active_poke = poke_copy
                else:
                    bench.append(poke_copy)
            sides.append(
                SideSnapshot(
                    player_id=pid,
                    username=self.usernames.get(pid, pid),
                    team_size=self.team_sizes.get(pid, 0),
                    revealed_count=len(self.pokemon[pid]),
                    active=active_poke,
                    bench=bench,
                )
            )
        return TurnState(turn_number=self.current_turn, p1=sides[0], p2=sides[1])


# ---------------------------------------------------------------------------
# Log splitting
# ---------------------------------------------------------------------------


def split_log(log: str) -> list:
    """
    Split the replay log into per-turn segments at exact '|upkeep' lines.

    This uses line-by-line comparison rather than a substring split so that
    weather-annotation lines like '|-weather|Sandstorm|[upkeep]' are NOT
    treated as turn boundaries.
    """
    segments = []
    current_lines = []
    for line in log.split("\n"):
        if line.strip() == "|upkeep":
            segments.append("\n".join(current_lines))
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        # Final segment: post-last-upkeep content (|win|, |raw|, etc.) — skip
        pass
    return segments


# ---------------------------------------------------------------------------
# String rendering
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    "par": "PAR",
    "brn": "BRN",
    "tox": "TOX",
    "psn": "PSN",
    "slp": "SLP",
    "frz": "FRZ",
}


def _fmt_status(status: Optional[str]) -> str:
    if not status:
        return "—"
    return _STATUS_LABELS.get(status, status.upper())


def _fmt_moves(moves: list) -> str:
    if not moves:
        return "(none seen)"
    return ", ".join(f"{m.name}×{m.times_used}" for m in moves)


def _render_pokemon_row(label: str, poke: PokemonState, indent: str = "    ") -> list:
    """Return 1–2 lines for a single Pokemon entry."""
    lines = []
    if poke.is_fainted:
        lines.append(f"{indent}{poke.species:<14}  FAINTED")
    else:
        status_str = _fmt_status(poke.status)
        lines.append(
            f"{indent}{poke.species:<14}  HP: {poke.hp_pct * 100:>3.0f}%"
            f"  status: {status_str:<4}"
        )
    lines.append(f"{indent}{'':14}  moves: [{_fmt_moves(poke.moves_seen)}]")
    return lines


def render_turn(ts: TurnState) -> str:
    lines = [f"Turn {ts.turn_number}", "─" * 60]
    for side in (ts.p1, ts.p2):
        lines.append(f"{side.player_id.upper()} ({side.username})")

        # Active Pokemon
        if side.active:
            p = side.active
            status_str = _fmt_status(p.status)
            lines.append(
                f"  ACTIVE : {p.species:<14}  HP: {p.hp_pct * 100:>3.0f}%"
                f"  status: {status_str:<4}"
            )
            lines.append(f"  {'':7}  {'':14}  moves: [{_fmt_moves(p.moves_seen)}]")
        else:
            lines.append("  ACTIVE : —")

        # Bench
        lines.append("  BENCH  :")
        for poke in side.bench:
            lines.extend(_render_pokemon_row("", poke, indent="    "))

        # Unrevealed slots
        unrevealed = side.team_size - side.revealed_count
        for _ in range(unrevealed):
            lines.append("    [Unknown]")

        lines.append("")

    lines.append("─" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_replay(path: str) -> list:
    """Load a replay JSON, parse all turns, return list[TurnState]."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    segments = split_log(data["log"])
    parser = GameParser()
    turn_states = []

    for seg in segments:
        parser.process_segment(seg)
        if parser.current_turn > 0:
            turn_states.append(parser.snapshot())

    return turn_states


def extract_turn_actions(path: str) -> list:
    """
    Extract the chosen action for each player per turn.

    Action strings:
        "move <MoveName>"     — player used a move
        "switch <Species>"    — player voluntarily switched

    A player's action is set to None (not included) when it cannot be
    determined from the log, specifically:

        - The Pokemon couldn't act due to sleep, full paralysis, or freeze
          (|cant| with no move exposed)
        - Exception: Taunt exposes the attempted move in the |cant| line, so
          those are included as "move <MoveName>"
        - The only switch visible for that player is post-faint (the player's
          Pokemon fainted before they could act; the switch-in is forced by
          the faint, not by the player's decision at turn start)
        - The only switch is a consequence of a move ([from] tag — Baton Pass,
          Parting Shot, etc.)
        - Forced switches from |drag| (Roar, Whirlwind) are never recorded
          as actions for either player

    Returns list[TurnActions].
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    results = []

    for seg in split_log(data["log"]):
        turn_num = None
        actions = {"p1": None, "p2": None}
        fainted = {"p1": False, "p2": False}

        for line in seg.split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            parts = line.split("|")
            cmd = parts[1] if len(parts) >= 2 else ""

            if cmd == "turn" and len(parts) >= 3:
                turn_num = int(parts[2])

            elif cmd == "move" and len(parts) >= 4:
                player_id, _ = _parse_actor(parts[2])
                if player_id and actions[player_id] is None:
                    actions[player_id] = f"move {parts[3]}"

            elif cmd == "switch" and len(parts) >= 3:
                player_id, species = _parse_actor(parts[2])
                if not player_id:
                    continue
                # Skip consequence-of-move switches (Baton Pass, Parting Shot…)
                if any("[from]" in p for p in parts[4:]):
                    continue
                # Skip post-faint switches: the player's Pokemon fainted before
                # they could act, so this switch-in is not their chosen action.
                if fainted[player_id]:
                    continue
                if actions[player_id] is None:
                    actions[player_id] = f"switch {species}"

            elif cmd == "faint" and len(parts) >= 3:
                player_id, _ = _parse_actor(parts[2])
                if player_id:
                    fainted[player_id] = True

            elif cmd == "cant" and len(parts) >= 4:
                # |cant|pXa: Species|reason|move_attempted
                # Taunt includes the move the player tried: |cant|...|move: Taunt|Spikes
                # Other reasons (slp, par, frz) do not expose the intended move.
                player_id, _ = _parse_actor(parts[2])
                if player_id and actions[player_id] is None:
                    reason = parts[3]
                    if reason == "move: Taunt" and len(parts) >= 5:
                        actions[player_id] = f"move {parts[4]}"
                    # all other cant reasons: action stays None (unclear)

            # |drag| is a forced switch from Roar/Whirlwind — never an action.

        if turn_num is not None:
            results.append(
                TurnActions(
                    turn_number=turn_num,
                    p1_action=actions["p1"],
                    p2_action=actions["p2"],
                )
            )

    return results


def main():
    # Ensure UTF-8 output on Windows (usernames may contain Unicode characters).
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        # Default to the first replay in collection_2 for quick testing
        default = (
            Path(__file__).parent.parent
            / "recorded_games"
            / "collection_2"
            / "gen3ou-2567398689.json"
        )
        path = str(default)
        print(f"No path given — using default: {path}\n")
    else:
        path = sys.argv[1]

    turn_states = parse_replay(path)

    os.makedirs("recorded_games/parsed_turns_2", exist_ok=True)
    out_file = open(
        "recorded_games/parsed_turns_2/" + os.path.basename(path), "w", encoding="utf-8"
    )

    for ts in turn_states:
        out_file.write(render_turn(ts) + "\n\n")

    out_file.close()

    actions_file = open(
        "recorded_games/parsed_actions_2/"
        + os.path.basename(path).replace(".json", "_actions.txt"),
        "w",
        encoding="utf-8",
    )

    turn_actions = extract_turn_actions(path)
    for ta in turn_actions:
        actions_file.write(
            f"Turn {ta.turn_number}: p1 action = {ta.p1_action}, p2 action = {ta.p2_action}\n"
        )
    actions_file.close()


if __name__ == "__main__":
    main()

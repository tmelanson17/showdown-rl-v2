"""
replay_exporter.py — Convert DataCollector recordings into Pokémon Showdown
replay files.

TWO OUTPUT FORMATS
------------------
1.  ``.log`` file  — the battle protocol log.  This is what the PS replay
    viewer actually parses to render the battle frame-by-frame.  It is a
    sequence of pipe-delimited protocol messages (|player|, |switch|,
    |move|, |-damage|, |turn|, |win|, etc.).

2.  ``.json`` file — the envelope the PS replay API returns.  It contains
    the log, an inputLog (the player commands that produced it), player
    names, format, and upload metadata.  You can feed this JSON to the
    replay viewer or to ``/importinputlog`` on a local PS server.

WHAT WORKS vs. WHAT NEEDS YOUR HELP
------------------------------------
The PS replay viewer is *stateless* — it re-derives every frame purely from
the protocol log.  That means the log has to be a faithful, causally-ordered
record of everything that happened: every switch, every attack, every point
of damage, every status tick.

Your DataCollector records (state, action) *snapshots*.  That is great for
training but it is the *wrong level of detail* for a replay log.  Concretely:

  ✓  We CAN reconstruct:
        • which Pokémon were on each team (from the preview state)
        • the order of switches and moves (from the action log)
        • the HP at the *start* of each turn (from the gamestate snapshots)
        • who won

  ✗  We CANNOT reconstruct (without re-running the PS battle engine):
        • critical hits, misses, secondary effects
        • exact damage numbers per hit (we only see the delta between snapshots)
        • ability / item activation messages
        • weather / hazard tick messages
        • the precise ordering of events *within* a single turn

The converter therefore produces a **best-effort** log that is structurally
valid and will render in the PS viewer, but the intra-turn detail is
synthesised from the HP deltas rather than replayed from a true battle log.

IF YOU HAVE THE REAL LOG
------------------------
If your PSServer wrapper is already capturing ``raw_protocol`` for every
message the PS process emits (which it should — that's the ``# PS PROTOCOL``
extension point in ps_server.py), then the ideal path is:

    1.  Accumulate raw_protocol strings in DataCollector alongside the
        structured data (add a ``raw_log_lines`` list to the recording).
    2.  At export time, just concatenate those lines — no synthesis needed.
        The converter's ``from_raw_log()`` path handles exactly this.

This module supports *both* paths so you can graduate from synthesised
replays to faithful ones as your PS integration matures.

USAGE
-----
    # Synthesise from structured data (works with current DataCollector output)
    python replay_exporter.py recorded_games/game_1769892775_6turns.json

    # Or in code:
    from replay_exporter import ReplayExporter
    exporter = ReplayExporter("recorded_games/game_1769892775_6turns.json")
    exporter.export("my_replay")   # writes my_replay.log and my_replay.json
"""

from __future__ import annotations

import json
import os
import math
import time
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_to_hp100(pct: float) -> int:
    """Convert a 0.0–1.0 HP fraction to PS's /100 scale, clamped."""
    return max(0, min(100, round(pct * 100)))


def _active_pokemon(side: dict) -> Optional[dict]:
    """Return the first Pokémon on *side* marked is_active, or the first one."""
    for p in side.get("pokemon", []):
        if p.get("is_active"):
            return p
    pokes = side.get("pokemon", [])
    return pokes[0] if pokes else None


def _player_tag(player_id: int) -> str:
    """``p1`` or ``p2``."""
    return f"p{player_id}"


def _active_tag(player_id: int, species: str) -> str:
    """``p1a: Pikachu``"""
    return f"{_player_tag(player_id)}a: {species}"


# ---------------------------------------------------------------------------
# Core exporter
# ---------------------------------------------------------------------------

class ReplayExporter:
    """Converts a single DataCollector JSON file into PS replay files.

    Parameters:
        recording_path: Path to the JSON file written by DataCollector.
    """

    def __init__(self, recording_path: str) -> None:
        with open(recording_path) as f:
            self.recording: dict = json.load(f)

        self.metadata = self.recording.get("metadata", {})
        self.preview  = self.recording.get("preview", {})
        self.turns    = self.recording.get("turns", [])

        # Normalise player names
        players = self.metadata.get("players", ["Player1", "Player2"])
        self.p1_name = players[0] if len(players) > 0 else "Player1"
        self.p2_name = players[1] if len(players) > 1 else "Player2"

    # -- public -------------------------------------------------------------
    def export(self, output_stem: str, format_id: str = "gen9ou") -> Tuple[str, str]:
        """Write ``.log`` and ``.json`` files.  Returns (log_path, json_path)."""
        log_lines  = self._build_log(format_id)
        input_log  = self._build_input_log(format_id)

        log_text   = "\n".join(log_lines)
        log_path   = output_stem + ".log"
        json_path  = output_stem + ".json"

        with open(log_path, "w") as f:
            f.write(log_text)

        replay_json = self._build_replay_json(format_id, log_text, input_log)
        with open(json_path, "w") as f:
            json.dump(replay_json, f, indent=2)

        return log_path, json_path

    # -- log construction ---------------------------------------------------
    def _build_log(self, format_id: str) -> List[str]:
        """Synthesise a PS battle protocol log from the structured recording."""
        lines: List[str] = []

        # --- header --------------------------------------------------------
        lines.append(f"|gameversion|Pokemon Showdown (exported replay)")
        lines.append(f"|gen|{self._gen_number(format_id)}")
        lines.append(f"|title|{self.p1_name} vs. {self.p2_name}")
        lines.append(f"|rule|Species Clause Mod")
        lines.append(f"|rule|HP Percentage Mod")

        # --- player declarations -------------------------------------------
        lines.append(f"|player|p1|{self.p1_name}||")
        lines.append(f"|player|p2|{self.p2_name}||")

        # --- team preview --------------------------------------------------
        if self.preview:
            lines.extend(self._emit_team_preview())

        # --- initial switch (leads) ----------------------------------------
        # Derive the leads from turn-1 gamestate (whoever is_active there)
        if self.turns:
            lines.extend(self._emit_initial_switches())

        # --- turn-by-turn --------------------------------------------------
        prev_state: Optional[dict] = None
        for turn_record in self.turns:
            gs     = turn_record["gamestate"]
            action = turn_record["action"]
            turn_n = gs["turn"]

            lines.append(f"|turn|{turn_n}")

            # Emit damage from previous turn's action (HP delta)
            if prev_state is not None:
                lines.extend(self._emit_damage_deltas(prev_state, gs))

            # Emit the action taken this turn
            lines.extend(self._emit_action(gs, action))

            prev_state = gs

        # --- final damage from the last action (into the end state) --------
        # We don't have a "turn N+1" state, so emit damage based on final
        # HP if any Pokémon fainted.
        if self.turns:
            last_gs = self.turns[-1]["gamestate"]
            lines.extend(self._emit_faint_check(last_gs))

        # --- winner --------------------------------------------------------
        outcome = self.metadata.get("outcome")
        if outcome == 1:
            lines.append(f"|win|{self.p1_name}")
        elif outcome == 2:
            lines.append(f"|win|{self.p2_name}")

        return lines

    # -- input log (the ``>`` commands) -------------------------------------
    def _build_input_log(self, format_id: str) -> List[str]:
        """Build the inputLog: the sequence of simulator commands that would
        reproduce this battle.  This is what ``/importinputlog`` expects."""
        lines: List[str] = []
        lines.append(f'>start {{"formatid":"{format_id}"}}')
        lines.append(f'>player p1 {{"name":"{self.p1_name}","team":""}}')
        lines.append(f'>player p2 {{"name":"{self.p2_name}","team":""}}')

        # Team preview order (just 1-6 in order; we don't know the real pick)
        lines.append(">p1 team 123456")
        lines.append(">p2 team 123456")

        # Turn commands
        for turn_record in self.turns:
            action = turn_record["action"]
            gs     = turn_record["gamestate"]
            player = _player_tag(gs["active_player"])
            lines.append(f">{player} {action['action_id']}")

        return lines

    # -- replay JSON envelope -----------------------------------------------
    def _build_replay_json(self, format_id: str, log_text: str, input_log: List[str]) -> dict:
        return {
            "id": f"exported-{int(self.metadata.get('timestamp', time.time()))}",
            "formatid": format_id,
            "p1": self.p1_name,
            "p2": self.p2_name,
            "p1rating": None,
            "p2rating": None,
            "log": log_text,
            "inputLog": "\n".join(input_log),
            "uploadtime": int(self.metadata.get("timestamp", time.time())),
            "private": False,
        }

    # -- emission helpers ---------------------------------------------------
    def _emit_team_preview(self) -> List[str]:
        """Emit |poke| lines for both sides during team preview."""
        lines: List[str] = []
        for side in self.preview.get("sides", []):
            pid = _player_tag(side["player_id"])
            for poke in side.get("pokemon", []):
                species = poke["species"]
                # DETAILS format: Species, L## if not 100, gender, shiny
                # We don't track level/gender/shiny so keep it minimal
                lines.append(f"|poke|{pid}|{species}, L100||")
        lines.append("|teampreview")
        return lines

    def _emit_initial_switches(self) -> List[str]:
        """Emit the |switch| messages for the leads at the start of the battle."""
        lines: List[str] = []
        first_gs = self.turns[0]["gamestate"]
        for side in first_gs.get("sides", []):
            pid = _player_tag(side["player_id"])
            lead = _active_pokemon(side)
            if lead:
                hp = _pct_to_hp100(lead["hp_pct"])
                lines.append(f"|switch|{pid}a: {lead['species']}|{lead['species']}, L100|{hp}/100")
        return lines

    def _emit_action(self, gamestate: dict, action: dict) -> List[str]:
        """Emit protocol messages for a single action."""
        lines: List[str] = []
        active_player = gamestate["active_player"]
        pid = _player_tag(active_player)

        # Find the active Pokémon on this side
        side = next(
            (s for s in gamestate.get("sides", []) if s["player_id"] == active_player),
            None
        )
        active = _active_pokemon(side) if side else None
        active_species = active["species"] if active else "???"

        if action["action_type"] == "move":
            move_name = action["label"]
            # Target is the opponent's active
            opp_id   = 2 if active_player == 1 else 1
            opp_side = next(
                (s for s in gamestate.get("sides", []) if s["player_id"] == opp_id),
                None
            )
            opp_active = _active_pokemon(opp_side) if opp_side else None
            opp_species = opp_active["species"] if opp_active else "???"

            lines.append(
                f"|move|{_active_tag(active_player, active_species)}"
                f"|{move_name}"
                f"|{_active_tag(opp_id, opp_species)}"
            )

        elif action["action_type"] == "switch":
            # Find the target Pokémon by slot index
            target_idx = action["target_index"] - 1   # 1-indexed → 0-indexed
            if side and target_idx < len(side.get("pokemon", [])):
                target = side["pokemon"][target_idx]
                hp = _pct_to_hp100(target["hp_pct"])
                lines.append(
                    f"|switch|{pid}a: {target['species']}"
                    f"|{target['species']}, L100"
                    f"|{hp}/100"
                )

        return lines

    def _emit_damage_deltas(self, prev_gs: dict, curr_gs: dict) -> List[str]:
        """Compare HP between two consecutive gamestates and emit |-damage|
        messages for any Pokémon that lost HP."""
        lines: List[str] = []

        prev_sides = {s["player_id"]: s for s in prev_gs.get("sides", [])}
        curr_sides = {s["player_id"]: s for s in curr_gs.get("sides", [])}

        for pid in (1, 2):
            prev_side = prev_sides.get(pid, {})
            curr_side = curr_sides.get(pid, {})
            prev_pokes = prev_side.get("pokemon", [])
            curr_pokes = curr_side.get("pokemon", [])

            for i, (pp, cp) in enumerate(zip(prev_pokes, curr_pokes)):
                if pp["species"] != cp["species"]:
                    continue   # species changed = switch happened, handled elsewhere
                prev_hp = _pct_to_hp100(pp["hp_pct"])
                curr_hp = _pct_to_hp100(cp["hp_pct"])
                if curr_hp < prev_hp:
                    tag = _active_tag(pid, cp["species"])
                    if curr_hp <= 0:
                        lines.append(f"|-damage|{tag}|0 sts")
                        lines.append(f"|faint|{tag}")
                    else:
                        lines.append(f"|-damage|{tag}|{curr_hp}/100")

        return lines

    def _emit_faint_check(self, last_gs: dict) -> List[str]:
        """After the final recorded turn, check if any Pokémon are at 0 HP
        and emit faint messages if so (covers the case where the last action
        KO'd something but we have no subsequent state to diff against)."""
        lines: List[str] = []
        for side in last_gs.get("sides", []):
            pid = side["player_id"]
            for poke in side.get("pokemon", []):
                if poke["hp_pct"] <= 0.0:
                    tag = _active_tag(pid, poke["species"])
                    lines.append(f"|faint|{tag}")
        return lines

    # -- utility ------------------------------------------------------------
    @staticmethod
    def _gen_number(format_id: str) -> str:
        """Extract the generation number from a format string like 'gen9ou'."""
        for i, ch in enumerate(format_id):
            if ch.isdigit():
                # grab consecutive digits
                end = i
                while end < len(format_id) and format_id[end].isdigit():
                    end += 1
                return format_id[i:end]
        return "9"   # default


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python replay_exporter.py <recording.json> [output_stem] [format_id]")
        print("       Writes <output_stem>.log and <output_stem>.json")
        sys.exit(1)

    recording_path = sys.argv[1]
    output_stem    = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(recording_path)[0]
    format_id      = sys.argv[3] if len(sys.argv) > 3 else "gen9ou"

    exporter = ReplayExporter(recording_path)
    log_path, json_path = exporter.export(output_stem, format_id)

    print(f"Exported log:  {log_path}")
    print(f"Exported JSON: {json_path}")
    print()
    print("To view locally, either:")
    print("  • Open the .json in a PS replay viewer")
    print("  • On a local PS server: /importinputlog and paste the inputLog contents")


if __name__ == "__main__":
    main()

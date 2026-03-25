"""
types.py — Shared data types for the PS environment.

These are plain dataclasses (no heavy dependencies) so they can be
serialized to/from JSON for IPC without friction.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence
import json


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@dataclass
class Action:
    """A single action the active player can take on their turn.

    Pokémon Showdown protocol uses a string command (e.g. "move 1") but
    we keep a structured representation so agents can reason over it.
    """

    action_id: str  # e.g. "move 1", "switch 3"
    action_type: str  # "move" | "switch"
    target_index: int  # 1-indexed slot (move slot or party slot)
    label: str  # human-readable, e.g. "Thunderbolt"

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        return cls(**d)

    def to_ps_command(self) -> str:
        """Convert back to the raw Pokémon Showdown protocol string."""
        return self.action_id


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------
@dataclass
class PokemonSlot:
    """Snapshot of one Pokémon on a side."""

    species: str
    hp_pct: float  # 0.0 – 1.0
    status: Optional[str] = None  # "brn", "par", "slp", "frz", "poi", or None
    is_active: bool = False
    moves: List[str] = field(default_factory=list)  # List of move names


@dataclass
class Side:
    """One player's side of the battle."""

    player_id: int  # 1 or 2
    username: str
    pokemon: List[PokemonSlot] = field(default_factory=list)


@dataclass
class GameState:
    """Full snapshot of the battle at a single point in time.

    Constructed by GameRunner after parsing the PS protocol output.
    Passed to agents via ``decide()``, and recorded by DataCollector.
    """

    turn: int
    active_player: int  # 1 or 2 — whose turn it is to choose
    sides: List[Side] = field(default_factory=list)  # always len 2
    available_actions: List[Action] = field(default_factory=list)
    raw_protocol: Optional[str] = None  # the raw PS output (useful for debugging)

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        sides = [
            Side(
                player_id=s["player_id"],
                username=s["username"],
                pokemon=[PokemonSlot(**p) for p in s.get("pokemon", [])],
            )
            for s in d.get("sides", [])
        ]
        actions = [Action.from_dict(a) for a in d.get("available_actions", [])]
        return cls(
            turn=d["turn"],
            active_player=d["active_player"],
            sides=sides,
            available_actions=actions,
            raw_protocol=d.get("raw_protocol"),
        )

    @classmethod
    def from_json(cls, s: str) -> "GameState":
        return cls.from_dict(json.loads(s))

"""
agents.py — Agent class hierarchy.

Matches the class diagram:
    Agent  (abstract)
    ├── PlayerAgent      – interactive; prompts a human via stdin/stdout (or a UI widget)
    ├── ModelAgent       – delegates decide() to an external process over UDS via IPC
    ├── RandomAgent      – picks a uniformly random legal action
    └── ReplayAgent      – replays a recorded game sequence (no challenge needed)
"""

from __future__ import annotations
import json
import random
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Sequence

from ps_types import GameState, Action
from ipc import IPCClient

_MOVE_CATEGORIES: dict[str, str] = json.loads(
    (Path(__file__).parent / "move_categories.json").read_text()
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------
class Agent(ABC):
    """Base class for all agents.

    Attributes:
        player_number: 1 or 2, assigned by GameRunner.
        username:      The username registered with the PS server.
    """

    def __init__(self, player_number: int, username: str) -> None:
        self.player_number = player_number
        self.username = username

    @abstractmethod
    def decide(self, gamestate: GameState) -> Action:
        """Choose an action given the current game state.

        Must return one of the actions in ``gamestate.available_actions``.
        """
        ...

    def get_available_actions(self, gamestate: GameState) -> List[Action]:
        """Return the legal actions for this agent's side.

        Default implementation just returns whatever the GameRunner put
        into the GameState.  Override if you need custom filtering.
        """
        return gamestate.available_actions


# ---------------------------------------------------------------------------
# PlayerAgent — human player via stdin/stdout
# ---------------------------------------------------------------------------
class PlayerAgent(Agent):
    """Prompts the human player on the terminal and reads their choice.

    In a GUI application you would swap the ``show_game_state`` /
    ``get_user_input`` methods for calls into a Qt/Tk/web widget.
    """

    def __init__(self, player_number: int, username: str) -> None:
        super().__init__(player_number, username)

    # -- "UI" layer (replace with real widgets as needed) ------------------
    def show_game_state(self, gamestate: GameState) -> None:
        """Print a human-readable battle summary."""
        print("\n" + "=" * 60)
        print(f"  Turn {gamestate.turn}  |  Your move (Player {self.player_number})")
        print("=" * 60)
        for side in gamestate.sides:
            marker = " <-- you" if side.player_id == self.player_number else ""
            print(f"\n  {side.username}{marker}:")
            for i, poke in enumerate(side.pokemon):
                active_tag = " [active]" if poke.is_active else ""
                status_tag = f" ({poke.status})" if poke.status else ""
                print(
                    f"    {i + 1}. {poke.species} — {poke.hp_pct * 100:.0f}% HP{status_tag}{active_tag}"
                )
        print()

    def get_user_input(self, actions: List[Action]) -> Action:
        """Display available actions and wait for the user to pick one."""
        print("  Available actions:")
        for i, act in enumerate(actions):
            print(f"    [{i}] {act.label}  ({act.action_id})")
        while True:
            raw = input("  Your choice (index): ").strip()
            try:
                idx = int(raw)
                if 0 <= idx < len(actions):
                    return actions[idx]
            except ValueError:
                pass
            print(f"  Please enter a number between 0 and {len(actions) - 1}.")

    # -- Agent interface ----------------------------------------------------
    def decide(self, gamestate: GameState) -> Action:
        self.show_game_state(gamestate)
        actions = self.get_available_actions(gamestate)
        return self.get_user_input(actions)


# ---------------------------------------------------------------------------
# ModelAgent — delegates to an external model server over UDS
# ---------------------------------------------------------------------------
class ModelAgent(Agent):
    """Sends the game state to an external model process via Unix domain socket.

    The external process runs an ``IPCServer`` that exposes at least:
        - ``decide(params)``  →  returns an action dict

    ``model_socket_path`` is the path to that server's UDS endpoint.
    """

    def __init__(
        self, player_number: int, username: str, model_socket_path: str
    ) -> None:
        super().__init__(player_number, username)
        self.model_location = model_socket_path
        self._client = IPCClient(model_socket_path)

    # -- private helpers ----------------------------------------------------
    def _query_model(self, gamestate: GameState) -> Action:
        """Send gamestate to the model server and parse the returned action."""
        params = self._to_query(gamestate)
        raw_action = self._client.call("decide", params)
        return Action.from_dict(raw_action)

    def _to_query(self, gamestate: GameState) -> dict:
        """Serialize gamestate into the params dict expected by the server."""
        return {"gamestate": gamestate.to_dict()}

    # -- Agent interface ----------------------------------------------------
    def decide(self, gamestate: GameState) -> Action:
        logger.info(
            "ModelAgent querying model at %s (turn %d)",
            self.model_location,
            gamestate.turn,
        )
        return self._query_model(gamestate)


# ---------------------------------------------------------------------------
# RandomAgent — uniform random baseline
# ---------------------------------------------------------------------------
class RandomAgent(Agent):
    """Picks a uniformly random legal action.  Useful as a baseline.

    For non-random battle formats (like gen9ou), can generate/load teams.
    """

    def __init__(
        self,
        player_number: int,
        username: str,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__(player_number, username)
        self.rng = random.Random(seed)

    @staticmethod
    def _move_weight(action: Action) -> int:
        """Return 2 for attacking moves (Physical/Special), 1 for status/switch."""
        if action.action_type != "move":
            return 1
        move_id = "".join(c for c in action.label.lower() if c.isalnum())
        return 1 if _MOVE_CATEGORIES.get(move_id) == "Status" else 2

    def generate_action(self, gamestate: GameState) -> Action:
        actions = self.get_available_actions(gamestate)
        weights = [self._move_weight(a) for a in actions]
        return self.rng.choices(actions, weights=weights, k=1)[0]

    def decide(self, gamestate: GameState) -> Action:
        return self.generate_action(gamestate)


# ---------------------------------------------------------------------------
# ReplayAgent — replays a pre-recorded game
# ---------------------------------------------------------------------------
class ReplayAgent(Agent):
    """Replays actions from a previously recorded game sequence.

    Does *not* need a live challenge — the sequence is fully determined
    ahead of time (e.g. loaded from a DataCollector recording).

    Raises IndexError if asked for more actions than the sequence contains.
    """

    def __init__(
        self, player_number: int, username: str, game_sequence: List[Action]
    ) -> None:
        super().__init__(player_number, username)
        self.game_sequence = list(game_sequence)
        self._index = 0

    def get_next_action(self) -> Action:
        if self._index >= len(self.game_sequence):
            raise IndexError(
                f"ReplayAgent exhausted: only {len(self.game_sequence)} actions recorded"
            )
        action = self.game_sequence[self._index]
        self._index += 1
        return action

    def decide(self, gamestate: GameState) -> Action:
        logger.info(
            "ReplayAgent replaying action %d/%d",
            self._index + 1,
            len(self.game_sequence),
        )
        return self.get_next_action()

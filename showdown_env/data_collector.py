"""
data_collector.py — Records game data to disk.

Matches the DataCollector lifeline in the sequence diagram.  GameRunner
calls into it at each major event:
    startRecord()
    recordIfChallengeFailed(reason)
    recordPreview(gamestate)
    recordStateActionPair(gamestate, action)
    endRecord()

Output is a single JSON file per game, structured as:
{
    "metadata": { "timestamp": ..., "players": [...], "outcome": ... },
    "preview": { ... },                         # team-preview state
    "turns": [                                  # ordered list of turns
        { "gamestate": {...}, "action": {...} },
        ...
    ]
}
"""

from __future__ import annotations
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ps_types import GameState, Action

logger = logging.getLogger(__name__)


class DataCollector:
    """Accumulates game events and flushes them to a JSON file on endRecord().

    Parameters:
        output_dir: Directory where game JSON files are written.
                    Created if it does not exist.
    """

    def __init__(self, output_dir: str = "recorded_games") -> None:
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        # Internal state — reset on each startRecord()
        self._recording = False
        self._metadata: Dict[str, Any] = {}
        self._preview: Optional[dict] = None
        self._turns: List[dict] = []
        self._challenge_failed: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def start_record(self) -> None:
        """Begin a new recording session."""
        self._recording = True
        self._metadata = {"timestamp": time.time(), "players": [], "outcome": None}
        self._preview = None
        self._turns = []
        self._challenge_failed = None
        logger.info("DataCollector: recording started")

    def end_record(self) -> str:
        """Flush the current game to disk.  Returns the path to the written file."""
        if not self._recording:
            logger.warning(
                "DataCollector: end_record called but no recording in progress"
            )
            return ""

        self._recording = False
        game_data = {
            "metadata": self._metadata,
            "challenge_failed": self._challenge_failed,
            "preview": self._preview,
            "turns": self._turns,
        }

        filename = f"game_{int(self._metadata['timestamp'])}_{len(self._turns)}turns.json"
        path = os.path.join(self.output_dir, filename)
        with open(path, "w") as f:
            json.dump(game_data, f, indent=2)

        logger.info(
            "DataCollector: game saved to %s (%d turns)", path, len(self._turns)
        )
        return path

    # -- event callbacks ----------------------------------------------------
    def record_if_challenge_failed(self, reason: str) -> None:
        """Called by GameRunner when a challenge is rejected or times out."""
        self._challenge_failed = reason
        logger.info("DataCollector: challenge failed — %s", reason)

    def record_preview(self, gamestate: GameState) -> None:
        """Store the team-preview game state (before the battle starts)."""
        self._preview = gamestate.to_dict()

    def record_state_action_pair(self, gamestate: GameState, action: Action) -> None:
        """Append one (state, action) pair for the current turn."""
        self._turns.append(
            {
                "gamestate": gamestate.to_dict(),
                "action": action.to_dict(),
            }
        )

    # -- metadata helpers ---------------------------------------------------
    def set_players(self, player1_name: str, player2_name: str) -> None:
        self._metadata["players"] = [player1_name, player2_name]

    def set_outcome(self, winner: Optional[int]) -> None:
        """Record the winner (1 or 2) or None for a draw/forfeit."""
        self._metadata["outcome"] = winner

"""
stats_logger.py — Session-level game statistics logger.

Tracks win/loss record, turn counts, and per-game summaries over the course
of a run. Intentionally independent from all other modules — callers pass
plain Python values (strings, ints, lists) so this file has no imports from
the rest of the codebase.

Lifecycle per game:
    stats.start_game()
    stats.record_preview(agent_team=["Landorus-Therian", ...])
    stats.end_game(result="win", turns=34, battle_log=[...], agent_player_id="p1")
    stats.print_summary()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GameRecord:
    """Statistics for a single completed game."""

    game_number: int
    result: str  # "win" | "loss" | "draw" | "unknown"
    turns: int
    agent_team: List[str]       # species names at team preview
    opponent_team: List[str]    # species names at team preview (may be empty)
    agent_remaining: Optional[int]    # alive pokemon at game end
    opponent_remaining: Optional[int] # alive pokemon at game end
    timestamp: float = field(default_factory=time.time)

    @property
    def pokemon_diff(self) -> Optional[int]:
        """Agent remaining minus opponent remaining (positive = agent advantage)."""
        if self.agent_remaining is None or self.opponent_remaining is None:
            return None
        return self.agent_remaining - self.opponent_remaining


class StatsLogger:
    """Accumulates per-game records and exposes session-wide statistics.

    Parameters:
        agent_username: Used only for display in the summary header.
    """

    def __init__(self, agent_username: str = "Agent") -> None:
        self.agent_username = agent_username
        self.games: List[GameRecord] = []

        # per-game scratch space
        self._preview_agent_team: List[str] = []
        self._preview_opponent_team: List[str] = []
        self._start_time: float = 0.0
        self._in_progress: bool = False

    # -- lifecycle -----------------------------------------------------------

    def start_game(self) -> None:
        """Mark the beginning of a new game."""
        self._preview_agent_team = []
        self._preview_opponent_team = []
        self._start_time = time.time()
        self._in_progress = True

    def record_preview(
        self,
        agent_team: List[str],
        opponent_team: Optional[List[str]] = None,
    ) -> None:
        """Capture the teams visible at team preview.

        Args:
            agent_team:    List of species names for the agent's team.
            opponent_team: List of species names for the opponent (may be unknown).
        """
        self._preview_agent_team = list(agent_team)
        self._preview_opponent_team = list(opponent_team or [])

    def end_game(
        self,
        result: str,
        turns: int,
        battle_log: Optional[List[str]] = None,
        agent_player_id: str = "p1",
    ) -> GameRecord:
        """Finalise the current game and append its record.

        Args:
            result:          "win", "loss", "draw", or "unknown".
            turns:           Number of turns taken.
            battle_log:      Raw PS protocol lines (used to count faints).
            agent_player_id: "p1" or "p2" — which side is the agent.
        """
        agent_remaining, opp_remaining = self._compute_remaining(
            battle_log=battle_log,
            agent_team_size=len(self._preview_agent_team),
            opponent_team_size=len(self._preview_opponent_team),
            agent_player_id=agent_player_id,
        )

        record = GameRecord(
            game_number=len(self.games) + 1,
            result=result,
            turns=turns,
            agent_team=self._preview_agent_team,
            opponent_team=self._preview_opponent_team,
            agent_remaining=agent_remaining,
            opponent_remaining=opp_remaining,
            timestamp=self._start_time,
        )
        self.games.append(record)
        self._in_progress = False

        diff_str = f"{record.pokemon_diff:+d}" if record.pokemon_diff is not None else "?"
        logger.info(
            "StatsLogger: game %d — %s in %d turns | pokemon diff %s | record %dW/%dL",
            record.game_number,
            record.result.upper(),
            record.turns,
            diff_str,
            self.wins,
            self.losses,
        )
        return record

    # -- session queries -----------------------------------------------------

    @property
    def total_games(self) -> int:
        return len(self.games)

    @property
    def wins(self) -> int:
        return sum(1 for g in self.games if g.result == "win")

    @property
    def losses(self) -> int:
        return sum(1 for g in self.games if g.result == "loss")

    @property
    def win_rate(self) -> float:
        decisive = self.wins + self.losses
        return self.wins / decisive if decisive else 0.0

    # -- display -------------------------------------------------------------

    def print_summary(self) -> None:
        """Print the session summary to stdout."""
        print(self.summary_str())

    def summary_str(self) -> str:
        """Return the session summary as a formatted string."""
        decisive = self.wins + self.losses
        wr_str = f"{self.win_rate * 100:.1f}%" if decisive else "n/a"
        lines = [
            "=" * 62,
            f"  Session stats — {self.agent_username}",
            f"  Games: {self.total_games}  |  W: {self.wins}  L: {self.losses}  |  WR: {wr_str}",
            "=" * 62,
        ]
        if self.games:
            lines.append(f"  {'#':>3}  {'Result':<8}  {'Turns':>5}  {'Diff':>5}  Starting team")
            lines.append("  " + "-" * 58)
            for g in self.games:
                diff_str = f"{g.pokemon_diff:+d}" if g.pokemon_diff is not None else "  ?"
                team_str = ", ".join(g.agent_team) if g.agent_team else "(unknown)"
                lines.append(
                    f"  {g.game_number:>3}  {g.result:<8}  {g.turns:>5}  {diff_str:>5}  {team_str}"
                )
        lines.append("=" * 62)
        return "\n".join(lines)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _compute_remaining(
        battle_log: Optional[List[str]],
        agent_team_size: int,
        opponent_team_size: int,
        agent_player_id: str,
    ) -> Tuple[Optional[int], Optional[int]]:
        """Count surviving pokemon for each side by parsing |faint| events.

        PS protocol format: "|faint|p1a: PokemonName" or "|faint|p2a: ..."
        """
        if not battle_log:
            return None, None

        opponent_player_id = "p2" if agent_player_id == "p1" else "p1"

        agent_faints = sum(
            1 for line in battle_log if f"|faint|{agent_player_id}" in line
        )
        opp_faints = sum(
            1 for line in battle_log if f"|faint|{opponent_player_id}" in line
        )

        # Fall back to 6 if team size was not captured at preview
        agent_total = agent_team_size or 6
        opp_total = opponent_team_size or 6

        return max(0, agent_total - agent_faints), max(0, opp_total - opp_faints)

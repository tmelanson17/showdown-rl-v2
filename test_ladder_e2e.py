"""
test_ladder_e2e.py — End-to-end tests for LadderGameRunner with an emulated PS server.

Covers:
  1. Opponent exits / match timeout → graceful exit and queue next game
  2. Opponent traps agent → switch attempt rejected, recovers with move
  3. Agent locked into multi-turn move (Outrage) → all switches rejected, picks locked move
  4. Move rejected → retries successive moves until one is accepted

Run with:
    pytest test_ladder_e2e.py -v
    python test_ladder_e2e.py
"""

import sys
import os
import unittest
from dataclasses import dataclass, field
from typing import List, Optional, Union
from unittest.mock import patch

# Make showdown_env modules importable
_ENV_DIR = os.path.join(os.path.dirname(__file__), "showdown_env")
if _ENV_DIR not in sys.path:
    sys.path.insert(0, _ENV_DIR)

from game_runner import LadderGameRunner
from ps_types import GameState, Action
from agents import Agent

# ---------------------------------------------------------------------------
# Sentinel value used to signal end-of-battle in a game script
# ---------------------------------------------------------------------------
BATTLE_END = "__END__"


# ---------------------------------------------------------------------------
# FakePSClient infrastructure
# ---------------------------------------------------------------------------

@dataclass
class GameScript:
    """Script for one complete ladder game."""
    requests: List[Union[dict, None, str]]  # dict=request, None=timeout, BATTLE_END=game over
    errors: List[Optional[str]]             # one entry per choose() call; None=accepted
    winner: Optional[str]


class FakeBattleState:
    my_player_id = "p1"
    p1_active = None
    p2_active = None


class FakePSClient:
    """Emulates PSClient by serving scripted responses from a list of GameScripts."""

    def __init__(self, scripts: List[GameScript]) -> None:
        self._scripts = scripts
        self._game_idx = -1
        self._current: Optional[GameScript] = None
        self._req_idx = 0
        self._err_idx = 0
        self._battle_over = False
        self._connected = True
        self.battle_state = FakeBattleState()

    # -- lifecycle ----------------------------------------------------------

    def reset_battle(self) -> None:
        self._game_idx += 1
        self._current = self._scripts[self._game_idx]
        self._req_idx = 0
        self._err_idx = 0
        self._battle_over = False

    def cancel_search(self) -> None:
        pass

    # -- matchmaking --------------------------------------------------------

    def search_ladder(self, format_id: str, team=None) -> bool:
        return True

    def wait_for_battle(self, timeout: float = 30.0) -> Optional[str]:
        return "battle-gen9randombattle-1"

    def send_timer_on(self) -> None:
        pass

    # -- battle loop --------------------------------------------------------

    def wait_for_request(self, timeout: float = 30.0) -> Optional[dict]:
        if self._req_idx >= len(self._current.requests):
            return None
        item = self._current.requests[self._req_idx]
        self._req_idx += 1
        if item == BATTLE_END:
            self._battle_over = True
            return None
        return item

    def choose(self, choice_str: str) -> None:
        pass

    def get_and_clear_battle_error(self) -> Optional[str]:
        if self._err_idx >= len(self._current.errors):
            return None
        err = self._current.errors[self._err_idx]
        self._err_idx += 1
        return err

    def clear_pending_request(self) -> None:
        pass

    def is_battle_over(self) -> bool:
        return self._battle_over

    def get_winner(self) -> Optional[str]:
        return self._current.winner if self._current else None

    def get_battle_log(self) -> List[str]:
        return []


# ---------------------------------------------------------------------------
# Test agents
# ---------------------------------------------------------------------------

class SwitchPreferringAgent(Agent):
    """Always picks the first available switch; falls back to first move if none."""

    def decide(self, gamestate: GameState) -> Action:
        switches = [a for a in gamestate.available_actions if a.action_type == "switch"]
        return switches[0] if switches else gamestate.available_actions[0]


class FirstActionAgent(Agent):
    """Always picks the first available action."""

    def decide(self, gamestate: GameState) -> Action:
        return gamestate.available_actions[0]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_runner(fake_client: FakePSClient, agent_cls) -> LadderGameRunner:
    return LadderGameRunner(
        agent_factory=lambda n, u: agent_cls(n, u),
        agent_username="testbot",
        _client=fake_client,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLadderE2E(unittest.TestCase):

    @patch("game_runner.time.sleep")
    def test_opponent_exits_then_second_game_succeeds(self, _mock_sleep):
        """
        Scenario: opponent disconnects mid-battle so wait_for_request() times out
        repeatedly. After max_retries is exceeded, _run_one() exits gracefully with
        no winner. The outer run() loop then queues and completes a second game.

        Retry math: max_retries=4, guard is `retry > max_retries` (strict).
        None #1→retry=1, #2→2, #3→3, #4→4, #5→5; None #6: 5>4 → break.
        """
        active_request = {
            "active": [{"moves": [{"move": "Thunderbolt", "id": "thunderbolt"}]}],
            "side": {"pokemon": [
                {"active": True, "details": "Pikachu, L50",
                 "condition": "100/100", "moves": []},
            ]},
            "rqid": 1,
        }
        scripts = [
            GameScript(
                requests=[None, None, None, None, None, None],  # 6 Nones → retry exhaustion
                errors=[],
                winner=None,
            ),
            GameScript(
                requests=[active_request, BATTLE_END],
                errors=[None],
                winner="testbot",
            ),
        ]
        fake = FakePSClient(scripts)
        runner = make_runner(fake, FirstActionAgent)

        results = runner.run(num_games=2, timeout=60.0)

        self.assertIsNone(results[0], "Game 1 should exit with no winner after timeout")
        self.assertEqual(results[1], "testbot", "Game 2 should complete normally")

    @patch("game_runner.time.sleep")
    def test_switch_rejected_recovers_with_move(self, _mock_sleep):
        """
        Scenario: opponent uses a trapping move (Arena Trap etc.) but the request
        doesn't carry the trapped flag, so switches appear in available_actions.
        The agent (switch-preferring) tries a switch; PS rejects it. On retry the
        switch is excluded, leaving only moves; agent picks a move which is accepted.
        """
        request = {
            "active": [{"moves": [
                {"move": "Thunderbolt", "id": "thunderbolt"},
                {"move": "Quick Attack", "id": "quickattack"},
            ]}],
            "side": {"pokemon": [
                {"active": True,  "details": "Pikachu, L50",
                 "condition": "100/100", "moves": []},
                {"active": False, "details": "Charizard, L50",
                 "condition": "100/100", "moves": []},
            ]},
            "rqid": 1,
        }
        # available_actions built by _build_game_state:
        #   move 1 (Thunderbolt), move 2 (Quick Attack), switch 2 (Charizard)
        # SwitchPreferringAgent picks switch 2 → rejected → picks move 1 → accepted
        scripts = [
            GameScript(
                requests=[request, BATTLE_END],
                errors=["Can't switch: The active Pokemon is trapped", None],
                winner="testbot",
            ),
        ]
        fake = FakePSClient(scripts)
        runner = make_runner(fake, SwitchPreferringAgent)

        results = runner.run(num_games=1, timeout=60.0)

        self.assertEqual(results[0], "testbot")
        self.assertEqual(fake._err_idx, 2, "Both error slots should be consumed")

    @patch("game_runner.time.sleep")
    def test_outrage_locked_all_switches_excluded(self, _mock_sleep):
        """
        Scenario: active Pokemon is locked into Outrage (moves 2 & 3 are disabled).
        Two benched Pokemon are available to switch to. The switch-preferring agent
        tries switch 2, then switch 3 — both rejected. With no switches left in the
        filtered list, it finally picks move 1 (Outrage) which is accepted.
        """
        request = {
            "active": [{"moves": [
                {"move": "Outrage",    "id": "outrage"},
                {"move": "Fire Punch", "id": "firepunch",  "disabled": True},
                {"move": "Earthquake", "id": "earthquake", "disabled": True},
            ]}],
            "side": {"pokemon": [
                {"active": True,  "details": "Dragonite, L50",
                 "condition": "200/200", "moves": []},
                {"active": False, "details": "Blastoise, L50",
                 "condition": "150/150", "moves": []},
                {"active": False, "details": "Venusaur, L50",
                 "condition": "140/140", "moves": []},
            ]},
            "rqid": 5,
        }
        # available_actions: move 1 (Outrage), switch 2 (Blastoise), switch 3 (Venusaur)
        # Attempts: switch 2 → rejected, switch 3 → rejected, move 1 → accepted
        scripts = [
            GameScript(
                requests=[request, BATTLE_END],
                errors=["[Unavailable choice]", "[Unavailable choice]", None],
                winner="opponent",
            ),
        ]
        fake = FakePSClient(scripts)
        runner = make_runner(fake, SwitchPreferringAgent)

        results = runner.run(num_games=1, timeout=60.0)

        self.assertEqual(results[0], "opponent")
        self.assertEqual(fake._err_idx, 3, "All three error slots should be consumed")

    @patch("game_runner.time.sleep")
    def test_move_rejected_retries_different_moves(self, _mock_sleep):
        """
        Scenario: general move rejection. The active Pokemon is trapped (no switches).
        The agent picks the first available move each time; rejected moves are excluded
        so successive attempts pick the next move. Moves 1 and 2 are rejected; move 3
        is accepted.
        """
        request = {
            "active": [{"trapped": True, "moves": [
                {"move": "Protect",     "id": "protect"},
                {"move": "Detect",      "id": "detect"},
                {"move": "Thunderbolt", "id": "thunderbolt"},
                {"move": "Quick Attack","id": "quickattack"},
            ]}],
            "side": {"pokemon": [
                {"active": True,  "details": "Pikachu, L50",
                 "condition": "100/100", "moves": []},
                {"active": False, "details": "Raichu, L50",
                 "condition": "120/120", "moves": []},
            ]},
            "rqid": 3,
        }
        # available_actions (trapped=True → no switches): move 1, move 2, move 3, move 4
        # FirstActionAgent: picks move 1 → excluded, move 2 → excluded, move 3 → accepted
        scripts = [
            GameScript(
                requests=[request, BATTLE_END],
                errors=["[Invalid choice]", "[Invalid choice]", None],
                winner="testbot",
            ),
        ]
        fake = FakePSClient(scripts)
        runner = make_runner(fake, FirstActionAgent)

        results = runner.run(num_games=1, timeout=60.0)

        self.assertEqual(results[0], "testbot")
        self.assertEqual(fake._err_idx, 3, "All three error slots should be consumed")


if __name__ == "__main__":
    unittest.main()

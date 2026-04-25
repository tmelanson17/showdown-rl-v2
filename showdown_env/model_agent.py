"""
model_agent.py — HTTP-based agents for Pokemon Showdown battles.

Two concrete agents are provided:

- OllamaModelAgent: sends an English-formatted game state to any Ollama-hosted
  instruction-following LLM (e.g. llama3.2).

- BERTModelAgent: sends the raw PS protocol directly to the locally trained
  RoBERTa QA model (model_server.py on port 11435), matching the format the
  model was trained on.

LLMModelAgent is kept as a backward-compatible alias for OllamaModelAgent.
"""

from __future__ import annotations
import abc
import re
import logging
from typing import List, Optional

from ps_types import GameState, Action
from agents import Agent
from llm_logger import LLMLogger

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1800


# ---------------------------------------------------------------------------
# Base class — shared HTTP query logic, action parsing, and game loop
# ---------------------------------------------------------------------------


class _BaseHTTPModelAgent(Agent):
    """Base for agents that query an Ollama-compatible HTTP model server."""

    DEFAULT_MODEL: str = "local-model"
    DEFAULT_OLLAMA_URL: str = "http://localhost:11435"

    def __init__(
        self,
        player_number: int,
        username: str,
        battle_format: str = "gen9ou",
        model_name: str | None = None,
        ollama_url: str | None = None,
        log_path: str | None = None,
    ) -> None:
        super().__init__(player_number, username)
        self.battle_format = battle_format
        self.model_name = model_name or self.DEFAULT_MODEL
        self.ollama_url = ollama_url or self.DEFAULT_OLLAMA_URL
        self._llm_logger = LLMLogger(log_path) if log_path else None

        self.action_history: List[str] = []
        self.turn_history: List[str] = []
        self._last_seen_turn = 0

        try:
            import requests

            self._requests = requests
        except ImportError:
            raise ImportError(
                "requests library required. Install with: pip install requests"
            )

    @abc.abstractmethod
    def _build_prompt(self, gamestate: GameState) -> str:
        """Build the prompt to send to the model server."""

    def _stringify_available_actions(self, actions: List[Action]) -> str:
        lines = ["Available actions:"]
        for action in actions:
            lines.append(f"  {action.action_type.upper()}: {action.label}")
        return "\n".join(lines)

    def _extract_turn_events(self, gamestate: GameState) -> List[str]:
        events = []
        if not gamestate.raw_protocol:
            return events

        for line in gamestate.raw_protocol.split("\n"):
            line = line.strip()
            if not line or not line.startswith("|"):
                continue

            parts = line.split("|")
            if len(parts) < 2:
                continue

            msg_type = parts[1]

            if msg_type == "move":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon} used {parts[3]}")

            elif msg_type == "-damage":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon} took damage ({parts[3]})")

            elif msg_type == "-heal":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon} healed ({parts[3]})")

            elif msg_type == "faint":
                if len(parts) >= 3:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon} fainted!")

            elif msg_type in ("switch", "drag"):
                if len(parts) >= 4:
                    pokemon = parts[3].split(",")[0]
                    player = (
                        "You" if f"p{self.player_number}" in parts[2] else "Opponent"
                    )
                    events.append(f"{player} sent out {pokemon}")

            elif msg_type == "-status":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    status_names = {
                        "brn": "burned",
                        "par": "paralyzed",
                        "slp": "fell asleep",
                        "frz": "frozen",
                        "psn": "poisoned",
                        "tox": "badly poisoned",
                    }
                    events.append(
                        f"{pokemon} was {status_names.get(parts[3], parts[3])}"
                    )

            elif msg_type == "-supereffective":
                events.append("It's super effective!")
            elif msg_type == "-resisted":
                events.append("It's not very effective...")
            elif msg_type == "-crit":
                events.append("Critical hit!")
            elif msg_type == "-miss":
                if len(parts) >= 3:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon}'s attack missed!")

        return events

    def _query_server(self, prompt: str) -> str:
        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.5, "num_predict": 50},
        }
        try:
            response = self._requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except self._requests.exceptions.ConnectionError:
            logger.error("Failed to connect to model server at %s.", self.ollama_url)
            raise RuntimeError(f"Cannot connect to model server at {self.ollama_url}")
        except self._requests.exceptions.Timeout:
            raise RuntimeError("Model server request timed out")
        except Exception as e:
            logger.error("Error querying model server: %s", e)
            raise

    def _parse_llm_response(
        self, response: str, available_actions: List[Action]
    ) -> Optional[Action]:
        response = response.strip()
        logger.debug("Model raw response: %s", response)

        # Strategy 1: numeric index
        numbers = re.findall(r"\b(\d+)\b", response)
        if numbers:
            try:
                idx = int(numbers[0])
                if 0 <= idx < len(available_actions):
                    logger.info("Matched action by index: %d", idx)
                    return available_actions[idx]
            except (ValueError, IndexError):
                pass

        response_lower = response.lower()

        # Strategy 2: exact label
        for action in available_actions:
            if action.label.lower() == response_lower:
                logger.info("Matched action by exact label: %s", action.label)
                return action

        # Strategy 3: partial label
        for action in available_actions:
            if action.label.lower() in response_lower:
                logger.info("Matched action by partial label: %s", action.label)
                return action
            label_no_space = action.label.lower().replace(" ", "")
            if label_no_space in response_lower:
                logger.info("Matched fuzzy label: %s", action.label)
                return action

        # Strategy 4: type keywords
        for keyword in ("use", "attack", "move"):
            if keyword in response_lower:
                for action in available_actions:
                    if (
                        action.action_type == "move"
                        and action.label.lower() in response_lower
                    ):
                        logger.info("Matched move action: %s", action.label)
                        return action

        for keyword in ("switch", "swap", "go"):
            if keyword in response_lower:
                for action in available_actions:
                    if (
                        action.action_type == "switch"
                        and action.label.lower() in response_lower
                    ):
                        logger.info("Matched switch action: %s", action.label)
                        return action

        # Strategy 5: word overlap
        best_match = None
        best_score = 0
        response_words = set(response_lower.split())
        for action in available_actions:
            overlap = len(response_words & set(action.label.lower().split()))
            if overlap > best_score:
                best_score = overlap
                best_match = action

        if best_match and best_score > 0:
            logger.info(
                "Matched action by word overlap: %s (score=%d)",
                best_match.label,
                best_score,
            )
            return best_match

        return None

    def decide(self, gamestate: GameState) -> Action:
        available_actions = self.get_available_actions(gamestate)
        if not available_actions:
            raise ValueError("No available actions to choose from!")

        events = self._extract_turn_events(gamestate)
        if events:
            if gamestate.turn > self._last_seen_turn:
                self.turn_history.append(f"--- Turn {gamestate.turn} ---")
                self._last_seen_turn = gamestate.turn
            self.turn_history.extend(events)

        logger.info("Turn %d - Available actions:", gamestate.turn)
        for i, action in enumerate(available_actions):
            logger.info("  [%d] %s", i, action.label)

        prompt = self._build_prompt(gamestate)
        logger.debug("Prompt:\n%s", prompt)

        try:
            response = self._query_server(prompt)
            logger.info("Model response: %s", response)

            action = self._parse_llm_response(response, available_actions)
            parse_success = action is not None

            if action is None:
                logger.warning(
                    "Could not parse model response '%s', using first available action",
                    response,
                )
                action = available_actions[0]

            if self._llm_logger is not None:
                self._llm_logger.log(
                    turn=gamestate.turn,
                    player_number=self.player_number,
                    username=self.username,
                    model=self.model_name,
                    battle_format=self.battle_format,
                    prompt=prompt,
                    response=response,
                    available_actions=[
                        {"action_type": a.action_type, "label": a.label}
                        for a in available_actions
                    ],
                    chosen_action={
                        "action_type": action.action_type,
                        "label": action.label,
                    },
                    parse_success=parse_success,
                )

            self.action_history.append(
                f"Turn {gamestate.turn}: {action.action_type} - {action.label}"
            )
            return action

        except Exception as e:
            logger.error("Error during model query: %s", e)
            import random

            action = random.choice(available_actions)
            logger.warning("Falling back to random action: %s", action.label)
            return action


# ---------------------------------------------------------------------------
# OllamaModelAgent — English-formatted prompt for instruction-following LLMs
# ---------------------------------------------------------------------------


class OllamaModelAgent(_BaseHTTPModelAgent):
    """Agent that uses an Ollama-hosted LLM to make battle decisions.

    Sends a human-readable English game state description.
    """

    DEFAULT_MODEL = "llama3.2:latest"
    DEFAULT_OLLAMA_URL = "http://localhost:11434"

    def _stringify_game_state(self, gamestate: GameState) -> str:
        lines = [
            f"Turn: {gamestate.turn}",
            f"Active Player: Player {gamestate.active_player}",
            "",
        ]
        for side in gamestate.sides:
            is_my_side = side.player_id == self.player_number
            marker = " (YOU)" if is_my_side else " (OPPONENT)"
            lines.append(f"=== {side.username}{marker} ===")
            if not side.pokemon:
                lines.append("  (No Pokemon info available)")
            else:
                for i, poke in enumerate(side.pokemon):
                    active_tag = " [ACTIVE]" if poke.is_active else ""
                    status_tag = f" ({poke.status})" if poke.status else ""
                    hp_display = (
                        "FAINTED" if poke.hp_pct == 0 else f"{poke.hp_pct * 100:.0f}%"
                    )
                    lines.append(
                        f"  {i + 1}. {poke.species}: {hp_display}{status_tag}{active_tag}"
                    )
                    if is_my_side and poke.moves:
                        lines.append(f"     Moves: {', '.join(poke.moves)}")
            lines.append("")
        return "\n".join(lines)

    def _build_prompt(self, gamestate: GameState) -> str:
        state_str = self._stringify_game_state(gamestate)
        actions_str = self._stringify_available_actions(gamestate.available_actions)

        turn_history_context = ""
        if self.turn_history:
            recent_turns = self.turn_history[-20:]
            turn_history_context = "\nRecent battle events:\n"
            turn_history_context += "\n".join(f"  - {e}" for e in recent_turns)
            turn_history_context += "\n"

        history_context = ""
        if self.action_history:
            recent_history = self.action_history[-10:]
            history_context = "\nYour previous actions this battle:\n"
            history_context += "\n".join(f"  - {a}" for a in recent_history)
            history_context += "\n"

        return (
            f"You are battling in {self.battle_format} as Player {self.player_number}. "
            f"Given the current battle state, please:\n"
            f"choose one of the legal moves on the active Pokemon, if applicable, or\n"
            f"switch to one of the non-fainted Pokemon on the team, if allowed.\n\n"
            f"Current Battle State:\n{state_str}\n"
            f"{turn_history_context}"
            f"{history_context}"
            f"{actions_str}\n\n"
            f"IMPORTANT: You must respond with ONLY the the exact action name from the list above.\n"
            f"Think about type matchups, HP levels, and status conditions when making your decision.\n"
            f"Do NOT include any explanation - just the action name. Be sure to put spaces in moves that consist of multiple words. \n\n"
            f"Your choice:"
        )


# ---------------------------------------------------------------------------
# BERTModelAgent — raw PS protocol for the locally trained RoBERTa QA model
# ---------------------------------------------------------------------------


class BERTModelAgent(_BaseHTTPModelAgent):
    """Agent that uses the locally trained RoBERTa QA model on port 11435.

    Sends the raw PS protocol as the question and the available actions block
    as context, matching the SQuAD format the model was trained on.
    """

    DEFAULT_MODEL = "local-model"
    DEFAULT_OLLAMA_URL = "http://localhost:11435"

    def _build_prompt(self, gamestate: GameState) -> str:
        protocol = (gamestate.raw_protocol or "")[-MAX_QUESTION_CHARS:]
        actions_str = self._stringify_available_actions(gamestate.available_actions)
        return f"{protocol}\n\n{actions_str}"


# ---------------------------------------------------------------------------
# Backward-compatibility alias
# ---------------------------------------------------------------------------

LLMModelAgent = OllamaModelAgent


# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------


def test_llm_vs_random():
    """Test the LLM agent against a RandomAgent using the GameRunner."""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    from game_runner import GameRunner
    from agents import RandomAgent
    from ps_server import PSServer

    FORMAT = "gen9randombattle"
    USERNAME1 = "LLMBot"
    USERNAME2 = "RandomBot"

    print("=" * 60)
    print(f"Pokemon Showdown - {USERNAME1} (LLM) vs {USERNAME2} (Random)")
    print(f"Format: {FORMAT}")
    print("=" * 60)

    print("\nChecking Ollama connection...")
    try:
        import requests

        response = requests.get(
            f"{OllamaModelAgent.DEFAULT_OLLAMA_URL}/api/tags", timeout=5
        )
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", m.get("model", "unknown")) for m in models]
            print(f"Ollama is running. Available models: {model_names}")
            if not model_names:
                print(
                    "WARNING: No models installed. Install one with: ollama pull llama3.2"
                )
        else:
            print(f"Warning: Ollama returned status {response.status_code}")
    except Exception:
        print(
            f"ERROR: Cannot connect to Ollama at {OllamaModelAgent.DEFAULT_OLLAMA_URL}"
        )
        print("Please ensure Ollama is running with: ollama serve")
        return

    print("\nStarting Pokemon Showdown server connection...")
    ps_server = PSServer(
        server_url="http://localhost:8000",
        ps_path="/mnt/c/Users/tmela/development/pokemans/pokemon-showdown",
        format_id=FORMAT,
    )

    try:
        ps_server.connect()
        print("PS Server connected successfully")
    except Exception as e:
        print(f"ERROR: Failed to connect PS server: {e}")
        return

    def llm_agent_factory(player_num, username):
        return OllamaModelAgent(
            player_number=player_num, username=username, battle_format=FORMAT
        )

    def random_agent_factory(player_num, username):
        return RandomAgent(player_num, username)

    runner = GameRunner(
        ps_server=ps_server,
        agent1_factory=llm_agent_factory,
        agent2_factory=random_agent_factory,
        data_collector=None,
        username1=USERNAME1,
        username2=USERNAME2,
        format=FORMAT,
    )

    print("\nStarting battle...")
    try:
        winner = runner.run()
        if winner == 1:
            print(f"\n{USERNAME1} (LLM) wins!")
        elif winner == 2:
            print(f"\n{USERNAME2} (Random) wins!")
        else:
            print("\nBattle ended without a clear winner")
    except KeyboardInterrupt:
        print("\n\nBattle interrupted by user")
    except Exception as e:
        logger.exception("Error during battle")
        print(f"\nError during battle: {e}")
    finally:
        ps_server.disconnect()
        print("\nPS Server disconnected")


def test_llm_query():
    """Simple test to verify LLM query works without full game infrastructure."""
    import logging

    logging.basicConfig(level=logging.INFO)

    from ps_types import Side, PokemonSlot

    print("=" * 60)
    print("Testing LLM Query")
    print("=" * 60)

    my_pokemon = [
        PokemonSlot(species="Charizard", hp_pct=0.75, status=None, is_active=True),
        PokemonSlot(species="Blastoise", hp_pct=1.0, status=None, is_active=False),
        PokemonSlot(species="Venusaur", hp_pct=0.5, status="psn", is_active=False),
    ]
    opponent_pokemon = [
        PokemonSlot(species="Pikachu", hp_pct=0.3, status=None, is_active=True),
        PokemonSlot(species="Raichu", hp_pct=1.0, status=None, is_active=False),
    ]
    available_actions = [
        Action(
            action_id="move 1", action_type="move", target_index=1, label="Flamethrower"
        ),
        Action(
            action_id="move 2", action_type="move", target_index=2, label="Dragon Claw"
        ),
        Action(
            action_id="move 3", action_type="move", target_index=3, label="Earthquake"
        ),
        Action(
            action_id="switch 2",
            action_type="switch",
            target_index=2,
            label="Blastoise",
        ),
        Action(
            action_id="switch 3", action_type="switch", target_index=3, label="Venusaur"
        ),
    ]
    gamestate = GameState(
        turn=5,
        active_player=1,
        sides=[
            Side(player_id=1, username="LLMBot", pokemon=my_pokemon),
            Side(player_id=2, username="Opponent", pokemon=opponent_pokemon),
        ],
        available_actions=available_actions,
    )

    agent = OllamaModelAgent(player_number=1, username="LLMBot", battle_format="gen9ou")

    print("\nGame State:")
    print(agent._stringify_game_state(gamestate))
    print("\nActions:")
    print(agent._stringify_available_actions(available_actions))
    print("\n" + "=" * 60)
    print("Querying LLM...")
    print("=" * 60)

    try:
        action = agent.decide(gamestate)
        print(f"\nLLM chose: {action.action_type.upper()} - {action.label}")
        print(f"   (action_id: {action.action_id})")
    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "query":
        test_llm_query()
    else:
        test_llm_vs_random()

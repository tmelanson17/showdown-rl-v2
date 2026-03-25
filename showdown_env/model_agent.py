"""
model_agent.py — LLM-based agent for Pokemon Showdown battles.

This agent uses a local LLM (via Ollama) to make battle decisions.
The LLM receives the current game state and must choose a valid action.
"""

from __future__ import annotations
import os
import re
import logging
from typing import List, Optional, TYPE_CHECKING

from ps_types import GameState, Action
from agents import Agent
from llm_logger import LLMLogger

if TYPE_CHECKING:
    from teams import TeamManager

logger = logging.getLogger(__name__)


class LLMModelAgent(Agent):
    """Agent that uses a local LLM (Ollama) to make battle decisions.

    The LLM receives:
    - The battle format
    - Player number
    - Current game state (stringified)
    - Previous actions taken
    - List of available actions

    The LLM must choose one of the available actions by name or index.
    """

    DEFAULT_MODEL = "llama3.2:latest"
    DEFAULT_OLLAMA_URL = "http://localhost:11434"

    def __init__(
        self,
        player_number: int,
        username: str,
        battle_format: str = "gen9ou",
        model_name: str = DEFAULT_MODEL,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        teams_dir: str = "teams",
        log_path: str | None = None,
    ) -> None:
        super().__init__(player_number, username)
        self.battle_format = battle_format
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.teams_dir = teams_dir
        self._team_cache: dict[str, str] = {}
        self._llm_logger = LLMLogger(log_path) if log_path else None

        # Track action history for context
        self.action_history: List[str] = []

        # Track turn-by-turn battle events
        self.turn_history: List[str] = []
        self._last_seen_turn = 0

        # Import requests here to make it optional
        try:
            import requests

            self._requests = requests
        except ImportError:
            raise ImportError(
                "requests library required for LLMModelAgent. "
                "Install with: pip install requests"
            )

    def _stringify_game_state(self, gamestate: GameState) -> str:
        """Convert game state to a human-readable string for the LLM."""
        lines = []
        lines.append(f"Turn: {gamestate.turn}")
        lines.append(f"Active Player: Player {gamestate.active_player}")
        lines.append("")

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
                    hp_display = f"{poke.hp_pct * 100:.0f}%"
                    if poke.hp_pct == 0:
                        hp_display = "FAINTED"
                    lines.append(
                        f"  {i + 1}. {poke.species}: {hp_display}{status_tag}{active_tag}"
                    )
                    # Show moveset for LLM player's team only
                    if is_my_side and poke.moves:
                        moves_str = ", ".join(poke.moves)
                        lines.append(f"     Moves: {moves_str}")
            lines.append("")

        return "\n".join(lines)

    def _stringify_available_actions(self, actions: List[Action]) -> str:
        """Convert available actions to a list."""
        lines = ["Available actions:"]
        for action in actions:
            action_type = action.action_type.upper()
            lines.append(f"  {action_type}: {action.label}")
        return "\n".join(lines)

    def _extract_turn_events(self, gamestate: GameState) -> List[str]:
        """Extract relevant battle events from the raw protocol."""
        events = []
        if not gamestate.raw_protocol:
            return events

        # Parse the raw protocol for battle events
        for line in gamestate.raw_protocol.split("\n"):
            line = line.strip()
            if not line or not line.startswith("|"):
                continue

            parts = line.split("|")
            if len(parts) < 2:
                continue

            msg_type = parts[1]

            # Capture interesting events
            if msg_type == "move":
                # |move|POKEMON|MOVE|TARGET
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    move = parts[3]
                    events.append(f"{pokemon} used {move}")

            elif msg_type == "-damage":
                # |-damage|POKEMON|HP STATUS
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    hp_info = parts[3]
                    events.append(f"{pokemon} took damage ({hp_info})")

            elif msg_type == "-heal":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    hp_info = parts[3]
                    events.append(f"{pokemon} healed ({hp_info})")

            elif msg_type == "faint":
                if len(parts) >= 3:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    events.append(f"{pokemon} fainted!")

            elif msg_type == "switch" or msg_type == "drag":
                if len(parts) >= 4:
                    pokemon = parts[3].split(",")[0]  # Get species name
                    player = (
                        "You" if f"p{self.player_number}" in parts[2] else "Opponent"
                    )
                    events.append(f"{player} sent out {pokemon}")

            elif msg_type == "-status":
                if len(parts) >= 4:
                    pokemon = parts[2].split(": ")[-1] if ": " in parts[2] else parts[2]
                    status = parts[3]
                    status_names = {
                        "brn": "burned",
                        "par": "paralyzed",
                        "slp": "fell asleep",
                        "frz": "frozen",
                        "psn": "poisoned",
                        "tox": "badly poisoned",
                    }
                    status_text = status_names.get(status, status)
                    events.append(f"{pokemon} was {status_text}")

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

    def _build_prompt(self, gamestate: GameState) -> str:
        """Build the prompt for the LLM."""
        state_str = self._stringify_game_state(gamestate)
        actions_str = self._stringify_available_actions(gamestate.available_actions)

        # Build turn history context
        turn_history_context = ""
        if self.turn_history:
            recent_turns = self.turn_history[-20:]  # Last 20 events
            turn_history_context = "\nRecent battle events:\n"
            turn_history_context += "\n".join(f"  - {event}" for event in recent_turns)
            turn_history_context += "\n"

        # Build action history context
        history_context = ""
        if self.action_history:
            recent_history = self.action_history[-10:]  # Last 10 actions
            history_context = "\nYour previous actions this battle:\n"
            history_context += "\n".join(f"  - {action}" for action in recent_history)
            history_context += "\n"

        prompt = f"""You are battling in {self.battle_format} as Player {self.player_number}. Given the current battle state, please:
choose one of the legal moves on the active Pokemon, if applicable, or
switch to one of the non-fainted Pokemon on the team, if allowed.

Current Battle State:
{state_str}
{turn_history_context}
{history_context}
{actions_str}

IMPORTANT: You must respond with ONLY the the exact action name from the list above.
Think about type matchups, HP levels, and status conditions when making your decision.
Do NOT include any explanation - just the action name.

Your choice:"""

        return prompt

    def _query_llm(self, prompt: str) -> str:
        """Send a prompt to the Ollama API and get a response."""
        url = f"{self.ollama_url}/api/generate"

        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.5,  # Lower temperature for more consistent choices
                "num_predict": 50,  # We only need a short response
            },
        }

        try:
            response = self._requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "").strip()
        except self._requests.exceptions.ConnectionError:
            logger.error(
                "Failed to connect to Ollama at %s. Is Ollama running?", self.ollama_url
            )
            raise RuntimeError(f"Cannot connect to Ollama at {self.ollama_url}")
        except self._requests.exceptions.Timeout:
            logger.error("Ollama request timed out")
            raise RuntimeError("Ollama request timed out")
        except Exception as e:
            logger.error("Error querying Ollama: %s", e)
            raise

    def _parse_llm_response(
        self, response: str, available_actions: List[Action]
    ) -> Optional[Action]:
        """Parse LLM response and find the matching action.

        Tries multiple strategies:
        1. Direct index match (e.g., "0", "1", "2")
        2. Exact action label match (case-insensitive)
        3. Partial action label match (fuzzy)
        4. Action type + name match (e.g., "use Thunderbolt", "switch to Pikachu")

        Returns None if no valid action could be determined.
        """
        response = response.strip()
        logger.debug("LLM raw response: %s", response)

        # Strategy 1: Try to extract a number
        numbers = re.findall(r"\b(\d+)\b", response)
        if numbers:
            try:
                idx = int(numbers[0])
                if 0 <= idx < len(available_actions):
                    logger.info("Matched action by index: %d", idx)
                    return available_actions[idx]
            except (ValueError, IndexError):
                pass

        # Strategy 2: Exact label match (case-insensitive)
        response_lower = response.lower()
        for action in available_actions:
            if action.label.lower() == response_lower:
                logger.info("Matched action by exact label: %s", action.label)
                return action

        # Strategy 3: Partial match - check if action label appears in response
        for action in available_actions:
            if action.label.lower() in response_lower:
                logger.info("Matched action by partial label: %s", action.label)
                return action

        # Strategy 4: Check for action type keywords
        move_keywords = ["use", "attack", "move"]
        switch_keywords = ["switch", "swap", "go"]

        # Check for move actions
        for keyword in move_keywords:
            if keyword in response_lower:
                # Look for move actions
                for action in available_actions:
                    if (
                        action.action_type == "move"
                        and action.label.lower() in response_lower
                    ):
                        logger.info("Matched move action: %s", action.label)
                        return action

        # Check for switch actions
        for keyword in switch_keywords:
            if keyword in response_lower:
                # Look for switch actions
                for action in available_actions:
                    if (
                        action.action_type == "switch"
                        and action.label.lower() in response_lower
                    ):
                        logger.info("Matched switch action: %s", action.label)
                        return action

        # Strategy 5: Fuzzy match using simple word overlap
        best_match = None
        best_score = 0
        response_words = set(response_lower.split())

        for action in available_actions:
            label_words = set(action.label.lower().split())
            overlap = len(response_words & label_words)
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
        """Ask the LLM to choose an action given the current game state."""
        available_actions = self.get_available_actions(gamestate)

        if not available_actions:
            raise ValueError("No available actions to choose from!")

        # Extract and record turn events from raw protocol
        events = self._extract_turn_events(gamestate)
        if events:
            # Add turn marker if this is a new turn
            if gamestate.turn > self._last_seen_turn:
                self.turn_history.append(f"--- Turn {gamestate.turn} ---")
                self._last_seen_turn = gamestate.turn
            self.turn_history.extend(events)

        # Log available actions for visibility
        logger.info("Turn %d - Available actions:", gamestate.turn)
        for i, action in enumerate(available_actions):
            logger.info("  [%d] %s", i, action.label)

        # Build and send prompt
        prompt = self._build_prompt(gamestate)
        logger.debug("Prompt:\n%s", prompt)

        try:
            response = self._query_llm(prompt)
            logger.info("LLM response: %s", response)

            # Parse the response
            action = self._parse_llm_response(response, available_actions)
            parse_success = action is not None

            if action is None:
                # Fallback: use first available action
                logger.warning(
                    "Could not parse LLM response '%s', using first available action",
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
                    chosen_action={"action_type": action.action_type, "label": action.label},
                    parse_success=parse_success,
                )

            # Record action for history
            action_desc = (
                f"Turn {gamestate.turn}: {action.action_type} - {action.label}"
            )
            self.action_history.append(action_desc)

            return action

        except Exception as e:
            logger.error("Error during LLM query: %s", e)
            # Fallback to random action on error
            import random

            action = random.choice(available_actions)
            logger.warning("Falling back to random action: %s", action.label)
            return action

    def get_team(self, format_id: str) -> Optional[str]:
        """Get or generate a team for the specified format."""
        # Random battle formats don't need teams
        if "random" in format_id.lower():
            return None

        # Check cache
        if format_id in self._team_cache:
            return self._team_cache[format_id]

        # Import here to avoid circular imports
        from teams import TeamManager

        manager = TeamManager(teams_dir=self.teams_dir)
        packed_team = manager.get_sample_team(format_id)

        # Save for future use
        self._save_team(format_id, packed_team, manager)

        # Cache and return
        self._team_cache[format_id] = packed_team
        logger.info("LLMModelAgent[%s]: loaded team for %s", self.username, format_id)

        return packed_team

    def _save_team(
        self, format_id: str, packed_team: str, manager: "TeamManager"
    ) -> None:
        """Save the team to a file for future reference."""
        os.makedirs(self.teams_dir, exist_ok=True)

        # Create format-specific directory
        format_dir = os.path.join(self.teams_dir, format_id)
        os.makedirs(format_dir, exist_ok=True)

        # Generate a unique filename
        import time

        timestamp = int(time.time())
        filename = f"team_{self.username}_{timestamp}.txt"
        filepath = os.path.join(format_dir, filename)

        # Save as human-readable export format
        try:
            manager.save_team_to_file(packed_team, filepath, as_export=True)
            logger.info("LLMModelAgent[%s]: saved team to %s", self.username, filepath)
        except Exception as e:
            logger.warning(
                "LLMModelAgent[%s]: failed to save team: %s", self.username, e
            )


# ---------------------------------------------------------------------------
# Test function: Run LLM agent vs Random agent
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

    # Configuration
    FORMAT = "gen9randombattle"  # Use random battle to avoid team issues
    USERNAME1 = "LLMBot"
    USERNAME2 = "RandomBot"

    print("=" * 60)
    print(f"Pokemon Showdown - {USERNAME1} (LLM) vs {USERNAME2} (Random)")
    print(f"Format: {FORMAT}")
    print("=" * 60)

    # Check if Ollama is available
    print("\nChecking Ollama connection...")
    try:
        import requests

        response = requests.get(
            f"{LLMModelAgent.DEFAULT_OLLAMA_URL}/api/tags", timeout=5
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
        print(f"ERROR: Cannot connect to Ollama at {LLMModelAgent.DEFAULT_OLLAMA_URL}")
        print("Please ensure Ollama is running with: ollama serve")
        print("And that you have a model installed: ollama pull llama3.2")
        return

    # Create the PS server connection
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

    # Define agent factories
    def llm_agent_factory(player_num: int, username: str) -> Agent:
        return LLMModelAgent(
            player_number=player_num,
            username=username,
            battle_format=FORMAT,
        )

    def random_agent_factory(player_num: int, username: str) -> Agent:
        return RandomAgent(player_num, username)

    # Create game runner (without data collector to avoid set_battle_log error)
    runner = GameRunner(
        ps_server=ps_server,
        agent1_factory=llm_agent_factory,
        agent2_factory=random_agent_factory,
        data_collector=None,
        username1=USERNAME1,
        username2=USERNAME2,
        format=FORMAT,
    )

    # Run the game
    print("\nStarting battle...")
    try:
        winner = runner.run()

        if winner == 1:
            print(f"\n🏆 {USERNAME1} (LLM) wins!")
        elif winner == 2:
            print(f"\n🏆 {USERNAME2} (Random) wins!")
        else:
            print("\n❓ Battle ended without a clear winner")

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

    # Create a mock game state
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

    # Create agent
    agent = LLMModelAgent(
        player_number=1,
        username="LLMBot",
        battle_format="gen9ou",
    )

    print("\nGame State:")
    print(agent._stringify_game_state(gamestate))
    print("\nActions:")
    print(agent._stringify_available_actions(available_actions))

    print("\n" + "=" * 60)
    print("Querying LLM...")
    print("=" * 60)

    try:
        action = agent.decide(gamestate)
        print(f"\n✅ LLM chose: {action.action_type.upper()} - {action.label}")
        print(f"   (action_id: {action.action_id})")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "query":
        test_llm_query()
    else:
        test_llm_vs_random()

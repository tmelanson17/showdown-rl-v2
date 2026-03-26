"""
game_runner.py — Main game orchestrator.

Implements the full sequence from the user's sequence diagram:

    GameRunner                  PSServer              Agent1 / Agent2       DataCollector
       |                           |                        |                     |
       |-- generateAgent(1) ------>|                        |                     |
       |-- registerPlayer(1) ----->|                        |                     |
       |-- generateAgent(2) ------------------------------------------>          |
       |-- startRecord() --------------------------------------------------------->|
       |-- registerPlayer(2) ----->|                        |                     |
       |-- initiateChallenge ----->|                        |                     |
       |<-- handleChallenge -------|                        |                     |
       |-- acceptChallenge ------->|                        |                     |
       |<-- recordIfChallengeFailed ------------------------------------------>   |
       |<-- sendPreview ------------|                       |                     |
       |-- recordPreview --------------------------------------------------------->|
       |-- decideTeamPreview(1) ----------------------------------------->        |
       |-- decideTeamPreview(2) ----------------------------------------->        |
       |-- handleTeamPreview ----->|                        |                     |
       |   [battle loop]           |                        |                     |
       |<-- getGameState(turn) ----|                        |                     |
       |-- maybeDecide(gamestate) ----------------------->   |                     |
       |-- recordStateActionPair -------------------------------------------------------->|
       |-- handleGameState ------->|                        |                     |
       |-- endRecord() ---------------------------------------------------------->|

HumanGameRunner extends GameRunner to support battles against human players on
the Pokemon Showdown server. It only controls Player 1 (the agent) while Player 2
is a human using the PS web client.
"""

from __future__ import annotations
import logging
import time
from typing import Callable, List, Optional, Type

from ps_types import GameState, Action
from agents import Agent, PlayerAgent, ModelAgent, RandomAgent, ReplayAgent
from ps_server import PSServer
from data_collector import DataCollector

logger = logging.getLogger(__name__)

# Type alias for the factory function that GameRunner uses to create agents.
# Signature: (player_number: int, username: str) -> Agent
AgentFactory = Callable[[int, str], Agent]


class GameRunner:
    """Orchestrates a single game (or many via ``run_match``).

    Parameters:
        ps_server:        A connected PSServer instance.
        agent1_factory:   Callable that creates Agent for player 1.
        agent2_factory:   Callable that creates Agent for player 2.
        data_collector:   DataCollector instance (or None to skip recording).
        username1:        Username for player 1 on the PS server.
        username2:        Username for player 2 on the PS server.
        format:           Pokémon format string (default: gen9ou).
    """

    def __init__(
        self,
        ps_server: PSServer,
        agent1_factory: AgentFactory,
        agent2_factory: AgentFactory,
        data_collector: Optional[DataCollector] = None,
        username1: str = "Player1",
        username2: str = "Player2",
        format: str = "gen9ou",
    ) -> None:
        self.ps = ps_server
        self.data_collector = data_collector
        self.username1 = username1
        self.username2 = username2
        self.format = format

        # Factories — called during run() to produce fresh agents each game
        self._agent1_factory = agent1_factory
        self._agent2_factory = agent2_factory

        # Populated during run()
        self.agent1: Optional[Agent] = None
        self.agent2: Optional[Agent] = None

    # -- public -------------------------------------------------------------
    def run(self) -> Optional[int]:
        """Execute one full game.  Returns the winning player number (or None)."""
        # --- agent generation & registration --------------------------------
        self.agent1 = self._generate_agent(1, self.username1, self._agent1_factory)
        self.ps.register_player(1, self.username1)

        self.agent2 = self._generate_agent(2, self.username2, self._agent2_factory)

        # --- start recording ------------------------------------------------
        if self.data_collector:
            self.data_collector.start_record()
            self.data_collector.set_players(self.username1, self.username2)

        self.ps.register_player(2, self.username2)

        # --- get teams from agents (for non-random formats) -----------------
        team1 = self.agent1.get_team(self.format)
        team2 = self.agent2.get_team(self.format)

        if team1:
            logger.info("GameRunner: player 1 using custom team")
            self.ps.set_player_team(1, team1)
        if team2:
            logger.info("GameRunner: player 2 using custom team")
            self.ps.set_player_team(2, team2)

        # --- challenge flow -------------------------------------------------
        self.ps.initiate_challenge(1, 2, format=self.format)
        challenge_accepted = self.ps.handle_challenge(2)

        if not challenge_accepted:
            if self.data_collector:
                self.data_collector.record_if_challenge_failed(
                    "Challenge rejected or timed out"
                )
                self.data_collector.end_record()
            logger.warning("GameRunner: challenge was not accepted — aborting")
            return None

        self.ps.accept_challenge(2)

        # --- team preview ---------------------------------------------------
        preview_state = self.ps.send_preview()

        if self.data_collector:
            self.data_collector.record_preview(preview_state)

        # Handle team preview if required
        # In random battles, team preview might just need default order
        if self.ps.needs_decision(1):
            self.ps.submit_team_preview(1, "default")
        if self.ps.needs_decision(2):
            self.ps.submit_team_preview(2, "default")

        # --- battle loop ----------------------------------------------------
        turn = 0
        winner: Optional[int] = None
        max_turns = 500  # Safety limit

        while turn < max_turns:
            # Check for battle end first
            if self.ps.is_battle_over(None):
                winner = self.ps.get_winner(None)
                break

            # In Pokemon, both players typically choose simultaneously
            # Check which players need to make decisions
            p1_needs_decision = self.ps.needs_decision(1)
            p2_needs_decision = self.ps.needs_decision(2)

            if not p1_needs_decision and not p2_needs_decision:
                # No decisions needed - read more state
                import time

                time.sleep(0.1)
                self.ps._read_all_responses(timeout=1.0)

                # Check again
                if self.ps.is_battle_over(None):
                    winner = self.ps.get_winner(None)
                    break

                p1_needs_decision = self.ps.needs_decision(1)
                p2_needs_decision = self.ps.needs_decision(2)

                if not p1_needs_decision and not p2_needs_decision:
                    # Still nothing - might be waiting, continue
                    continue

            # Handle player 1's decision
            if p1_needs_decision:
                gamestate1 = self.ps._build_game_state(turn=turn, for_player=1)

                if gamestate1.available_actions:
                    action1 = self._maybe_decide(self.agent1, gamestate1)

                    if self.data_collector:
                        self.data_collector.record_state_action_pair(
                            gamestate1, action1
                        )

                    self.ps.submit_action(1, action1)

            # Handle player 2's decision
            if p2_needs_decision:
                gamestate2 = self.ps._build_game_state(turn=turn, for_player=2)

                if gamestate2.available_actions:
                    action2 = self._maybe_decide(self.agent2, gamestate2)

                    if self.data_collector:
                        self.data_collector.record_state_action_pair(
                            gamestate2, action2
                        )

                    self.ps.submit_action(2, action2)

            # Read responses after both players have acted
            import time

            time.sleep(0.1)
            self.ps._read_all_responses(timeout=2.0)

            # Update turn counter based on PS state
            turn = max(turn, self.ps._state.turn)

            if p1_needs_decision or p2_needs_decision:
                turn += 1

        # --- end recording --------------------------------------------------
        if self.data_collector:
            self.data_collector.set_outcome(winner)
            # Include the raw battle log from PSServer
            battle_log = self.ps.get_battle_log()
            if battle_log:
                self.data_collector.set_battle_log(battle_log)
            path = self.data_collector.end_record()
            logger.info("GameRunner: game recorded to %s", path)

        logger.info("GameRunner: game over — winner is Player %s", winner)
        return winner

    def run_match(self, num_games: int = 1) -> List[Optional[int]]:
        """Run *num_games* sequential games.  Returns list of winners."""
        results = []
        for i in range(num_games):
            logger.info("GameRunner: starting game %d/%d", i + 1, num_games)
            winner = self.run()
            results.append(winner)
        return results

    # -- private ------------------------------------------------------------
    @staticmethod
    def _generate_agent(
        player_number: int, username: str, factory: AgentFactory
    ) -> Agent:
        agent = factory(player_number, username)
        logger.info(
            "GameRunner: generated %s for player %d (%s)",
            type(agent).__name__,
            player_number,
            username,
        )
        return agent

    @staticmethod
    def _maybe_decide(agent: Agent, gamestate: GameState) -> Action:
        """Call agent.decide() — the name mirrors the sequence diagram."""
        return agent.decide(gamestate)


class HumanGameRunner:
    """Game runner for battles against human players on Pokemon Showdown.

    This class connects to the Pokemon Showdown server via WebSocket and
    challenges a human player. Only Player 1 (the agent) is controlled
    programmatically; Player 2 actions come from the human via the PS client.

    Parameters:
        agent_factory:    Callable that creates Agent for player 1 (the bot).
        agent_username:   Username for the agent to login as.
        human_username:   Username of the human opponent to challenge.
        format:           Battle format (default: gen9randombattle).
        data_collector:   DataCollector instance (or None to skip recording).
        server_url:       PS server WebSocket URL (default: main server).
        password:         Password for agent login (None for guest mode).

    Example:
        runner = HumanGameRunner(
            agent_factory=lambda n, u: RandomAgent(n, u),
            agent_username="MyBot",
            human_username="ctjn20",
        )
        winner = runner.run()
    """

    DEFAULT_HUMAN_USERNAME = "ctjn20"

    def __init__(
        self,
        agent_factory: AgentFactory,
        agent_username: str = "GuestBot",
        human_username: str = DEFAULT_HUMAN_USERNAME,
        format: str = "gen9randombattle",
        data_collector: Optional[DataCollector] = None,
        server_url: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        # Import here to avoid circular imports and make websockets optional
        from ps_client import PSClient

        self.agent_factory = agent_factory
        self.agent_username = agent_username
        self.human_username = human_username
        self.format = format
        self.data_collector = data_collector
        self.password = password

        # Use default server if not specified
        if server_url is None:
            server_url = PSClient.MAIN_SERVER

        self.client = PSClient(server_url=server_url, username=agent_username)
        self.agent: Optional[Agent] = None
        self._is_local_server = "localhost" in server_url or "127.0.0.1" in server_url

    def connect(self) -> bool:
        """Connect to the Pokemon Showdown server and login."""
        logger.info("HumanGameRunner: connecting to Pokemon Showdown...")

        if not self.client.connect():
            logger.error("HumanGameRunner: failed to connect")
            return False

        # Login with PS authentication server
        # - If password provided, uses registered account login
        # - Otherwise uses unregistered name assertion (works for any unused name)
        success = self.client.login(self.agent_username, self.password or "")

        if not success:
            logger.warning(
                "HumanGameRunner: login failed - name may be registered or invalid"
            )
            return False

        return True

    def disconnect(self) -> None:
        """Disconnect from the server."""
        self.client.disconnect()

    def run(self, timeout: float = 300.0) -> Optional[str]:
        """Run a battle against the human opponent.

        Args:
            timeout: Maximum time to wait for battle to complete (seconds).

        Returns:
            The winner's username, or None if battle didn't complete.
        """
        # Generate agent
        self.agent = self.agent_factory(1, self.agent_username)
        logger.info(
            "HumanGameRunner: using %s agent as %s",
            type(self.agent).__name__,
            self.agent_username,
        )

        # Start recording
        if self.data_collector:
            self.data_collector.start_record()
            self.data_collector.set_players(self.agent_username, self.human_username)

        # Get team from agent if needed
        team = self.agent.get_team(self.format)

        # Challenge the human, retry with different teams if rejected
        max_retries = 3
        for attempt in range(max_retries):
            logger.info(
                "HumanGameRunner: challenging %s to %s (attempt %d)",
                self.human_username,
                self.format,
                attempt + 1,
            )
            if attempt == 0:
                print(
                    f"\nChallenging {self.human_username} to a {self.format} battle..."
                )
                print(f"   Waiting for them to accept...\n")

            success, rejection = self.client.challenge(
                self.human_username, self.format, team
            )

            if success:
                break

            # Team was rejected
            print(f"Team rejected: {rejection}")
            logger.warning("HumanGameRunner: team rejected: %s", rejection)

            if attempt < max_retries - 1:
                # Try to get a different team
                print(f"   Retrying with a different team...")
                # Clear agent's team cache to get a new one
                if hasattr(self.agent, "_team_cache"):
                    self.agent._team_cache.pop(self.format, None)
                team = self.agent.get_team(self.format)
            else:
                print(
                    f"Failed after {max_retries} attempts. Team validation failed."
                )
                if self.data_collector:
                    self.data_collector.record_if_challenge_failed(
                        f"Team rejected: {rejection}"
                    )
                    self.data_collector.end_record()
                return None

        # Wait for battle to start
        battle_room = self.client.wait_for_battle(timeout=120.0)
        if not battle_room:
            logger.warning(
                "HumanGameRunner: battle didn't start (challenge not accepted?)"
            )
            print("Challenge was not accepted or timed out.")
            if self.data_collector:
                self.data_collector.record_if_challenge_failed("Challenge not accepted")
                self.data_collector.end_record()
            return None

        logger.info("HumanGameRunner: battle started in room %s", battle_room)
        print(f"Battle started! Room: {battle_room}\n")

        # Battle loop
        start_time = time.time()
        turn = 0

        while time.time() - start_time < timeout:
            # Check for battle end
            if self.client.is_battle_over():
                break

            # Wait for a request (our turn to act)
            request = self.client.wait_for_request(timeout=30.0)

            if request is None:
                # No request, check if battle ended
                if self.client.is_battle_over():
                    break
                continue

            # Handle team preview
            if request.get("teamPreview"):
                logger.info("HumanGameRunner: team preview")
                # For now, use default order
                team_size = len(request.get("side", {}).get("pokemon", []))
                order = "".join(str(i) for i in range(1, min(team_size + 1, 7)))
                self.client.choose(f"team {order}")
                continue

            # Handle force switch
            if request.get("forceSwitch"):
                # Build game state and let agent decide
                gamestate = self._build_game_state(request, turn)
                if gamestate.available_actions:
                    action = self.agent.decide(gamestate)

                    if self.data_collector:
                        self.data_collector.record_state_action_pair(gamestate, action)

                    choice = self._action_to_choice(action)
                    self.client.choose(choice)
                continue

            # Handle wait (opponent still choosing)
            if request.get("wait"):
                continue

            # Normal turn - agent makes a decision
            if request.get("active"):
                turn = request.get("rqid", turn)

                gamestate = self._build_game_state(request, turn)

                if gamestate.available_actions:
                    action = self.agent.decide(gamestate)

                    if self.data_collector:
                        self.data_collector.record_state_action_pair(gamestate, action)

                    choice = self._action_to_choice(action)
                    logger.info("HumanGameRunner: choosing %s", choice)
                    self.client.choose(choice)

        # Battle ended
        winner = self.client.get_winner()
        logger.info("HumanGameRunner: battle ended, winner: %s", winner)

        if winner:
            if winner.lower() == self.agent_username.lower():
                print(f"\nVictory! {self.agent_username} wins!")
            elif winner.lower() == self.human_username.lower():
                print(f"\nDefeat. {self.human_username} wins.")
            else:
                print(f"\nBattle ended. Winner: {winner}")

        # End recording
        if self.data_collector:
            # Determine winner number
            winner_num = None
            if winner:
                if winner.lower() == self.agent_username.lower():
                    winner_num = 1
                elif winner.lower() == self.human_username.lower():
                    winner_num = 2

            self.data_collector.set_outcome(winner_num)
            battle_log = self.client.get_battle_log()
            if battle_log:
                self.data_collector.set_battle_log(battle_log)
            path = self.data_collector.end_record()
            logger.info("HumanGameRunner: game recorded to %s", path)

        return winner

    def _build_game_state(self, request: dict, turn: int) -> GameState:
        """Build a GameState from a PS request object."""
        from ps_types import Side, PokemonSlot

        available_actions: List[Action] = []

        # Parse active pokemon moves
        active_data = request.get("active", [{}])
        if active_data:
            moves = active_data[0].get("moves", [])
            for i, move in enumerate(moves):
                if not move.get("disabled"):
                    move_name = move.get("move", f"Move {i + 1}")
                    available_actions.append(
                        Action(
                            action_id=f"move {i + 1}",
                            action_type="move",
                            target_index=i + 1,
                            label=move_name,
                        )
                    )

        # Parse switches
        side_data = request.get("side", {})
        pokemon_list = side_data.get("pokemon", [])
        for i, poke in enumerate(pokemon_list):
            if not poke.get("active") and poke.get("condition", "0 fnt") != "0 fnt":
                species = poke.get("details", "").split(",")[0]
                available_actions.append(
                    Action(
                        action_id=f"switch {i + 1}",
                        action_type="switch",
                        target_index=i + 1,
                        label=species,
                    )
                )

        # Build sides
        my_pokemon = []
        for poke in pokemon_list:
            details = poke.get("details", "")
            species = details.split(",")[0] if details else "Unknown"
            condition = poke.get("condition", "100/100")

            # Parse HP as percentage
            hp_pct = 1.0
            if "/" in condition:
                hp_parts = condition.split()[0].split("/")
                try:
                    hp_current = int(hp_parts[0])
                    hp_max = int(hp_parts[1])
                    hp_pct = hp_current / hp_max if hp_max > 0 else 0.0
                except (ValueError, IndexError):
                    pass
            elif condition == "0 fnt":
                hp_pct = 0.0

            # Extract moves for this Pokemon
            poke_moves = []
            for move_data in poke.get("moves", []):
                # Moves can be stored as move IDs or move names
                if isinstance(move_data, str):
                    # Convert move ID to readable name (e.g., "thunderbolt" -> "Thunderbolt")
                    move_name = move_data.replace("-", " ").title()
                    poke_moves.append(move_name)
                elif isinstance(move_data, dict):
                    poke_moves.append(
                        move_data.get("move", move_data.get("id", "Unknown"))
                    )

            my_pokemon.append(
                PokemonSlot(
                    species=species,
                    hp_pct=hp_pct,
                    status=poke.get("status"),
                    is_active=poke.get("active", False),
                    moves=poke_moves,
                )
            )

        my_side = Side(
            player_id=1,
            username=self.agent_username,
            pokemon=my_pokemon,
        )

        # Opponent side is mostly unknown
        opponent_side = Side(
            player_id=2,
            username=self.human_username,
            pokemon=[],
        )

        raw_log = self.client.get_battle_log()
        raw_protocol = "\n".join(raw_log[-50:]) if raw_log else None

        return GameState(
            turn=turn,
            active_player=1,
            sides=[my_side, opponent_side],
            available_actions=available_actions,
            raw_protocol=raw_protocol,
        )

    def _action_to_choice(self, action: Action) -> str:
        """Convert an Action to a PS choice string."""
        # The action_id is already in the correct format (e.g., "move 1", "switch 3")
        return action.action_id

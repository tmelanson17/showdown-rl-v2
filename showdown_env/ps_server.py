"""
ps_server.py — Wrapper around a Pokémon Showdown server instance.

This module spawns a local PS simulator subprocess and communicates with it
via stdin/stdout using the PS protocol documented in:
- https://github.com/smogon/pokemon-showdown/blob/master/sim/SIMULATOR.md
- https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md

The class is synchronous and blocking — GameRunner calls it on its own thread.
"""

from __future__ import annotations
import logging
import subprocess
import json
import queue
import threading
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from ps_types import GameState, Action, Side, PokemonSlot

logger = logging.getLogger(__name__)


@dataclass
class BattleState:
    """Internal tracking of battle state."""

    turn: int = 0
    p1_pokemon: List[Dict[str, Any]] = field(default_factory=list)
    p2_pokemon: List[Dict[str, Any]] = field(default_factory=list)
    p1_active: Optional[str] = None
    p2_active: Optional[str] = None
    p1_request: Optional[Dict[str, Any]] = None
    p2_request: Optional[Dict[str, Any]] = None
    winner: Optional[str] = None
    battle_started: bool = False
    team_preview: bool = False
    raw_log: List[str] = field(default_factory=list)


class PSServer:
    """Facade over a Pokémon Showdown simulator subprocess.

    Parameters:
        ps_path: Path to the pokemon-showdown directory (in WSL).
        format_id: The battle format (e.g., "gen9randombattle").
    """

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        ps_path: str = "/mnt/c/Users/tmela/development/pokemans/pokemon-showdown",
        format_id: str = "gen9randombattle",
    ) -> None:
        self.server_url = server_url
        self.ps_path = ps_path
        self.format_id = format_id
        self._connected = False
        self._process: Optional[subprocess.Popen] = None
        self._state = BattleState()
        self._lock = threading.Lock()
        self._usernames: Dict[int, str] = {}
        self._output_buffer: List[str] = []

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        """Start the PS simulator subprocess."""
        logger.info("PSServer: starting simulator subprocess at %s", self.ps_path)

        # Launch the simulator via WSL with --skip-build for faster startup
        cmd = [
            "wsl",
            "-e",
            "bash",
            "-c",
            f"cd {self.ps_path} && node pokemon-showdown --skip-build simulate-battle",
        ]

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Start a reader thread for non-blocking stdout reading
        self._output_queue: queue.Queue = queue.Queue()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        self._connected = True
        self._state = BattleState()
        logger.info(
            "PSServer: simulator subprocess started (PID: %s)", self._process.pid
        )

    def _reader_loop(self) -> None:
        """Background thread that reads stdout line by line."""
        try:
            if self._process and self._process.stdout:
                for line in iter(self._process.stdout.readline, ""):
                    if line:
                        self._output_queue.put(line)
                    if not self._connected:
                        break
        except Exception as e:
            logger.error("PSServer: reader thread error: %s", e)

    def disconnect(self) -> None:
        """Terminate the PS simulator subprocess."""
        if self._process:
            logger.info("PSServer: shutting down simulator subprocess")
            try:
                self._process.stdin.close()
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception as e:
                logger.warning("PSServer: error during shutdown: %s", e)
                try:
                    self._process.kill()
                except Exception:
                    pass
            finally:
                self._process = None
        self._connected = False

    def _send(self, message: str) -> None:
        """Send a message to the simulator."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("PSServer: not connected")

        logger.debug("PSServer SEND: %s", message.strip())
        self._process.stdin.write(message + "\n")
        self._process.stdin.flush()

    def _read_until_complete(self, timeout: float = 10.0) -> List[str]:
        """Read output from simulator until we get a complete response block.

        PS simulator sends responses in blocks separated by double newlines.
        Each block starts with a type (update, sideupdate, end) followed by messages.

        Uses queue-based reading from the background reader thread.
        """
        lines: List[str] = []
        start_time = time.time()
        buffer = ""

        while True:
            if time.time() - start_time > timeout:
                logger.debug("PSServer: read timeout after %.1fs", timeout)
                break

            try:
                # Try to get a line from the queue with a short timeout
                line = self._output_queue.get(timeout=0.1)
                buffer += line

                # Check for double newline (end of message block)
                if buffer.endswith("\n\n") or (buffer.strip() and "\n\n" in buffer):
                    break

            except queue.Empty:
                # No data available
                if buffer and buffer.strip():
                    # We have some data, might be complete enough
                    break
                continue

        # Parse the buffer into lines
        if buffer:
            lines = buffer.strip().split("\n")
            for line in lines:
                logger.debug("PSServer RECV: %s", line)

        return lines

    def _read_all_responses(self, timeout: float = 5.0) -> List[List[str]]:
        """Read all pending response blocks from the simulator."""
        all_blocks: List[List[str]] = []
        start_time = time.time()
        buffer = ""

        while time.time() - start_time < timeout:
            try:
                # Try to get lines from the queue
                line = self._output_queue.get(timeout=0.2)
                buffer += line

                # Check for block separator
                if "\n\n" in buffer:
                    # Split on double newlines
                    parts = buffer.split("\n\n")
                    for part in parts[:-1]:  # All complete blocks
                        if part.strip():
                            block_lines = part.strip().split("\n")
                            all_blocks.append(block_lines)
                            # Parse and update internal state
                            self._parse_response(block_lines)

                    # Keep the last partial block
                    buffer = parts[-1]

                    # Check if we're waiting for player input
                    if self._state.p1_request or self._state.p2_request:
                        break

                    # Check if battle ended
                    if self._state.winner:
                        break

            except queue.Empty:
                # No more data available right now
                # If we have pending buffer content, process it
                if buffer.strip():
                    block_lines = buffer.strip().split("\n")
                    all_blocks.append(block_lines)
                    self._parse_response(block_lines)
                    buffer = ""

                # Check if we have what we need
                if (
                    self._state.p1_request
                    or self._state.p2_request
                    or self._state.winner
                ):
                    break

                # Give more time if nothing yet
                if not all_blocks:
                    continue
                else:
                    break

        return all_blocks

    def _parse_response(self, lines: List[str]) -> None:
        """Parse a response block and update internal state."""
        if not lines:
            return

        msg_type = lines[0] if lines else ""

        if msg_type == "update":
            self._parse_update(lines[1:])
        elif msg_type == "sideupdate":
            if len(lines) >= 3:
                player_id = lines[1]  # "p1" or "p2"
                self._parse_sideupdate(player_id, lines[2:])
        elif msg_type == "end":
            if len(lines) >= 2:
                self._parse_end(lines[1])

    def _parse_update(self, lines: List[str]) -> None:
        """Parse update messages (sent to all players)."""
        for line in lines:
            self._state.raw_log.append(line)

            if not line.startswith("|"):
                continue

            parts = line[1:].split("|")
            if not parts:
                continue

            msg_type = parts[0]

            if msg_type == "turn":
                self._state.turn = int(parts[1]) if len(parts) > 1 else 0
                logger.info("PSServer: Turn %d", self._state.turn)

            elif msg_type == "win":
                self._state.winner = parts[1] if len(parts) > 1 else None
                logger.info("PSServer: battle won by %s", self._state.winner)

            elif msg_type == "tie":
                self._state.winner = "tie"
                logger.info("PSServer: battle ended in tie")

            elif msg_type == "start":
                self._state.battle_started = True
                logger.info("PSServer: battle started")

            elif msg_type == "teampreview":
                self._state.team_preview = True
                logger.info("PSServer: team preview phase")

            elif msg_type == "switch" or msg_type == "drag":
                # |switch|p1a: Pikachu|Pikachu, L50, M|100/100
                if len(parts) >= 4:
                    pokemon_id = parts[1]  # e.g., "p1a: Pikachu"
                    if pokemon_id.startswith("p1"):
                        self._state.p1_active = pokemon_id
                    elif pokemon_id.startswith("p2"):
                        self._state.p2_active = pokemon_id

            elif msg_type == "poke":
                # |poke|p1|Pikachu, L50, M|item
                if len(parts) >= 3:
                    player = parts[1]
                    details = parts[2]
                    species = details.split(",")[0]
                    poke_data = {"species": species, "details": details}
                    if player == "p1":
                        self._state.p1_pokemon.append(poke_data)
                    elif player == "p2":
                        self._state.p2_pokemon.append(poke_data)

            elif msg_type == "faint":
                # |faint|p1a: Pikachu
                if len(parts) >= 2:
                    pokemon_id = parts[1]
                    logger.info("PSServer: %s fainted", pokemon_id)

    def _parse_sideupdate(self, player_id: str, lines: List[str]) -> None:
        """Parse sideupdate messages (sent to specific player)."""
        for line in lines:
            if line.startswith("|request|"):
                request_json = line[9:]  # Skip "|request|"
                if request_json and request_json.strip():
                    try:
                        request = json.loads(request_json)
                        if player_id == "p1":
                            self._state.p1_request = request
                            logger.debug("PSServer: received request for p1")
                        elif player_id == "p2":
                            self._state.p2_request = request
                            logger.debug("PSServer: received request for p2")
                    except json.JSONDecodeError as e:
                        logger.warning("PSServer: failed to parse request JSON: %s", e)

    def _parse_end(self, data: str) -> None:
        """Parse end message with battle result."""
        try:
            result = json.loads(data)
            if "winner" in result:
                self._state.winner = result["winner"]
            logger.info("PSServer: battle ended - %s", result)
        except json.JSONDecodeError:
            pass

    # -- team management -------------------------------------------------------
    def set_player_team(self, player_number: int, packed_team: str) -> None:
        """Set the team for a player (in packed format)."""
        if not hasattr(self, "_teams"):
            self._teams: Dict[int, str] = {}
        self._teams[player_number] = packed_team
        logger.info("PSServer: set team for player %d", player_number)

    def get_player_team(self, player_number: int) -> Optional[str]:
        """Get the team for a player."""
        if hasattr(self, "_teams"):
            return self._teams.get(player_number)
        return None

    # -- player registration ------------------------------------------------
    def register_player(self, player_number: int, username: str) -> None:
        """Register a player with the PS simulator."""
        self._usernames[player_number] = username
        logger.info("PSServer: registering player %d as '%s'", player_number, username)

    # -- challenge flow (simplified for local sim) --------------------------
    def initiate_challenge(
        self, challenger_id: int, target_id: int, format: str = "gen9randombattle"
    ) -> None:
        """Start a battle between two players.

        For non-random formats, teams must be set via set_player_team() before calling this.
        """
        self.format_id = format
        logger.info("PSServer: initiating battle (format: %s)", self.format_id)

        # Send start command
        start_cmd = f'>start {{"formatid":"{self.format_id}"}}'
        self._send(start_cmd)

        # Register both players (with teams if available)
        p1_name = self._usernames.get(1, "Player1")
        p2_name = self._usernames.get(2, "Player2")

        # Build player commands with optional teams
        p1_team = self.get_player_team(1)
        p2_team = self.get_player_team(2)

        if p1_team:
            self._send(f'>player p1 {{"name":"{p1_name}","team":"{p1_team}"}}')
        else:
            self._send(f'>player p1 {{"name":"{p1_name}"}}')

        if p2_team:
            self._send(f'>player p2 {{"name":"{p2_name}","team":"{p2_team}"}}')
        else:
            self._send(f'>player p2 {{"name":"{p2_name}"}}')

        # Read initial responses
        time.sleep(0.5)  # Give the simulator time to process
        self._read_all_responses(timeout=5.0)

    def handle_challenge(self, target_id: int) -> bool:
        """In local sim mode, challenge is always accepted."""
        return True

    def accept_challenge(self, player_id: int) -> None:
        """In local sim mode, no explicit accept needed."""
        pass

    # -- preview ------------------------------------------------------------
    def send_preview(self) -> GameState:
        """Get the team preview GameState."""
        logger.info("PSServer: getting team preview state")

        # Read any pending messages to get team preview data
        self._read_all_responses(timeout=3.0)

        # Build GameState from current state
        return self._build_game_state(turn=0, for_player=1)

    def handle_team_preview(self, action: Action) -> None:
        """Submit a team-preview decision."""
        logger.info("PSServer: team preview decision — %s", action.label)
        # In random battles, team preview may send default

    def submit_team_preview(self, player: int, order: str = "default") -> None:
        """Submit team preview order for a specific player."""
        player_id = f"p{player}"
        command = f">{player_id} {order}"
        logger.info("PSServer: submitting team preview for %s: %s", player_id, order)
        self._send(command)

    # -- battle loop --------------------------------------------------------
    def get_game_state(self, turn: int) -> GameState:
        """Fetch the current battle state for the specified turn."""
        logger.debug("PSServer: getting game state for turn %d", turn)

        # Read any pending messages
        self._read_all_responses(timeout=2.0)

        # Determine which player needs to make a decision
        active_player = 1
        if self._state.p1_request and not self._state.p2_request:
            active_player = 1
        elif self._state.p2_request and not self._state.p1_request:
            active_player = 2
        elif self._state.p1_request and self._state.p2_request:
            # Both need to decide (simultaneous turns in Pokemon)
            active_player = 1  # Start with p1

        return self._build_game_state(turn=self._state.turn, for_player=active_player)

    def _build_game_state(self, turn: int, for_player: int) -> GameState:
        """Build a GameState from the current internal state."""
        # Get the active request for the player
        request = self._state.p1_request if for_player == 1 else self._state.p2_request

        # Build sides
        sides = self._build_sides(request, for_player)

        # Build available actions
        actions = self._build_actions(request, for_player)

        # Build raw protocol log
        raw_protocol = "\n".join(self._state.raw_log[-50:])  # Last 50 lines

        return GameState(
            turn=turn,
            active_player=for_player,
            sides=sides,
            available_actions=actions,
            raw_protocol=raw_protocol,
        )

    def _build_sides(self, request: Optional[Dict], for_player: int) -> List[Side]:
        """Build Side objects from request data."""
        sides = []

        # Player's side (from request if available)
        if request and "side" in request:
            side_data = request["side"]
            pokemon_list = []

            for i, poke in enumerate(side_data.get("pokemon", [])):
                # Parse condition like "227/227" or "100/100 par"
                condition = poke.get("condition", "100/100")
                hp_pct = self._parse_hp_condition(condition)
                status = self._parse_status_condition(condition)

                pokemon_list.append(
                    PokemonSlot(
                        species=poke.get("details", "Unknown").split(",")[0],
                        hp_pct=hp_pct,
                        status=status,
                        is_active=poke.get("active", False),
                    )
                )

            sides.append(
                Side(
                    player_id=for_player,
                    username=side_data.get("name", f"Player{for_player}"),
                    pokemon=pokemon_list,
                )
            )
        else:
            # Fallback
            sides.append(
                Side(
                    player_id=for_player,
                    username=self._usernames.get(for_player, f"Player{for_player}"),
                    pokemon=[],
                )
            )

        # Opponent's side (limited information)
        opponent_player = 2 if for_player == 1 else 1
        opponent_pokemon = (
            self._state.p2_pokemon if for_player == 1 else self._state.p1_pokemon
        )

        opponent_poke_list = []
        for poke in opponent_pokemon:
            opponent_poke_list.append(
                PokemonSlot(
                    species=poke.get("species", "Unknown"),
                    hp_pct=1.0,  # Unknown exact HP
                    is_active=False,
                )
            )

        sides.append(
            Side(
                player_id=opponent_player,
                username=self._usernames.get(
                    opponent_player, f"Player{opponent_player}"
                ),
                pokemon=opponent_poke_list,
            )
        )

        return sides

    def _build_actions(self, request: Optional[Dict], for_player: int) -> List[Action]:
        """Build available actions from request data."""
        actions = []

        if not request:
            return actions

        # Check for team preview
        if request.get("teamPreview"):
            # Team preview - offer team order selection
            actions.append(Action("default", "team", 1, "Use default team order"))
            return actions

        # Check for wait (no action needed)
        if request.get("wait"):
            return actions

        # Check for force switch
        if request.get("forceSwitch"):
            # Must switch - add switch options
            side = request.get("side", {})
            for i, poke in enumerate(side.get("pokemon", [])):
                if not poke.get("active", False):
                    condition = poke.get("condition", "")
                    if condition != "0 fnt" and "fnt" not in condition:
                        species = poke.get("details", "Unknown").split(",")[0]
                        actions.append(
                            Action(
                                f"switch {i + 1}",
                                "switch",
                                i + 1,
                                f"Switch to {species}",
                            )
                        )
            return actions

        # Normal turn - add move and switch options
        active = request.get("active", [])
        if active:
            active_data = active[0]
            moves = active_data.get("moves", [])

            for i, move in enumerate(moves):
                if not move.get("disabled", False) and move.get("pp", 1) > 0:
                    move_name = move.get("move", f"Move {i + 1}")
                    actions.append(
                        Action(
                            f"move {i + 1}",
                            "move",
                            i + 1,
                            move_name,
                        )
                    )

        # Add switch options (if not trapped)
        side = request.get("side", {})
        trapped = False
        if active:
            trapped = active[0].get("trapped", False) or active[0].get(
                "maybeTrapped", False
            )

        if not trapped:
            for i, poke in enumerate(side.get("pokemon", [])):
                if not poke.get("active", False):
                    condition = poke.get("condition", "")
                    if condition != "0 fnt" and "fnt" not in condition:
                        species = poke.get("details", "Unknown").split(",")[0]
                        actions.append(
                            Action(
                                f"switch {i + 1}",
                                "switch",
                                i + 1,
                                f"Switch to {species}",
                            )
                        )

        return actions

    def _parse_hp_condition(self, condition: str) -> float:
        """Parse HP from condition string like '227/227' or '50/100 par'."""
        if not condition or condition == "0 fnt" or "fnt" in condition:
            return 0.0

        # Split off status
        hp_part = condition.split()[0]

        if "/" in hp_part:
            parts = hp_part.split("/")
            try:
                current = int(parts[0])
                maximum = int(parts[1])
                return current / maximum if maximum > 0 else 0.0
            except ValueError:
                return 1.0

        return 1.0

    def _parse_status_condition(self, condition: str) -> Optional[str]:
        """Parse status from condition string like '227/227 par'."""
        parts = condition.split()
        if len(parts) > 1:
            status = parts[1]
            if status in ("brn", "par", "slp", "frz", "psn", "tox", "fnt"):
                return status
        return None

    def handle_game_state(self, action: Action) -> None:
        """Submit an in-battle action to PS (legacy interface)."""
        # Determine which player is making this action based on pending requests
        if self._state.p1_request:
            player = 1
        elif self._state.p2_request:
            player = 2
        else:
            player = 1  # fallback

        self.submit_action(player, action)

    def submit_action(self, player: int, action: Action) -> None:
        """Submit an action for a specific player."""
        player_id = f"p{player}"
        command = f">{player_id} {action.to_ps_command()}"
        logger.info(
            "PSServer: player %d submitting action — %s", player, action.to_ps_command()
        )

        self._send(command)

        # Clear the request for this player
        if player == 1:
            self._state.p1_request = None
        else:
            self._state.p2_request = None

        # Give simulator time to process
        time.sleep(0.1)

    # -- game outcome -------------------------------------------------------
    def is_battle_over(self, gamestate: Optional[GameState] = None) -> bool:
        """Check whether the battle has ended."""
        # Read latest state
        self._read_all_responses(timeout=1.0)
        return self._state.winner is not None

    def get_winner(self, gamestate: Optional[GameState] = None) -> Optional[int]:
        """Return the winning player number, or None if no winner yet."""
        if self._state.winner is None:
            return None

        if self._state.winner == "tie":
            return None

        # Match winner name to player number
        for player_num, username in self._usernames.items():
            if username.lower() == self._state.winner.lower():
                return player_num

        # Try to match by looking at the exact winner string
        if self._state.winner:
            # Sometimes winner is just the username
            if self._state.winner == self._usernames.get(1):
                return 1
            elif self._state.winner == self._usernames.get(2):
                return 2

        return None

    def get_battle_log(self) -> List[str]:
        """Return the full battle log."""
        return self._state.raw_log.copy()

    def needs_decision(self, player: int) -> bool:
        """Check if a player needs to make a decision."""
        if player == 1:
            return self._state.p1_request is not None
        else:
            return self._state.p2_request is not None

    def get_request(self, player: int) -> Optional[Dict[str, Any]]:
        """Get the current request for a player."""
        if player == 1:
            return self._state.p1_request
        else:
            return self._state.p2_request

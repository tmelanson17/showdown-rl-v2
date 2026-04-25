"""
ps_client.py — WebSocket client for connecting to Pokemon Showdown server.

This module provides a client that connects to the actual Pokemon Showdown
server (or a local instance) via WebSocket, allowing battles against human players.

Usage:
    client = PSClient(server_url="wss://sim3.psim.us/showdown/websocket")
    client.connect()
    client.login("username", "password")  # or guest login
    client.challenge("opponent_username", "gen9randombattle")
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
import threading
import time
import queue
from typing import Optional, Dict, Any, List, Callable, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Try to import websockets, but make it optional
try:
    import websockets
    import websockets.sync.client as ws_sync

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not installed. Run: pip install websockets")


@dataclass
class BattleState:
    """State of an ongoing battle."""

    room_id: str = ""
    turn: int = 0
    p1_pokemon: List[Dict[str, Any]] = field(default_factory=list)
    p2_pokemon: List[Dict[str, Any]] = field(default_factory=list)
    p1_active: Optional[str] = None
    p2_active: Optional[str] = None
    request: Optional[Dict[str, Any]] = None
    winner: Optional[str] = None
    battle_started: bool = False
    team_preview: bool = False
    raw_log: List[str] = field(default_factory=list)
    my_player_id: Optional[str] = None  # "p1" or "p2"
    last_error: Optional[str] = None  # last |error| from a rejected choice


class PSClient:
    """WebSocket client for Pokemon Showdown server.

    Supports connecting to the real PS server to battle human players.

    Parameters:
        server_url: WebSocket URL for PS server.
                   Main server: wss://sim3.psim.us/showdown/websocket
                   Local: ws://localhost:8000/showdown/websocket
        username: Username to login with (or for guest mode).
    """

    # Main Pokemon Showdown servers
    MAIN_SERVER = "wss://sim3.psim.us/showdown/websocket"
    LOCAL_SERVER = "ws://localhost:8000/showdown/websocket"

    def __init__(
        self,
        server_url: str = MAIN_SERVER,
        username: str = "Guest",
    ) -> None:
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "websockets library required. Run: pip install websockets"
            )

        self.server_url = server_url
        self.username = username
        self._ws: Optional[Any] = None
        self._connected = False
        self._logged_in = False
        self._challstr: Optional[str] = None

        # Message handling
        self._message_queue: queue.Queue = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None

        # Battle state
        self._battles: Dict[str, BattleState] = {}
        self._active_battle: Optional[str] = None
        self._current_username: Optional[str] = None  # Actual username from server
        self._last_popup: Optional[str] = (
            None  # Last popup message (for rejection detection)
        )

        # Query responses (keyed by query type, e.g. "userdetails")
        self._query_responses: Dict[str, Any] = {}

        # Ladder search state
        self._searching: bool = False

        # Callbacks
        self._on_challenge: Optional[Callable[[str, str], None]] = None
        self._on_battle_start: Optional[Callable[[str], None]] = None
        self._on_request: Optional[Callable[[Dict], None]] = None
        self._on_battle_end: Optional[Callable[[str], None]] = None

    @property
    def battle_state(self) -> Optional[BattleState]:
        """Get the active battle state."""
        if self._active_battle and self._active_battle in self._battles:
            return self._battles[self._active_battle]
        return None

    def connect(self) -> bool:
        """Connect to the Pokemon Showdown server."""
        logger.info("PSClient: connecting to %s", self.server_url)

        try:
            self._ws = ws_sync.connect(self.server_url)
            self._connected = True

            # Start reader thread
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self._reader_thread.start()

            # Wait for challstr
            timeout = 10.0
            start = time.time()
            while self._challstr is None and time.time() - start < timeout:
                time.sleep(0.1)

            if self._challstr:
                logger.info("PSClient: connected successfully")
                return True
            else:
                logger.warning("PSClient: connected but no challstr received")
                return True  # Still connected, just can't login

        except Exception as e:
            logger.error("PSClient: connection failed: %s", e)
            return False

    def disconnect(self) -> None:
        """Disconnect from the server."""
        # If in an existing battle, forfeit
        if self._active_battle:
            self.forfeit()
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except:
                pass
        self._ws = None
        logger.info("PSClient: disconnected")

    def _reader_loop(self) -> None:
        """Background thread to read WebSocket messages."""
        while self._connected and self._ws:
            try:
                message = self._ws.recv()
                if message:
                    self._handle_message(message)
            except Exception as e:
                if self._connected:
                    logger.error("PSClient: reader error: %s", e)
                break
        # Mark disconnected so callers (wait_for_request, etc.) don't block forever
        self._connected = False

    def _send(self, message: str) -> None:
        """Send a message to the server."""
        if not self._ws:
            raise RuntimeError("PSClient: not connected")

        logger.debug("PSClient SEND: %s", message[:200])
        self._ws.send(message)

    def _send_to_room(self, room: str, message: str) -> None:
        """Send a message to a specific room."""
        self._send(f"{room}|{message}")

    def _handle_message(self, raw_message: str) -> None:
        """Handle an incoming message from the server."""
        lines = raw_message.strip().split("\n")

        # Check if first line is a room identifier
        room = ""
        if lines and lines[0].startswith(">"):
            room = lines[0][1:]
            lines = lines[1:]

        for line in lines:
            self._parse_line(room, line)

    def _parse_line(self, room: str, line: str) -> None:
        """Parse a single protocol line."""
        if not line or not line.startswith("|"):
            return

        parts = line.split("|")
        if len(parts) < 2:
            return

        msg_type = parts[1]

        # Global messages
        if msg_type == "challstr":
            self._challstr = "|".join(parts[2:])
            logger.debug("PSClient: received challstr")

        elif msg_type == "updateuser":
            # |updateuser|username|named|avatar
            if len(parts) >= 3:
                new_name = parts[2].strip()
                if new_name.startswith(" "):
                    new_name = new_name[1:]
                # Track current username (strip guest prefix)
                self._current_username = new_name
                # Remove guest prefix if present
                if new_name.lower() != "guest":
                    self._logged_in = True
                    logger.info("PSClient: logged in as %s", new_name)

        elif msg_type == "updatechallenges":
            # |updatechallenges|{"challengesFrom":{"user":"format"},"challengeTo":null}
            if len(parts) >= 3:
                try:
                    challenges = json.loads(parts[2])
                    # Store pending challenges
                    self._pending_challenges = challenges.get("challengesFrom", {})
                    if self._pending_challenges:
                        logger.info(
                            "PSClient: received challenge(s) from: %s",
                            list(self._pending_challenges.keys()),
                        )
                    challenge_to = challenges.get("challengeTo")
                    if challenge_to:
                        logger.info(
                            "PSClient: outgoing challenge registered — to=%s format=%s",
                            challenge_to.get("to"),
                            challenge_to.get("format"),
                        )
                    if challenges.get("challengesFrom") and self._on_challenge:
                        for user, fmt in challenges["challengesFrom"].items():
                            self._on_challenge(user, fmt)
                except json.JSONDecodeError:
                    pass

        elif msg_type == "updatesearch":
            # |updatesearch|{"searching":["gen9randombattle"],"games":null}
            if len(parts) >= 3:
                logger.info("Message parts: %s|%s|%s", parts[0], parts[1], parts[2])
                try:
                    search = json.loads(parts[2])
                    searching = search.get("searching", [])
                    games = search.get("games")
                    self._searching = bool(searching)
                    if searching:
                        logger.info("PSClient: searching ladder for %s", searching)
                    elif games:
                        logger.info(
                            "PSClient: matched into game(s): %s", list(games.keys())
                        )
                except json.JSONDecodeError:
                    pass

        elif msg_type == "popup":
            # |popup|message
            if len(parts) >= 3:
                popup_text = "|".join(parts[2:])
                self._last_popup = popup_text
                logger.warning("PSClient: popup: %s", popup_text)

        elif msg_type == "queryresponse":
            # |queryresponse|userdetails|{json} or null
            if len(parts) >= 4:
                query_type = parts[2]
                payload = "|".join(parts[3:])
                try:
                    self._query_responses[query_type] = json.loads(payload)
                except json.JSONDecodeError:
                    self._query_responses[query_type] = payload
                logger.debug(
                    "PSClient: queryresponse[%s]: %s", query_type, payload[:200]
                )

        elif msg_type == "pm":
            # |pm|~|~|/challenge opponent,format
            if len(parts) >= 5:
                sender = parts[2]
                message = parts[4]
                logger.info("PSClient: PM from %s: %s", sender, message)

        # Battle messages
        elif room.startswith("battle-"):
            if msg_type == "init" and parts[2] == "battle":
                self._update_battle_room(room)
            self._parse_battle_message(room, msg_type, parts)

        # Check for other message types as needed (e.g., lobby chat, etc.)
        elif msg_type == "init":
            logger.info(
                "PSClient: joined room '%s' (type: %s)",
                room,
                parts[2] if len(parts) > 2 else "?",
            )

        elif msg_type == "users":
            logger.info(
                "PSClient: lobby has %s users",
                parts[2].split(",")[0] if len(parts) > 2 else "?",
            )

    def _update_battle_room(self, room: str) -> None:
        self._battles[room] = BattleState(room_id=room)
        self._active_battle = room
        logger.info("PSClient: joined battle %s", room)

    def _parse_battle_message(self, room: str, msg_type: str, parts: List[str]) -> None:
        """Parse battle-specific messages."""
        # Get or create battle state
        if room not in self._battles:
            logging.error("PSClient: battle not found: %s", room)
            return

        state = self._battles[room]

        # Store raw log
        line = "|".join(parts)
        state.raw_log.append(line)

        if msg_type == "player":
            # |player|p1|username|avatar|rating
            if len(parts) >= 4:
                player_id = parts[2]
                player_name = parts[3]
                if player_name.lower() == self.username.lower():
                    state.my_player_id = player_id
                    logger.info("PSClient: I am %s in this battle", player_id)

        elif msg_type == "request":
            # |request|{json}
            if len(parts) >= 3 and parts[2]:
                try:
                    request = json.loads(parts[2])
                    state.request = request
                    logger.debug("PSClient: received request")
                    if self._on_request:
                        self._on_request(request)
                except json.JSONDecodeError:
                    pass

        elif msg_type == "turn":
            if len(parts) >= 3:
                state.turn = int(parts[2])
                logger.debug("PSClient: turn %d", state.turn)

        elif msg_type == "win":
            if len(parts) >= 3:
                logger.info("Win message: %s", "|".join(parts))
                state.winner = parts[2]
                logger.info("PSClient: battle won by %s", state.winner)
                if self._on_battle_end:
                    self._on_battle_end(state.winner)

        elif msg_type == "tie":
            state.winner = "tie"
            logger.info("PSClient: battle ended in tie")
            if self._on_battle_end:
                self._on_battle_end("tie")

        elif msg_type == "start":
            state.battle_started = True
            logger.info("PSClient: battle started")
            if self._on_battle_start:
                self._on_battle_start(room)

        elif msg_type == "teampreview":
            state.team_preview = True

        elif msg_type == "error":
            error_text = "|".join(parts[2:]) if len(parts) > 2 else "unknown error"
            state.last_error = error_text
            logger.warning("PSClient: choice rejected in %s: %s", room, error_text)

    def login_guest(self) -> bool:
        """Login as a guest (no authentication required)."""
        if not self._challstr:
            logger.warning("PSClient: no challstr, cannot login")
            return False

        # For guest login, just send a name command
        self._send(f"|/trn {self.username},0,")

        # Wait for login confirmation
        timeout = 5.0
        start = time.time()
        while not self._logged_in and time.time() - start < timeout:
            time.sleep(0.1)

        # Join the lobby so we're visible to other users
        self._send("|/join lobby")
        time.sleep(0.5)

        return self._logged_in

    def login_local(self, username: str) -> bool:
        """Login with a username on a local server (no password required).

        Local Pokemon Showdown servers don't validate passwords, so we can
        use any username. This is useful for testing.

        Args:
            username: The username to login as.

        Returns:
            True if login was successful.
        """
        if not self._challstr:
            logger.warning("PSClient: no challstr, cannot login")
            return False

        self.username = username

        # For local server, use /trn command without assertion
        # This only works for unregistered usernames
        logger.info("PSClient: attempting to change name to %s", username)
        self._send(f"|/trn {username},0,")

        # Wait for the username to actually change (compare lowercased)
        timeout = 5.0
        start = time.time()
        target_name = username.lower().replace(" ", "")
        while time.time() - start < timeout:
            if self._current_username:
                current = self._current_username.lower().replace(" ", "")
                if current == target_name:
                    break
            time.sleep(0.1)

        # Join the lobby so we're visible to other users
        self._send("|/join lobby")
        time.sleep(0.5)

        success = (
            self._current_username
            and self._current_username.lower().replace(" ", "") == target_name
        )
        logger.info(
            "PSClient: logged in as %s on local server (success=%s)",
            self._current_username,
            success,
        )
        return success

    def is_user_online(self, username: str, timeout: float = 5.0) -> bool:
        """Check whether a user is currently online on Pokemon Showdown.

        Sends a /query userdetails request and waits for the server response.
        Returns True if the user is online, False if offline or unknown.
        """
        userid = username.lower().replace(" ", "")
        # Clear any stale response for this query type
        self._query_responses.pop("userdetails", None)

        self._send(f"|/query userdetails {userid}")

        start = time.time()
        while time.time() - start < timeout:
            if "userdetails" in self._query_responses:
                data = self._query_responses["userdetails"]
                if data is None:
                    logger.info("PSClient: %s is offline (not found)", username)
                    return False
                online_name = data.get("name", "") or data.get("id", "")
                logger.info("PSClient: %s is online (name=%s)", username, online_name)
                return True
            time.sleep(0.1)

        logger.warning("PSClient: userdetails query for %s timed out", username)
        return False

    def search_ladder(self, format_id: str, team: Optional[str] = None) -> bool:
        """Enter the matchmaking queue for a ladder format.

        Args:
            format_id: Battle format (e.g., "gen9randombattle", "gen3ou").
            team: Packed team string (required for non-random formats).

        Returns:
            True if the search command was sent successfully.
        """
        if not self._connected:
            logger.error("PSClient: not connected")
            return False
        if team:
            logger.info("Uploading team for %s", format_id)
            self._send(f"|/utm {team}")
        else:
            logger.info("Clearing team slot for %s", format_id)
            self._send("|/utm null")
        logger.info(f"Sending search command: |/search {format_id}")
        self._send(f"|/search {format_id}")
        logger.info("PSClient: entered ladder queue for %s", format_id)
        return True

    def cancel_search(self) -> None:
        """Cancel an active ladder search."""
        self._send("|/cancelsearch")
        self._searching = False
        logger.info("PSClient: cancelled ladder search")

    def reset_battle(self) -> None:
        """Clear battle state between games (use before searching for the next game)."""
        self._battles.clear()
        self._active_battle = None

    def join_room(self, room: str) -> None:
        """Join a chat room."""
        self._send(f"|/join {room}")
        logger.info("PSClient: joining room %s", room)

    def login(self, username: str, password: str = "") -> bool:
        """Login with a username (registered or unregistered).

        For registered accounts, provide the password.
        For unregistered usernames, leave password empty.

        This authenticates with the Pokemon Showdown login server to get
        an assertion that proves ownership of the username.
        """
        import urllib.request
        import urllib.parse

        if not self._challstr:
            logger.warning("PSClient: no challstr, cannot login")
            return False

        self.username = username

        # Headers required by PS login server
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://play.pokemonshowdown.com",
            "Referer": "https://play.pokemonshowdown.com/",
        }

        try:
            assertion = None

            if password:
                # Registered account - use /api/login
                login_url = "https://play.pokemonshowdown.com/api/login"
                data = urllib.parse.urlencode(
                    {"name": username, "pass": password, "challstr": self._challstr}
                ).encode()

                req = urllib.request.Request(
                    login_url, data=data, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = resp.read().decode()
                    if result.startswith("]"):
                        result = result[1:]
                    auth_data = json.loads(result)

                    if auth_data.get("actionsuccess"):
                        assertion = auth_data.get("assertion")
                    else:
                        logger.error("PSClient: login failed: %s", auth_data)
                        return False
            else:
                # Unregistered name - use /api/getassertion
                login_url = "https://play.pokemonshowdown.com/api/getassertion"
                data = urllib.parse.urlencode(
                    {
                        "userid": username.lower().replace(" ", ""),
                        "challstr": self._challstr,
                    }
                ).encode()

                req = urllib.request.Request(
                    login_url, data=data, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = resp.read().decode().strip()

                    # Check if the name is registered (starts with ";")
                    if result.startswith(";"):
                        logger.error(
                            "PSClient: '%s' is a registered name, password required",
                            username,
                        )
                        return False

                    assertion = result

            if assertion:
                logger.info("PSClient: got assertion, sending /trn command")
                self._send(f"|/trn {username},0,{assertion}")

                # Wait for username to change
                timeout = 5.0
                start = time.time()
                target_name = username.lower().replace(" ", "")
                while time.time() - start < timeout:
                    if self._current_username:
                        current = self._current_username.lower().replace(" ", "")
                        if current == target_name:
                            break
                    time.sleep(0.1)

                # Join the lobby
                self._send("|/join lobby")
                time.sleep(0.5)

                success = (
                    self._current_username
                    and self._current_username.lower().replace(" ", "") == target_name
                )
                logger.info(
                    "PSClient: logged in as %s (success=%s)",
                    self._current_username,
                    success,
                )
                return success

            return False

        except Exception as e:
            logger.error("PSClient: login error: %s", e)
            return False

    def challenge(
        self,
        target_user: str,
        format_id: str = "gen9randombattle",
        team: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Challenge another user to a battle.

        Args:
            target_user: Username to challenge.
            format_id: Battle format (e.g., "gen9randombattle", "gen9ou").
            team: Packed team string (required for non-random formats).

        Returns:
            Tuple of (success, rejection_message). If success is True, rejection_message is None.
            If the team was rejected, success is False and rejection_message contains the reason.
        """
        if not self._connected:
            logger.error("PSClient: not connected")
            return False, "Not connected"

        logger.info("PSClient: challenging %s to %s", target_user, format_id)

        # Clear any previous popup
        self._last_popup = None

        if team:
            self._send(f"|/utm {team}")
        else:
            self._send("|/utm null")

        self._send(f"|/challenge {target_user}, {format_id}")

        # Wait a moment to see if we get a rejection popup
        time.sleep(1.0)

        if self._last_popup and "reject" in self._last_popup.lower():
            logger.warning("PSClient: team rejected: %s", self._last_popup)
            return False, self._last_popup

        return True, None

    def accept_challenge(self, from_user: str, team: Optional[str] = None) -> bool:
        """Accept a challenge from another user."""
        if team:
            self._send(f"|/utm {team}")

        self._send(f"|/accept {from_user}")
        return True

    def cancel_challenge(self) -> None:
        """Cancel outgoing challenge."""
        self._send("|/cancelchallenge")

    def send_timer_on(self) -> None:
        """Enable the battle timer in the active battle room."""
        if not self._active_battle:
            logger.warning("PSClient: no active battle, cannot enable timer")
            return
        self._send_to_room(self._active_battle, "/timer on")
        logger.info("PSClient: timer enabled in %s", self._active_battle)

    def get_and_clear_battle_error(self) -> Optional[str]:
        """Return and clear any pending choice-rejection error for the active battle."""
        if not self._active_battle:
            return None
        state = self._battles.get(self._active_battle)
        if state and state.last_error:
            err = state.last_error
            state.last_error = None
            return err
        return None

    def clear_pending_request(self) -> None:
        """Discard any pending re-sent request (used after a rejected choice to prevent double-processing)."""
        if self._active_battle and self._active_battle in self._battles:
            self._battles[self._active_battle].request = None

    def choose(self, choice: str) -> None:
        """Make a choice in the active battle.

        Args:
            choice: The choice string, e.g., "move 1", "switch 2", "team 123456".
        """
        if not self._active_battle:
            logger.warning("PSClient: no active battle")
            return

        self._send_to_room(self._active_battle, f"/choose {choice}")
        logger.debug("PSClient: chose %s", choice)

    def forfeit(self) -> None:
        """Forfeit the active battle."""
        if self._active_battle:
            self._send_to_room(self._active_battle, "/forfeit")

    def send_chat(self, message: str) -> None:
        """Send a chat message in the active battle."""
        if self._active_battle:
            self._send_to_room(self._active_battle, message)

    def wait_for_battle(self, timeout: float = 60.0) -> Optional[str]:
        """Wait for a battle to start.

        Returns:
            The battle room ID, or None if timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._active_battle:
                state = self._battles.get(self._active_battle)
                if state and state.battle_started:
                    return self._active_battle
            time.sleep(0.5)
        return None

    def wait_for_request(self, timeout: float = 30.0) -> Optional[Dict]:
        """Wait for a battle request (action needed).

        Returns:
            The request dict, or None if timeout.
        """
        if not self._active_battle:
            return None

        state = self._battles.get(self._active_battle)
        if not state:
            return None

        start = time.time()
        while time.time() - start < timeout:
            if not self._connected:
                logger.warning("PSClient: connection lost while waiting for request")
                return None
            if state.request:
                req = state.request
                state.request = None  # Clear after reading
                return req
            time.sleep(0.1)
        return None

    def is_battle_over(self) -> bool:
        """Check if the active battle has ended."""
        if not self._active_battle:
            return True
        state = self._battles.get(self._active_battle)
        return state is not None and state.winner is not None

    def get_winner(self) -> Optional[str]:
        """Get the winner of the active battle."""
        if not self._active_battle:
            return None
        state = self._battles.get(self._active_battle)
        return state.winner if state else None

    def get_battle_log(self) -> List[str]:
        """Get the raw battle log."""
        if not self._active_battle:
            return []
        state = self._battles.get(self._active_battle)
        return state.raw_log.copy() if state else []

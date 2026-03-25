"""
main.py — Demo entry point.  Wires up GameRunner with different agent
configurations and runs a game against the PS server.

Usage:
    python main.py RANDOM      # RandomAgent vs RandomAgent  (default)
    python main.py PLAYER      # You (PlayerAgent) vs RandomAgent
    python main.py MODEL       # ModelAgent vs RandomAgent  (requires example_model_server.py running)
    python main.py REPLAY      # ReplayAgent vs RandomAgent (replays a dummy sequence)
"""

import logging
import signal
import sys
from typing import List

from agents import Agent, PlayerAgent, ModelAgent, RandomAgent, ReplayAgent
from ps_types import Action
from ps_server import PSServer
from game_runner import GameRunner
from data_collector import DataCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# Global reference for cleanup
ps_server = None


# ---------------------------------------------------------------------------
# Signal handler for graceful shutdown
# ---------------------------------------------------------------------------
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global ps_server
    logger.info("Received signal %d, shutting down...", signum)
    if ps_server:
        ps_server.disconnect()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Agent factories for each demo mode
# ---------------------------------------------------------------------------
MODEL_SOCKET_PATH = "/tmp/ps_model_agent.sock"


def make_random_agent(player_number: int, username: str) -> Agent:
    return RandomAgent(player_number, username, seed=42)


def make_player_agent(player_number: int, username: str) -> Agent:
    return PlayerAgent(player_number, username)


def make_model_agent(player_number: int, username: str) -> Agent:
    return ModelAgent(player_number, username, model_socket_path=MODEL_SOCKET_PATH)


def make_replay_agent(player_number: int, username: str) -> Agent:
    """Create a ReplayAgent with a dummy pre-recorded sequence."""
    dummy_sequence: List[Action] = [
        Action("move 1", "move", 1, "Thunderbolt"),
        Action("move 2", "move", 2, "Quick Attack"),
        Action("move 1", "move", 1, "Thunderbolt"),
        Action("switch 2", "switch", 2, "Switch to Charizard"),
        Action("move 1", "move", 1, "Thunderbolt"),
        Action("move 2", "move", 2, "Quick Attack"),
        Action("move 1", "move", 1, "Thunderbolt"),
        Action(
            "move 1", "move", 1, "Thunderbolt"
        ),  # extra — in case the game runs long
    ]
    return ReplayAgent(player_number, username, game_sequence=dummy_sequence)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global ps_server

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    mode = sys.argv[1].upper() if len(sys.argv) > 1 else "RANDOM"

    # --- pick agent factories based on mode --------------------------------
    if mode == "RANDOM":
        agent1_factory = make_random_agent
        agent2_factory = make_random_agent
        logger.info("Mode: RANDOM vs RANDOM")

    elif mode == "PLAYER":
        agent1_factory = make_player_agent
        agent2_factory = make_random_agent
        logger.info("Mode: PLAYER (you) vs RANDOM")

    elif mode == "MODEL":
        agent1_factory = make_model_agent
        agent2_factory = make_random_agent
        logger.info("Mode: MODEL (via UDS) vs RANDOM")
        logger.info("  → Make sure example_model_server.py is running first!")

    elif mode == "REPLAY":
        agent1_factory = make_replay_agent
        agent2_factory = make_random_agent
        logger.info("Mode: REPLAY vs RANDOM")

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python main.py [RANDOM | PLAYER | MODEL | REPLAY]")
        sys.exit(1)

    # --- set up components -------------------------------------------------
    ps_server = PSServer(
        server_url="http://localhost:8000",
        ps_path="/mnt/c/Users/tmela/development/pokemans/pokemon-showdown",
        format_id="gen9ou",
    )

    try:
        ps_server.connect()

        collector = DataCollector(output_dir="recorded_games")

        runner = GameRunner(
            ps_server=ps_server,
            agent1_factory=agent1_factory,
            agent2_factory=agent2_factory,
            data_collector=collector,
            username1="Alice",
            username2="Bob",
            format="gen9ou",
        )

        # --- run ---------------------------------------------------------------
        winner = runner.run()
        if winner:
            print(f"\n  Winner: Player {winner}")
        else:
            print("\n  No winner (draw or error)")

        # Save the battle log
        battle_log = ps_server.get_battle_log()
        if battle_log:
            import os

            os.makedirs("recorded_games", exist_ok=True)
            log_path = "recorded_games/last_battle.log"
            with open(log_path, "w") as f:
                f.write("\n".join(battle_log))
            logger.info("Battle log saved to %s", log_path)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Error during battle: %s", e)
    finally:
        ps_server.disconnect()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()

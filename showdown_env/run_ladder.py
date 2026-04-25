#!/usr/bin/env python3
"""
Run a Pokemon Showdown ladder bot.

Usage:
    python run_ladder.py --bot-name MyBot --password mypass [options]

Example:
    python run_ladder.py --bot-name MyBot --password mypass --format gen9randombattle --num-games 5
"""

import argparse
import logging

from game_runner import LadderGameRunner
from model_agent import OllamaModelAgent, BERTModelAgent
from agents import RandomAgent
from teams import TeamBuilder
from stats_logger import StatsLogger


def main():
    parser = argparse.ArgumentParser(description="Run a Pokemon Showdown ladder bot")
    parser.add_argument(
        "--bot-name",
        type=str,
        required=True,
        help="Registered PS username for the bot",
    )
    parser.add_argument(
        "--password",
        type=str,
        required=True,
        help="Password for the bot's PS account (required for ladder)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="gen9randombattle",
        help="Battle format (default: gen9randombattle)",
    )
    parser.add_argument(
        "--num-games",
        type=int,
        default=1,
        help="Number of ladder games to play (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per game in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--server",
        type=str,
        default="wss://sim3.psim.us/showdown/websocket",
        help="Pokemon Showdown server URL (default: main server)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="llama3.2:latest",
        help="Ollama model to use (default: llama3.2:latest)",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        default="http://localhost:11434",
        help="Ollama API URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--log-path",
        type=str,
        default=None,
        help="Path to write LLM interaction log as .jsonl (default: disabled)",
    )
    parser.add_argument(
        "--team-index",
        type=int,
        default=None,
        help="0-based index of built-in team to use (skips saved team files)",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    print("=" * 60)
    print(f"Pokemon Showdown Ladder Bot — {args.bot_name}")
    print(f"Format: {args.format}")
    print(f"Games:  {args.num_games}")
    print(f"Model:  {args.model}")
    print("=" * 60)

    # Create team builder only for non-random formats
    team_builder = (
        None
        if "random" in args.format.lower()
        else TeamBuilder(team_index=args.team_index, name=args.bot_name)
    )

    def agent_factory(player_number, username):
        if args.model == "bert":
            return BERTModelAgent(
                player_number=player_number,
                username=username,
                battle_format=args.format,
                log_path=args.log_path,
            )
        elif args.model == "random":
            return RandomAgent(
                player_number=player_number,
                username=username,
            )
        return OllamaModelAgent(
            player_number=player_number,
            username=username,
            battle_format=args.format,
            model_name=args.model,
            ollama_url=args.ollama_url,
            log_path=args.log_path,
        )

    runner = LadderGameRunner(
        agent_factory=agent_factory,
        agent_username=args.bot_name,
        format=args.format,
        server_url=args.server,
        password=args.password,
        team_builder=team_builder,
        stats_logger=StatsLogger(agent_username=args.bot_name),
    )

    print("\nConnecting...")
    if not runner.connect():
        print("FAILED to connect or login.")
        return 1

    print(f"Logged in as {args.bot_name}\n")

    results = runner.run(num_games=args.num_games, timeout=args.timeout)
    runner.disconnect()

    wins = sum(1 for r in results if r and r.lower() == args.bot_name.lower())
    losses = sum(1 for r in results if r and r.lower() != args.bot_name.lower())
    print(f"\nFinal record: {wins}W / {losses}L ({len(results)} games)")

    return 0


if __name__ == "__main__":
    exit(main())

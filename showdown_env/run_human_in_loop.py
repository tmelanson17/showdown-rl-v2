#!/usr/bin/env python3
"""
Run a Pokemon Showdown battle with an LLM agent against a human player.

Usage:
    python run_human_in_loop.py [--human NAME] [--format FORMAT] [--timeout SECONDS]

Example:
    python run_human_in_loop.py --human ctjn17 --format gen3ou --timeout 1800
"""

import argparse
import logging
import random
import string

from game_runner import HumanGameRunner
from model_agent import OllamaModelAgent, BERTModelAgent


def main():
    parser = argparse.ArgumentParser(
        description="Run a Pokemon Showdown battle with LLM agent vs human"
    )
    parser.add_argument(
        "--human",
        type=str,
        default="ctjn17",
        help="Username of the human player (default: ctjn17)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="gen3ou",
        help="Battle format (default: gen3ou)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--server",
        type=str,
        default="ws://localhost:8000/showdown/websocket",
        help="Pokemon Showdown server URL",
    )
    parser.add_argument(
        "--bot-name",
        type=str,
        default=None,
        help="Bot username (default: auto-generated LLMBotXXXXXX)",
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
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Password for the bot (default: None)",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Generate bot name if not provided
    bot_name = args.bot_name
    if bot_name is None:
        bot_name = "LLMBot" + "".join(random.choices(string.digits, k=6))

    print("=" * 60)
    print(f"Pokemon Showdown - {bot_name} (LLM) vs Human")
    print(f"Format: {args.format}")
    print(f"Model: {args.model}")
    print("=" * 60)

    # Create agent factory
    def agent_factory(player_number, username):
        if args.model == "bert":
            return BERTModelAgent(
                player_number=player_number,
                username=username,
                battle_format=args.format,
                ollama_url="http://tj-training.tail38a3b.ts.net:11435",
                log_path=args.log_path,
                team_index=args.team_index,
            )
        return OllamaModelAgent(
            player_number=player_number,
            username=username,
            battle_format=args.format,
            model_name=args.model,
            ollama_url=args.ollama_url,
            log_path=args.log_path,
        )

    # Create and run the game runner
    runner = HumanGameRunner(
        agent_factory=agent_factory,
        agent_username=bot_name,
        human_username=args.human,
        format=args.format,
        server_url=args.server,
        password=args.password,
    )

    print("\nConnecting...")
    if runner.connect():
        print(f"SUCCESS! Logged in as {bot_name}")
        print(
            f"\n>>> Go to {args.server.replace('wss://', 'https://').replace('/websocket', '')} and log in as {args.human} to accept the challenge.\n"
        )
        print(f">>> Log in as {args.human}")
        print(f">>> Accept the challenge from {bot_name}!")
        print(f"\nWaiting for battle ({args.timeout // 60} min timeout)...\n")

        winner = runner.run(timeout=args.timeout)

        print(f"\nFinal result: {winner}")
        runner.disconnect()
    else:
        print("FAILED to connect or login")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

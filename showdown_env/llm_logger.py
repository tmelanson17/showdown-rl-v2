import json
import os
from datetime import datetime, timezone


class LLMLogger:
    """Appends one JSON record per LLM decision to a .jsonl file."""

    def __init__(self, log_path: str = "llm_logs/llm_log.jsonl") -> None:
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def log(
        self,
        *,
        turn: int,
        player_number: int,
        username: str,
        model: str,
        battle_format: str,
        prompt: str,
        response: str,
        available_actions: list[dict],
        chosen_action: dict,
        parse_success: bool,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "player_number": player_number,
            "username": username,
            "model": model,
            "format": battle_format,
            "prompt": prompt,
            "response": response,
            "available_actions": available_actions,
            "chosen_action": chosen_action,
            "parse_success": parse_success,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

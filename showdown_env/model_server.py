"""
model_server.py — Ollama-compatible HTTP server that runs a local model.

Exposes the same API surface as Ollama so that LLMModelAgent works without
any changes — just point it at this server instead of a real Ollama instance:

    # Default (real Ollama):
    agent = LLMModelAgent(..., ollama_url="http://localhost:11434")

    # This server:
    agent = LLMModelAgent(..., ollama_url="http://localhost:11435")

Run with:
    python model_server.py           # listens on port 11435 by default
    python model_server.py 8080      # custom port

Replace ``model_policy`` with your trained model's forward pass.
"""

import json
import logging
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

logging.basicConfig(
    level=logging.INFO,
    format="[MODEL SERVER] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = 11435
MODEL_NAME = "local-model"


# ---------------------------------------------------------------------------
# Policy — replace with your trained model
# ---------------------------------------------------------------------------

def model_policy(prompt: str, available_actions: List[str]) -> str:
    """Choose an action given the prompt and list of available action labels.

    Replace the body of this function with your model's forward pass.
    ``available_actions`` is already parsed for you so you don't have to
    re-parse the prompt text.

    Args:
        prompt: The full prompt string sent by LLMModelAgent.
        available_actions: Action labels extracted from the prompt, e.g.
            ["Flamethrower", "Dragon Claw", "Earthquake", "Blastoise"]

    Returns:
        The chosen action label (must be one of ``available_actions``).
    """
    # TODO: replace with model.predict(prompt) or model.predict(available_actions)
    if not available_actions:
        return ""
    return available_actions[0]


# ---------------------------------------------------------------------------
# Prompt parser
# ---------------------------------------------------------------------------

def parse_available_actions(prompt: str) -> List[str]:
    """Extract action labels from the LLMModelAgent prompt format.

    Looks for the "Available actions:" section and parses lines like:
        MOVE: Flamethrower
        SWITCH: Blastoise
    """
    actions: List[str] = []
    in_actions = False

    for line in prompt.splitlines():
        if line.strip() == "Available actions:":
            in_actions = True
            continue
        if in_actions:
            m = re.match(r"\s+\w+:\s+(.+)", line)
            if m:
                actions.append(m.group(1).strip())
            elif line.strip() and not line.startswith(" "):
                break  # left the actions block

    return actions


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class OllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt, *args)

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    # GET /api/tags — lets LLMModelAgent check that we're alive
    def do_GET(self):
        if self.path == "/api/tags":
            self._send_json(200, {
                "models": [{"name": MODEL_NAME, "model": MODEL_NAME}]
            })
        else:
            self._send_json(404, {"error": "not found"})

    # POST /api/generate — main inference endpoint
    def do_POST(self):
        if self.path != "/api/generate":
            self._send_json(404, {"error": "not found"})
            return

        try:
            payload = self._read_json_body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return

        prompt = payload.get("prompt", "")
        available_actions = parse_available_actions(prompt)

        logger.info(
            "Request — model=%s, actions=%s",
            payload.get("model", "?"),
            available_actions,
        )

        try:
            chosen = model_policy(prompt, available_actions)
        except Exception as exc:
            logger.exception("model_policy raised")
            self._send_json(500, {"error": str(exc)})
            return

        logger.info("Chose: %s", chosen)
        self._send_json(200, {
            "model": MODEL_NAME,
            "response": chosen,
            "done": True,
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = HTTPServer(("0.0.0.0", port), OllamaHandler)
    logger.info("Listening on http://localhost:%d", port)
    logger.info("Point LLMModelAgent at: ollama_url='http://localhost:%d'", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()

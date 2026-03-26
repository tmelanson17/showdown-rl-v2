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
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[MODEL SERVER] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = 11435
MODEL_NAME = "local-model"
MODEL_DIR = os.path.expanduser("~/pokemans/final_model")
MAX_QUESTION_CHARS = 1800


# ---------------------------------------------------------------------------
# Model loading and inference
# ---------------------------------------------------------------------------

def _load_model(model_dir: str):
    from transformers import AutoTokenizer, AutoModelForQuestionAnswering
    import torch
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForQuestionAnswering.from_pretrained(model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    logger.info("Model loaded from %s on %s", model_dir, device)
    return tokenizer, model, device


def _predict(tokenizer, model, device, question: str, context: str) -> str:
    """Score each complete context line as start_logit[first_tok] + end_logit[last_tok].

    This constrains extraction to line-aligned spans, preventing partial matches
    like bare "move" or "switch" instead of "move Dragon Dance" or "switch Skarmory".
    """
    import torch
    context_lines = [l.strip() for l in context.strip().split('\n') if l.strip()]
    if not context_lines:
        return ""

    encoding = tokenizer(
        question,
        context,
        max_length=512,
        truncation="only_first",
        return_tensors="pt",
        padding=True,
        return_offsets_mapping=True,
    )

    offset_mapping = encoding.pop("offset_mapping")[0].tolist()
    sequence_ids = encoding.sequence_ids(0)
    model_inputs = {k: v.to(device) for k, v in encoding.items()}

    with torch.no_grad():
        outputs = model(**model_inputs)

    start_logits = outputs.start_logits[0]
    end_logits = outputs.end_logits[0]

    best_score = float('-inf')
    best_line = context_lines[0]

    char_pos = 0
    for line in context_lines:
        line_start = context.index(line, char_pos)
        line_end = line_start + len(line)
        char_pos = line_end

        tok_start = None
        tok_end = None
        for i, (c_start, c_end) in enumerate(offset_mapping):
            if sequence_ids[i] != 1:
                continue
            if tok_start is None and c_start >= line_start and c_start < line_end:
                tok_start = i
            if c_start >= line_start and c_end <= line_end:
                tok_end = i

        if tok_start is None or tok_end is None or tok_end < tok_start:
            continue

        score = start_logits[tok_start].item() + end_logits[tok_end].item()
        if score > best_score:
            best_score = score
            best_line = line

    return best_line


try:
    _tokenizer, _model, _device = _load_model(MODEL_DIR)
except Exception as exc:
    logger.warning("Could not load model from %s: %s — falling back to first action", MODEL_DIR, exc)
    _tokenizer = _model = _device = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def _parse_typed_actions(prompt: str) -> List[Tuple[str, str]]:
    """Return [(action_type, label), ...] from the Available actions block."""
    typed: List[Tuple[str, str]] = []
    in_actions = False
    for line in prompt.splitlines():
        if line.strip() == "Available actions:":
            in_actions = True
            continue
        if in_actions:
            m = re.match(r"\s+(MOVE|SWITCH):\s+(.+)", line)
            if m:
                typed.append((m.group(1).lower(), m.group(2).strip()))
            elif line.strip() and not line.startswith(" "):
                break
    return typed


def model_policy(prompt: str, available_actions: List[str]) -> str:
    if not available_actions:
        return ""
    if _model is None:
        return available_actions[0]
    typed = _parse_typed_actions(prompt)
    context = "\n".join(f"{t} {l}" for t, l in typed) if typed else "\n".join(available_actions)
    parts = prompt.split("\nAvailable actions:", 1)
    question = parts[0][-MAX_QUESTION_CHARS:]
    return _predict(_tokenizer, _model, _device, question, context)


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

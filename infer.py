"""
infer.py — Run inference with the trained Pokemon Showdown QA model.

Reads a JSON object from stdin with "context" and "question" keys and
prints the predicted action (e.g. "move Body Slam" or "switch Skarmory").

Usage:
    echo '{"context": "move Body Slam\n...", "question": "...battle log..."}' | python3 infer.py
    python3 infer.py < input.json

Input JSON fields:
    context  — newline-separated available actions ("move X" / "switch Y")
    question — battle log ending with "Please choose your next move."
"""

import json
import sys

from transformers import AutoTokenizer, AutoModelForQuestionAnswering
import torch

MODEL_DIR = "./final_model"
MAX_QUESTION_CHARS = 1800


def load_model(model_dir: str):
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForQuestionAnswering.from_pretrained(model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    return tokenizer, model, device


def predict(tokenizer, model, device, question: str, context: str) -> str:
    question = question[-MAX_QUESTION_CHARS:]
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

    # Score each complete context line as start_logit[first_tok] + end_logit[last_tok].
    # This constrains extraction to line-aligned spans, preventing partial matches like
    # bare "move" or "switch" instead of "move Dragon Dance" or "switch Skarmory".
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


def main():
    data = json.load(sys.stdin)
    context = data["context"]
    question = data["question"]

    tokenizer, model, device = load_model(MODEL_DIR)
    answer = predict(tokenizer, model, device, question, context)
    print(answer)


if __name__ == "__main__":
    main()

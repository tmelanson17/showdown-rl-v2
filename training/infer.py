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

    inputs = tokenizer(
        question,
        context,
        max_length=512,
        truncation="only_first",
        return_tensors="pt",
        padding=True,
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)

    start = outputs.start_logits.argmax().item()
    end = outputs.end_logits.argmax().item()

    # Clamp end so it's never before start
    if end < start:
        end = start

    tokens = inputs["input_ids"][0][start : end + 1]
    answer = tokenizer.decode(tokens, skip_special_tokens=True).strip()
    return answer


def main():
    data = json.load(sys.stdin)
    context = data["context"]
    question = data["question"]

    tokenizer, model, device = load_model(MODEL_DIR)
    answer = predict(tokenizer, model, device, question, context)
    print(answer)


if __name__ == "__main__":
    main()

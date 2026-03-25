from transformers import AutoTokenizer
from datasets import Dataset
import json


def load_for_causal_lm(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)

    texts = []
    for article in data["data"]:
        for paragraph in article["paragraphs"]:
            context = paragraph["context"]
            for qa in paragraph["qas"]:
                # Format as instruction-following
                prompt = f"""Game State:
{qa["question"]}

Available Actions:
{context}

Chosen Action: {qa["answers"][0]["text"]}"""
                texts.append({"text": prompt})
    return texts


# Load and tokenize
examples = load_for_causal_lm("training/example_squad_output.json")
dataset = Dataset.from_list(examples)

tokenizer = AutoTokenizer.from_pretrained("gpt2")
tokenizer.pad_token = tokenizer.eos_token

tokenized = dataset.map(
    lambda x: tokenizer(
        x["text"], truncation=True, max_length=1024, padding="max_length"
    ),
    batched=True,
)

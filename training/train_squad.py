from transformers import (
    AutoTokenizer,
    AutoModelForQuestionAnswering,
    TrainingArguments,
    Trainer,
    DefaultDataCollator,
)
from datasets import Dataset
import json

MODEL_NAME = "deepset/roberta-base-squad2"
DATA_FILE = "squad_pokemon_dataset.json"
# Keep the tail of the battle log — most recent game state is what matters
MAX_QUESTION_CHARS = 1800

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

def flatten_squad(path):
    with open(path) as f:
        squad = json.load(f)
    examples = []
    for article in squad["data"]:
        for para in article["paragraphs"]:
            context = para["context"]
            for qa in para["qas"]:
                answer = qa["answers"][0]
                # Trim from the front — drop older turns, keep current state
                question = qa["question"][-MAX_QUESTION_CHARS:]
                examples.append({
                    "id": qa["id"],
                    "context": context,
                    "question": question,
                    "answers": {
                        "text": [answer["text"]],
                        "answer_start": [answer["answer_start"]],
                    },
                })
    return examples

dataset = Dataset.from_list(flatten_squad(DATA_FILE))
print(f"Total examples: {len(dataset)}")
split = dataset.train_test_split(test_size=0.1, seed=42)
print(f"Train: {len(split['train'])}, Val: {len(split['test'])}")

def preprocess(examples):
    inputs = tokenizer(
        examples["question"],
        examples["context"],
        max_length=512,
        truncation="only_first",  # truncate battle log if still too long, never the moves context
        return_offsets_mapping=True,
        padding="max_length",
    )
    offset_mapping = inputs.pop("offset_mapping")
    answers = examples["answers"]
    start_positions, end_positions = [], []

    for i, offset in enumerate(offset_mapping):
        answer = answers[i]
        start_char = answer["answer_start"][0]
        end_char = start_char + len(answer["text"][0])
        sequence_ids = inputs.sequence_ids(i)

        # Find the context token range (sequence_id == 1)
        ctx_start = next(j for j, s in enumerate(sequence_ids) if s == 1)
        ctx_end = len(sequence_ids) - 1 - next(
            j for j, s in enumerate(reversed(sequence_ids)) if s == 1
        )

        if offset[ctx_start][0] > end_char or offset[ctx_end][1] < start_char:
            start_positions.append(0)
            end_positions.append(0)
        else:
            idx = ctx_start
            while idx <= ctx_end and offset[idx][0] <= start_char:
                idx += 1
            start_positions.append(idx - 1)
            idx = ctx_end
            while idx >= ctx_start and offset[idx][1] >= end_char:
                idx -= 1
            end_positions.append(idx + 1)

    inputs["start_positions"] = start_positions
    inputs["end_positions"] = end_positions
    return inputs

tokenized = split.map(preprocess, batched=True, remove_columns=split["train"].column_names)

model = AutoModelForQuestionAnswering.from_pretrained(MODEL_NAME)

args = TrainingArguments(
    output_dir="./checkpoints",
    num_train_epochs=3,
    per_device_train_batch_size=16,
    learning_rate=2e-5,
    weight_decay=0.01,
    eval_strategy="epoch",
    save_strategy="epoch",
    save_total_limit=2,
    load_best_model_at_end=True,
    fp16=True,
    report_to="none",
    logging_steps=100,
)

trainer = Trainer(
    model=model,
    args=args,
    train_dataset=tokenized["train"],
    eval_dataset=tokenized["test"],
    data_collator=DefaultDataCollator(),
    processing_class=tokenizer,
)

trainer.train()
trainer.save_model("./final_model")
tokenizer.save_pretrained("./final_model")
print("Done. Model saved to ./final_model")

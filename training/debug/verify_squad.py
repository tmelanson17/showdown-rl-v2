"""Quick verification of the SQuAD dataset generation."""

import json
import argparse


def verify_squad_dataset(filepath: str):
    with open(filepath) as f:
        d = json.load(f)

    entries = d["data"][0]["paragraphs"]
    print(f"Total entries: {len(entries)}")

    # Check that all answers appear in context
    mismatches = []
    for e in entries:
        entry_id = e["qas"][0]["id"]
        context = e["context"]
        answer = e["qas"][0]["answers"][0]["text"]
        if answer not in context:
            mismatches.append((entry_id, answer, context))

    if mismatches:
        print(f"\n!!! Found {len(mismatches)} entries where answer not in context:")
        for entry_id, answer, context in mismatches[:5]:
            print(f"  {entry_id}: answer='{answer}'")
            print(f"    context: {context[:100]}...")
    else:
        print("\nAll answers found in context!")

    # Show some sample entries
    print("\n=== Sample Entries ===")
    for e in entries[:3]:
        entry_id = e["qas"][0]["id"]
        print(f"\n--- {entry_id} ---")
        print(f"Context:\n{e['context']}")
        print(f"Answer: {e['qas'][0]['answers'][0]['text']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify SQuAD dataset")
    parser.add_argument(
        "filepath",
        nargs="?",
        default="../test_squad_output.json",
        help="Path to SQuAD JSON file to verify",
    )
    args = parser.parse_args()
    verify_squad_dataset(args.filepath)

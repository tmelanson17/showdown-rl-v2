"""Test PS simulator using file-based communication for reliability."""

import subprocess
import time
import sys
import os
import tempfile

# Use project directory for files
project_dir = r"c:\Users\tmela\development\pokemans\showdown-rl-v2"
input_file = os.path.join(project_dir, "ps_input.txt")
output_file = os.path.join(project_dir, "ps_output.txt")

# Write initial commands
commands = [
    '>start {"formatid":"gen9randombattle"}',
    '>player p1 {"name":"Alice"}',
    '>player p2 {"name":"Bob"}',
]

with open(input_file, "w", newline="\n") as f:  # Use Unix line endings
    f.write("\n".join(commands) + "\n")

print(f"Input file: {input_file}")
print(f"Commands written.")

# Convert paths to WSL format
wsl_input = "/mnt/c/Users/tmela/development/pokemans/showdown-rl-v2/ps_input.txt"
wsl_output = "/mnt/c/Users/tmela/development/pokemans/showdown-rl-v2/ps_output.txt"

# First verify the input file exists and is readable
check_cmd = f"cat {wsl_input}"
result = subprocess.run(
    ["wsl", "-e", "bash", "-c", check_cmd], capture_output=True, text=True
)
print(f"Input file contents:\n{result.stdout}")

# Run simulator with file input
cmd = f"cd /mnt/c/Users/tmela/development/pokemans/pokemon-showdown && cat {wsl_input} | timeout 5 node pokemon-showdown simulate-battle > {wsl_output} 2>&1"

print(f"\nRunning simulator...")
result = subprocess.run(
    ["wsl", "-e", "bash", "-c", cmd], capture_output=True, text=True, timeout=60
)

print(f"\nExit code: {result.returncode}")
if result.stderr:
    print(f"Stderr: {result.stderr}")

# Read output
if os.path.exists(output_file):
    with open(output_file, "r") as f:
        output = f.read()
    print(f"\n--- Output ({len(output)} bytes) ---")
    print(output[:3000])  # First 3000 chars
else:
    print("No output file created")

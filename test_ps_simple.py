"""Simple test to verify PS simulator communication."""

import subprocess
import time
import sys
import threading
import queue
import os

# Launch the simulator - key is to NOT set bufsize=0, use line buffering
cmd = [
    "wsl",
    "-e",
    "bash",
    "-c",
    "cd /mnt/c/Users/tmela/development/pokemans/pokemon-showdown && stdbuf -oL node pokemon-showdown simulate-battle",
]

print("Starting PS simulator...")
proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

output_queue = queue.Queue()
all_output = []


def reader_thread():
    """Read stdout in a separate thread, line by line."""
    try:
        for line in iter(proc.stdout.readline, ""):
            if line:
                output_queue.put(line)
                all_output.append(line)
    except Exception as e:
        print(f"Reader error: {e}")


# Start reader thread
reader = threading.Thread(target=reader_thread, daemon=True)
reader.start()


def send(msg):
    print(f">>> {msg}")
    proc.stdin.write(msg + "\n")
    proc.stdin.flush()


def read_output(timeout=3.0, until_empty_wait=0.5):
    """Read output until timeout or no more data."""
    lines = []
    start = time.time()
    last_data = start

    while time.time() - start < timeout:
        try:
            line = output_queue.get(timeout=0.1)
            print(f"<<< {line.rstrip()}")
            lines.append(line)
            last_data = time.time()
        except queue.Empty:
            # No data for a bit - if we had data before, might be done
            if lines and time.time() - last_data > until_empty_wait:
                break
            continue

    return lines


try:
    # Start battle
    send('>start {"formatid":"gen9randombattle"}')
    time.sleep(0.1)
    send('>player p1 {"name":"Alice"}')
    time.sleep(0.1)
    send('>player p2 {"name":"Bob"}')

    # Read initial response
    print("\n--- Reading initial response ---")
    read_output(5.0)

    print("\n\n--- Making moves (turn 1) ---")
    send(">p1 move 1")
    time.sleep(0.1)
    send(">p2 move 1")

    print("\n--- Reading after turn 1 ---")
    read_output(3.0)

    print("\n\n--- Making moves (turn 2) ---")
    send(">p1 move 2")
    time.sleep(0.1)
    send(">p2 move 2")

    print("\n--- Reading after turn 2 ---")
    read_output(3.0)

    print("\n\nBattle simulation successful!")

except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except:
        proc.kill()
    print("\nSimulator terminated.")

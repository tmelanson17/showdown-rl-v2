# Showdown RL v2

A Python framework for running Pokémon Showdown battles with reinforcement learning agents.

## Overview

This project provides a modular architecture for:
- Running Pokémon Showdown battles locally
- Training and evaluating RL agents
- Recording battle data for offline analysis
- Supporting multiple agent types (random, human, ML model-based)

## Project Structure

```
showdown-rl-v2/
├── README.md                    # This file
├── showdown_env/
│   ├── main.py                  # Entry point - runs battles with configurable agents
│   ├── ps_server.py             # Pokemon Showdown server wrapper (subprocess communication)
│   ├── ps_types.py              # Data types: GameState, Action, Side, PokemonSlot
│   ├── game_runner.py           # Battle orchestrator - manages game flow
│   ├── agents.py                # Agent hierarchy: Random, Player, Model, Replay
│   ├── teams.py                 # Team management: loading, saving, format conversion
│   ├── ipc.py                   # Unix Domain Socket IPC for external model servers
│   ├── data_collector.py        # Records game data to JSON files
│   └── model_server.py          # Ollama-compatible HTTP server for locally trained models
├── training/
│   └── replay_exporter.py       # Converts recorded games to PS replay format
├── teams/                       # Saved teams directory
│   └── gen9ou/                  # Teams organized by format
└── recorded_games/              # Output directory for battle recordings
```

## Components

### PSServer (`showdown_env/ps_server.py`)

Thin wrapper around a local Pokémon Showdown instance. Communicates with the PS simulator
via subprocess stdin/stdout using the [PS protocol](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md).

Key responsibilities:
- Spawn and manage the PS simulator subprocess
- Send player actions (`>p1 move 1`, `>p2 switch 3`, etc.)
- Parse PS protocol messages into structured `GameState` objects
- Handle team preview, battle loop, and game termination

### GameRunner (`showdown_env/game_runner.py`)

Orchestrates the full battle flow:
1. Generate agents for both players
2. Register players with PS server
3. Handle challenge/accept flow
4. Run team preview phase
5. Execute battle loop (get state → agent decides → submit action)
6. Record game data via DataCollector

### Agents (`showdown_env/agents.py`)

Abstract `Agent` base class with concrete implementations:

| Agent | Description |
|-------|-------------|
| `RandomAgent` | Picks uniformly random legal actions (baseline) |
| `PlayerAgent` | Interactive human player via stdin/stdout |
| `ModelAgent` | Delegates to external ML model via Unix Domain Socket |
| `ReplayAgent` | Replays a pre-recorded action sequence |

### Data Types (`showdown_env/ps_types.py`)

Core data structures:
- `GameState` - Full battle snapshot (turn, sides, available actions)
- `Side` - One player's team state
- `PokemonSlot` - Individual Pokémon status (HP, species, status conditions)
- `Action` - A legal action (move or switch)

### IPC (`showdown_env/ipc.py`)

Unix Domain Socket IPC layer for communicating with external model servers:
- `IPCServer` - JSON-RPC style server for hosting ML models
- `IPCClient` - Blocking client for querying models

### DataCollector (`showdown_env/data_collector.py`)

Records battle data to JSON files:
- Team preview state
- (state, action) pairs for each turn
- Game outcome and metadata

### TeamManager (`showdown_env/teams.py`)

Handles team loading, saving, and format conversion:
- Load teams from PokePaste files
- Convert between packed and export formats via PS CLI
- Provide sample competitive teams for gen9ou
- Save teams to files for reuse

### ReplayExporter (`training/replay_exporter.py`)

Converts recorded game JSON to Pokémon Showdown replay format:
- `.log` file - Protocol messages for PS replay viewer
- `.json` file - Full replay envelope with metadata

## Usage

### Starting the Pokémon Showdown Server

Many scripts (`main.py`, `run_human_in_loop.py`, etc.) require a local Pokémon Showdown
server to be running first. Start it in WSL before running anything else:

```bash
# On Windows host
cd C:\Users\<your-username>\development\pokemans\pokemon-showdown
node pokemon-showdown
```

Leave this terminal open — the server must stay running for the duration of your session.
Connect to it in a browser at `http://localhost:8000` to verify it's up.

### Prerequisites

1. Clone Pokémon Showdown server:
   ```bash
   # In WSL (Linux)
   cd /mnt/c/Users/<your-username>/development/pokemans
   git clone https://github.com/smogon/pokemon-showdown.git
   cd pokemon-showdown
   npm install
   ```

2. Set up Python environment:
   ```bash
   cd showdown-rl-v2
   pip install -r requirements.txt  # if exists, or just ensure Python 3.8+
   ```

### Running Battles

From the project root:

```bash
# Random vs Random battle
python showdown_env/main.py RANDOM

# Human vs Random battle  
python showdown_env/main.py PLAYER

# ML Model vs Random (requires model server running)
python showdown_env/main.py MODEL

# Replay recorded game vs Random
python showdown_env/main.py REPLAY
```

### Using the Model Server

`model_server.py` is an Ollama-compatible HTTP server that runs your locally trained model.
Point `LLMModelAgent` at it instead of a real Ollama instance.

**Running locally:**

```bash
python showdown_env/model_server.py           # default port 11435
python showdown_env/model_server.py 8080      # custom port
```

**Running on a remote machine (e.g. `tj-training`):**

```bash
ssh tj-training.tail38a3b.ts.net \
  "nohup python3 ~/model_server.py > /tmp/model_server.log 2>&1 &"
```

Check the log / verify it's up:
```bash
ssh tj-training.tail38a3b.ts.net "cat /tmp/model_server.log"
ssh tj-training.tail38a3b.ts.net "curl -s http://localhost:11435/api/tags"
```

The server does **not** survive reboots — re-run the `nohup` command after each restart.

**Pointing an agent at the server:**

```python
# Local
agent = LLMModelAgent(..., ollama_url="http://localhost:11435")

# Remote (via SSH tunnel: ssh -L 11435:localhost:11435 tj-training.tail38a3b.ts.net)
agent = LLMModelAgent(..., ollama_url="http://localhost:11435")
```

**Plugging in your model:**

Edit `model_policy()` in `showdown_env/model_server.py`:

```python
def model_policy(prompt: str, available_actions: List[str]) -> str:
    # Replace with your model's forward pass
    return my_model.predict(available_actions)
```

In another terminal, run with MODEL mode:
```bash
python showdown_env/main.py MODEL
```

### Playing Against a Human (LLM Bot vs Human)

`run_human_in_loop.py` connects to a running PS server via WebSocket, logs in as a bot, and challenges a human player. The bot uses an Ollama-hosted LLM to make decisions.

**Prerequisites:**

1. A local Pokemon Showdown server must be running (the human connects to it via browser):
   ```bash
   # In WSL
   cd /mnt/c/Users/<your-username>/development/pokemans/pokemon-showdown
   node pokemon-showdown
   ```

2. Ollama must be running locally with a model pulled:
   ```bash
   ollama pull llama3.2
   ```

**Run the bot:**
```bash
# From the showdown_env/ directory
python run_human_in_loop.py --human <your-ps-username> --format gen3ou
```

Then in a browser, go to `http://localhost:8000`, log in as `<your-ps-username>`, and accept the challenge from the bot.

**Key options:**

| Flag | Default | Description |
|---|---|---|
| `--human` | `ctjn17` | PS username of the human player to challenge |
| `--format` | `gen3ou` | Battle format |
| `--model` | `llama3.2:latest` | Ollama model to use |
| `--bot-name` | auto (`LLMBotXXXXXX`) | Bot's PS username |
| `--server` | `ws://localhost:8000/showdown/websocket` | PS server WebSocket URL |
| `--timeout` | `1800` | Max wait time in seconds (default 30 min) |
| `--ollama-url` | `http://localhost:11434` | Ollama API URL |
| `--debug` | off | Enable verbose logging |

**Example with all options:**
```bash
python run_human_in_loop.py \
  --human ctjn17 \
  --format gen3ou \
  --model llama3.2:latest \
  --bot-name MyBot \
  --timeout 3600
```

### Configuration

Key settings in `main.py`:
- `MODEL_SOCKET_PATH` - Unix socket path for model IPC
- Battle format defaults to `gen9ou` (standard OU tier with custom teams)
- Usernames default to "Alice" and "Bob"

### Team Management

For formats that require teams (like `gen9ou`), the framework handles team selection:

| Format Type | Team Handling |
|-------------|---------------|
| Random Battle | PS generates teams automatically |
| Standard Formats | Agents provide teams via `get_team()` method |

**RandomAgent** includes built-in sample Gen 9 OU teams and can:
- Load teams from saved files in `teams/<format>/`
- Use built-in sample competitive teams
- Save teams to files in human-readable PokePaste format

Teams are stored in the `teams/` directory:
```
teams/
└── gen9ou/
    ├── team_Alice_1234567890.txt
    └── team_Bob_1234567891.txt
```

**Team Formats:**
- **Export (PokePaste)** - Human-readable, used for file storage
- **Packed** - Compact pipe-delimited format, used for PS protocol

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         GameRunner                               │
│  (orchestrates battle flow, coordinates all components)          │
└───────────┬────────────────┬────────────────┬───────────────────┘
            │                │                │
            ▼                ▼                ▼
    ┌───────────────┐ ┌─────────────┐ ┌──────────────────┐
    │   PSServer    │ │   Agents    │ │  DataCollector   │
    │ (PS process)  │ │ (decisions) │ │ (JSON recording) │
    └───────┬───────┘ └──────┬──────┘ └──────────────────┘
            │                │
            ▼                ▼
    ┌───────────────┐ ┌─────────────┐
    │ PS Simulator  │ │ IPCClient   │ ──► External Model
    │  (subprocess) │ │ (optional)  │       Server
    └───────────────┘ └─────────────┘
```

## Battle Protocol Flow

```
GameRunner                PSServer              Agent              DataCollector
    │                        │                    │                      │
    │── register_player(1) ──►                    │                      │
    │── register_player(2) ──►                    │                      │
    │◄──── send_preview ─────│                    │                      │
    │── record_preview ──────────────────────────────────────────────────►
    │                        │                    │                      │
    │ [Battle Loop]          │                    │                      │
    │◄─── get_game_state ────│                    │                      │
    │────────────────────────────► decide() ──────│                      │
    │◄───────────────────────────── action ───────│                      │
    │── record_state_action ─────────────────────────────────────────────►
    │── handle_game_state ───►                    │                      │
    │         ...            │                    │                      │
    │                        │                    │                      │
    │◄─── is_battle_over ────│                    │                      │
    │── end_record ──────────────────────────────────────────────────────►
```

## PS Protocol Reference

The simulator uses a simple text protocol. Key message types:

**Server → Client:**
- `|request|JSON` - Available actions for a player
- `|switch|POKEMON|DETAILS|HP` - Pokémon switched in
- `|move|POKEMON|MOVE|TARGET` - Move used
- `|-damage|POKEMON|HP` - Damage dealt
- `|win|USER` - Battle winner
- `|turn|NUMBER` - New turn

**Client → Server:**
- `>start {"formatid":"gen9randombattle"}` - Start battle
- `>player p1 {"name":"Alice"}` - Set player info
- `>p1 move 1` - Use move in slot 1
- `>p1 switch 2` - Switch to Pokémon in slot 2
- `>p1 team 123456` - Team preview order

## Development

### Adding a New Agent

1. Create a new class inheriting from `Agent` in `agents.py`
2. Implement the `decide(gamestate: GameState) -> Action` method
3. Add a factory function in `main.py`
4. Add the mode to the CLI argument handling

### Extending PS Protocol Support

1. Modify `PSServer._parse_request()` to handle new message types
2. Update `ps_types.py` data structures if needed
3. Ensure `DataCollector` captures relevant data

## License

See LICENSE file in the repository.

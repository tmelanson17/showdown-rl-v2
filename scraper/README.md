# Pokemon Showdown Replay Scraper

Polls [replay.pokemonshowdown.com](https://replay.pokemonshowdown.com) for new public battle replays, filters by rating, downloads the full logs, and parses them into `(game state, action)` pairs for RL training.

> **Note:** This scraper collects *finished replays*, not live ongoing battles.

## Prerequisites

Run from the repo root using the project venv:

```bash
.venv/Scripts/python scraper/web_scraper.py [options]
```

Or from inside the `scraper/` directory with any Python 3.8+ interpreter (the script adds `showdown_env/` to `sys.path` automatically).

## Usage

### One-shot poll (recommended for manual runs / cron)

```bash
.venv/Scripts/python scraper/web_scraper.py --once --min-elo 1500
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | `gen3ou` | PS format ID (e.g. `gen9ou`, `gen3ou`) |
| `--min-elo` | `0` | Minimum rating; `0` disables the filter |
| `--output` | `replays/` | Directory where replay JSONs are saved |
| `--interval` | `30` | Polling interval in minutes (used by scheduled task) |
| `--once` | off | Run a single poll then exit |
| `--test` | off | Run self-tests and exit |

### Self-tests

```bash
.venv/Scripts/python scraper/web_scraper.py --test
```

Fetches a known replay, verifies the turn-record parser, and checks for duplicate IDs in the output directory and state file.

## First-run behaviour

On the very first run (no `ps_poller_state.json` present), the scraper sets its cursor to *now* and exits without downloading anything. The second run picks up all replays uploaded after that cursor. This prevents a large backfill on initial setup.

## Output format

Each downloaded replay is saved as `<id>.json` in the output directory. The file is the raw PS replay envelope plus three added fields:

```json
{
  "id": "gen3ou-2567241884",
  "log": "...",
  "parsed_turns": [ { "turn": 1, "state": {...}, "p1_action": {...}, "p2_action": {...} }, ... ],
  "turn_count": 37,
  "parse_valid": true,
  "parse_errors": []
}
```

## State file

`ps_poller_state.json` (written next to wherever the script is run from) tracks the cursor timestamp and all seen replay IDs. Delete it to reset and start fresh.

## Automated scheduling

See [SCHEDULED_TASK.md](SCHEDULED_TASK.md) for instructions on setting up a Windows Scheduled Task to run the scraper every 15 minutes.

## Known limitations

- Replays with `rating: null` pass through the `--min-elo` filter (the API omits the field for unrated games).
- The continuous polling loop (`--interval` without `--once`) is not yet implemented; use a scheduled task or cron with `--once` instead.
- The parser reconstructs state from HP deltas and move/switch events only — it does not capture full team rosters, held items, or intra-turn event ordering.

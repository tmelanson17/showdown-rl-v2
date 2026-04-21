# Turn State Object — Design Doc

## Overview

This script parses a Pokemon Showdown replay JSON, splits the `log` field on
`|upkeep` to isolate each turn's event block, and builds a `TurnState` snapshot
after every turn.

The snapshot represents **what is knowable at the START of the next turn** — i.e.,
after all end-of-turn effects (Leftovers, Sandstorm, burn damage, etc.) have
resolved and the upkeep line has been emitted.

---

## Log Structure (quick reference)

Relevant event types we parse:

| Line pattern | Meaning |
|---|---|
| `\|switch\|p1a: Zapdos\|Zapdos\|100/100` | Voluntary switch; updates active + HP |
| `\|drag\|p1a: Suicune\|Suicune\|100/100` | Forced switch (Roar, etc.); same treatment |
| `\|move\|p1a: Zapdos\|Thunderbolt\|...` | Move used; increments `times_used` |
| `\|-damage\|p2a: Zapdos\|62/100` | HP update after damage |
| `\|-heal\|p1a: Zapdos\|61/100\|...` | HP update after healing |
| `\|-status\|p1a: X\|par` | Status condition applied |
| `\|-curestatus\|p1a: X\|par` | Status condition cured |
| `\|faint\|p2a: Forretress` | Pokemon faints; HP → 0, `is_fainted = True` |
| `\|turn\|N` | Start of turn N |
| `\|upkeep` | End-of-turn delimiter |

HP values in the log use the "HP Percentage Mod" format (`62/100` = 62%).

---

## State Object Schema

```python
@dataclass
class MoveRecord:
    name: str         # e.g. "Thunderbolt"
    times_used: int   # total observed uses up to this turn snapshot


@dataclass
class PokemonState:
    species: str             # e.g. "Zapdos"  (no nickname, no gender suffix)
    hp_pct: float            # 0.0–1.0; last observed value
    status: str | None       # "par" | "brn" | "tox" | "slp" | "frz" | None
    is_fainted: bool
    is_active: bool
    moves_seen: list[MoveRecord]  # moves observed at any point in the game so far


@dataclass
class SideState:
    player_id: str           # "p1" or "p2"
    username: str
    active: PokemonState | None      # None only at game start before first switch
    bench: list[PokemonState]        # all non-active pokemon (revealed so far)
    # Note: unrevealed bench slots are simply absent — we only track seen pokemon


@dataclass
class TurnState:
    turn_number: int
    p1: SideState
    p2: SideState
```

---

## Key Design Decisions & Tradeoffs

### 1. HP tracking
- HP is only updated when an event for that Pokemon appears in the log.
- **Active** Pokemon: HP updated on every `|-damage|`, `|-heal|`, `|switch|`, or `|drag|` line.
- **Bench** Pokemon: HP is the **last known value** from when they were active. It is
  *not* updated while benched (the log doesn't emit bench HP unless they switch in).
- If a Pokemon has never been seen, it is absent from the state entirely.

### 2. PP tracking
The replay log does not encode PP values or move PP caps. We track `times_used`
(count of `|move|` lines for that Pokemon+move combination). This is a strict
lower bound on PP consumed.

**Limitation:** PP can be depleted by effects not visible in the log (e.g., Pressure
ability draining 2 PP per use). PP caps (base PP × 1.6 for max PP-Ups) require
external Pokedex data and are out of scope here.

### 3. Unrevealed Pokemon
We only include Pokemon the parser has observed switching in. Unrevealed team slots
are absent from the state. This mirrors the **partial observability** of real play.

### 4. Species string normalisation
The log encodes switches as e.g. `Snorlax, M` or `Tyranitar, F`. We strip the
gender suffix so `species` is always the clean Pokedex name (e.g. `"Snorlax"`).

### 5. Snapshot timing
The state is snapshotted **after** the upkeep phase of turn N (end-of-turn effects
applied) and stored as the state at the **start of turn N+1**. Segment 0 (before
the first `|upkeep`) captures the initial switch-ins and is labelled `turn 1`.

---

## String Representation Format (proposed)

```
Turn 4
──────────────────────────────────────
P1 (Xenocles)
  ACTIVE: Suicune  HP: 100%
  BENCH:
    Zapdos     HP:  61%  status: —       moves: [Thunderbolt×1, Baton Pass×0]
    Snorlax    HP: 100%  status: —       moves: []
    Tyranitar  HP: 100%  status: —       moves: []

P2 (Conflict)
  ACTIVE: Tyranitar  HP: 100%
  BENCH:
    Zapdos     HP:  80%  status: —       moves: [Thunderbolt×1, Toxic×1, Roar×1]
──────────────────────────────────────
```

Fields shown per bench Pokemon:
- Species name
- `HP: XX%` (last known)
- `status:` (abbreviated condition or `—` for none)
- `moves:` list of `MoveName×timesUsed` for each observed move

---

## Questions for Review

1. **Bench HP staleness** — Should we annotate bench HP with a "last seen on turn N"
   label, or is the raw percentage sufficient?

2. **PP representation** — Should we show `times_used` as-is (e.g. `Surf×2`),
   or attempt to show estimated remaining PP (requires bundling a move PP table)?

3. **Unrevealed slots** — Should we show placeholder rows like `[Unknown ×2]` so
   the reader knows how many unrevealed Pokemon remain, or omit them entirely?

4. **Fainted Pokemon** — Should fainted Pokemon appear in the bench list with
   `HP: 0% (fainted)`, or be removed from the bench entirely?

5. **Active Pokemon moves** — The spec says moves/PP for bench; should the active
   Pokemon's move list also be shown in the string representation?

6. **Segment 0 (pre-game header)** — Should the initial switch-in segment be
   included as `turn 0` / `turn 1`, or skipped entirely?

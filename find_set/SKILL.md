# Skill: find-set

Infer the EV spread of a specific opponent Pokemon (PokemonX) by analyzing battle history against a known team.

## Inputs

- **team_file** — path to a text file describing the user's team (Pokemon, moves, EV investments)
- **pokemon_name** — the name of the opponent Pokemon to analyze (PokemonX)
- **battle_history** — path to a battle log file (Pokemon Showdown replay format or structured turn-by-turn log)
- **format** — the battle format (e.g., Gen 9 OU) to ensure correct damage calculations

## Steps

### 1. Initialize EV ranges

Create an EV range for each of PokemonX's six stats, initialized to `[0, 252]`. Assume 31 IVs in every stat throughout the analysis — this applies even if PokemonX's observed Hidden Power type implies non-31 IVs; treat it as close enough. Track ranges as `{ hp: [0,252], atk: [0,252], def: [0,252], spa: [0,252], spd: [0,252], spe: [0,252] }`.

### 2. Load team

Parse the team file. For each Pokemon on the user's team, record:
- Species, level, nature
- Moves (name, category: physical/special, base power, type)
- EV investments in all stats

### 3. Parse battle history

Walk the battle log turn by turn. Identify every turn that involves PokemonX:
- Turns where PokemonX uses a damaging move (PokemonX deals damage)
- Turns where an ally Pokemon uses a damaging move against PokemonX (PokemonX receives damage)
- Turns where turn order reveals speed information (who moved first)

### 4. Narrow speed range from turn order

For each turn where relative speed is observable (PokemonX moved before or after a known Pokemon on the user's team):

- Determine the minimum speed stat of the ally that moved after PokemonX, or the maximum speed stat of the ally that moved before PokemonX.
- Convert the ally's speed stat to an EV threshold using the standard stat formula with the ally's nature and IVs.
- Update `spe` range:
  - PokemonX moved first → `spe[0] = max(spe[0], ally_speed_stat_evs_to_outspeed)`
  - PokemonX moved second → `spe[1] = min(spe[1], ally_speed_stat_evs_to_be_outsped)`

### 5. Narrow offensive EV range from damage dealt

For each turn where PokemonX dealt damage to a user Pokemon:

Note: use the format-specific information for the calculations.

1. Identify the move used (physical → Attack stat, special → Special Attack stat).
2. Make two API calls to `calc.pokemonshowdown.com` to get the damage range:
   - **Minimum investment**: PokemonX with 0 EVs in the relevant offensive stat (with a neutral or hindering nature as appropriate).
   - **Maximum investment**: PokemonX with 252 EVs in the relevant offensive stat and a boosting nature.
3. Record the actual HP lost by the target from the battle log. Treat all HP values as percentages unless the log explicitly states raw HP numbers.
4. Narrow the EV range:
   - If actual damage > max of the minimum-investment damage range → raise `atk[0]` (or `spa[0]`) to the minimum EVs that can produce the observed damage.
   - If actual damage < min of the maximum-investment damage range → lower `atk[1]` (or `spa[1]`) to the maximum EVs that can produce the observed damage.
   - Binary search or iterative API calls over the EV range to find the tightest bracket that contains the observed damage.


### 6. Narrow defensive EV range from damage received

For each turn where an ally dealt damage to PokemonX via a **direct attack** (moves that hit for a damage roll). Skip passive damage sources such as residual damage from weather, burn, poison, Leech Seed, entry hazards, and recoil — these are constant percentages and carry no EV information.

Note: use the format-specific information for the calculations.

1. Identify the move used (physical → Defense stat, special → Special Defense stat).
2. Make two API calls to `calc.pokemonshowdown.com`:
   - **Minimum investment**: PokemonX with 0 EVs in the relevant defensive stat.
   - **Maximum investment**: PokemonX with 252 EVs in the relevant defensive stat and a boosting nature.
3. Record the actual HP lost by PokemonX from the battle log. Treat all HP values as percentages unless the log explicitly states raw HP numbers.
4. Narrow the EV range the same way as step 5: find the tightest bracket of defensive EVs consistent with the observed damage.

### 7. Apply 510-EV budget constraint (two-stat full investment rule)

After all turns are processed:

- Check if any two stats have a narrowed range whose lower bound is ≥ 252 (i.e., full investment is confirmed).
- If so, set the upper bound of every other stat's EV range to 0 (no remaining budget).
- Recheck: if this contradicts a previously observed lower bound on another stat, flag an inconsistency.

### 8. Report results

Output a summary:
- For each stat: the narrowed EV range `[min, max]`.
- Confidence notes: which turns drove each narrowing, and how far the range could still be compressed.
- A best-guess EV spread (use the midpoint of each range, rounded to the nearest multiple of 4).
- Any inconsistencies detected (e.g., observed damage requires more EVs than budget allows).

## API usage

All damage calculations must use `calc.pokemonshowdown.com` — do not attempt to compute damage rolls manually. The Showdown calc accounts for all generation-specific mechanics, rounding rules, and item/ability interactions. Construct requests with:
- Attacker: PokemonX species, level, relevant stat EVs being tested, nature
- Defender: the target species, level, known EVs/nature (from team file or Showdown defaults)
- Move: name, generation

Use binary search over the EV range to minimize the number of API calls needed to pinpoint a boundary.

## Notes

- If PokemonX never deals or receives damage, only speed inference is possible.
- Nature affects stat calculations; if PokemonX's nature is unknown, test with neutral nature and note the uncertainty.
- Always assume 31 IVs in all stats, even if Hidden Power type implies otherwise — the approximation is close enough.
- All HP values from the battle log are treated as percentages unless the log explicitly labels them as raw HP numbers.
- Only direct attack hits count for defensive EV narrowing. Passive damage (weather, burn, poison, Leech Seed, hazards, recoil) is always a fixed percentage and provides no EV information — skip those turns.
- Critical hits invalidate damage-based inference for that turn — skip turns flagged as critical hits in the log.
- Multi-hit moves and moves with variable base power (e.g., Gyro Ball, Grass Knot) require extra care; handle them individually.

/**
 * Wrapper around @smogon/calc that calculates damage for a Pokemon's moves
 * against a neutral Mew (??? type, no EVs, Docile nature, level 50).
 *
 * Accepts a single JSON argument: { pokemon_name, moves, ability }
 * Prints a JSON result to stdout.
 *
 * For each damaging move and investment, returns the result.
 *
 * Ability-based weather/terrain is set on the Field automatically so that
 * abilities like Drought, Drizzle, Hadron Engine, Fairy Aura, etc. are
 * properly factored into damage.
 *
 * Moves with KO-scaled power (Last Respects) are expanded into one entry
 * per KO tier: base, 1 KO, 2 KOs, 3 KOs.
 */

"use strict";

const { Generations, Pokemon, Move, Field, calculate } = require("@smogon/calc");

const gen = Generations.get(9);

// Some Pokemon need a form suffix for the calculator to resolve their attacking stats.
const CALC_NAME_MAP = {
  Aegislash: "Aegislash-Blade",
  Palafin: "Palafin-Hero",
};

// Mew base HP at level 50, 0 EVs, 31 IVs:
//   floor((floor((2*100 + 31 + 0)*50/100) + 10 + 50)) = floor(115 + 60) = 175
const MEW_HP = 175;

const ABILITY_TO_WEATHER = {
  Drought: "Sun",
  "Desolate Land": "Harsh Sunshine",
  Drizzle: "Rain",
  "Primordial Sea": "Heavy Rain",
  "Sand Stream": "Sand",
  "Snow Warning": "Snow",
  "Orichalcum Pulse": "Sun",
};

const ABILITY_TO_TERRAIN = {
  "Hadron Engine": "Electric",
  "Electric Surge": "Electric",
  "Grassy Surge": "Grassy",
  "Misty Surge": "Misty",
  "Psychic Surge": "Psychic",
};

// Moves whose base power scales with in-battle conditions that are best
// represented as multiple named variants.  Each variant specifies the
// display suffix and the basePower override to pass to the Move constructor.
const KO_SCALED_MOVES = {
  "Last Respects": [
    { suffix: "",        basePower: 50  },  // 0 fainted allies
    { suffix: " (1 KO)", basePower: 100 },
    { suffix: " (2 KOs)", basePower: 150 },
    { suffix: " (3 KOs)", basePower: 200 },
  ],
};

function championsEvToSV(ev) {
  return ev === 0 ? 0 : 4 + 8 * (ev - 1);
}

function convertSpreadEvs(evs) {
  const out = {};
  for (const stat of ["hp", "atk", "def", "spa", "spd", "spe"]) {
    out[stat] = championsEvToSV(evs[stat] ?? 0);
  }
  return out;
}

function makeMew() {
  const mew = new Pokemon(gen, "Mew", { level: 50 });
  // Override to ??? type so all moves hit at neutral effectiveness.
  mew.species.types = ["???"];
  mew.types = ["???"];
  return mew;
}

function calcAvg(dmg) {
  if (Array.isArray(dmg[0])) {
    return dmg.reduce((total, hitRolls) => total + hitRolls.reduce((a, b) => a + b, 0) / hitRolls.length, 0);
  }
  return dmg.reduce((a, b) => a + b, 0) / dmg.length;
}

function divide(dmg_divisor, dmg_dividend) {
  if (Array.isArray(dmg_divisor[0])) {
    return dmg_divisor.map((arr, i) => arr.map((val, j) => val / dmg_dividend[i][j]));
  }
  return dmg_divisor.map((val, i) => val / dmg_dividend[i]);
}

const SNAP_VALUES = [1, 1.5, 2.0, 2.25, 3.0, 1.5 ** 3, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0];

function snapMultiplier(x) {
  return SNAP_VALUES.reduce((a, b) => Math.abs(b - x) < Math.abs(a - x) ? b : a);
}


function calcMultiplier(damages, moveBP, move, attacker, defender) {
  // Compute multiplier: ratio of actual damage to a baseline that strips all
  // type-based modifiers. The baseline uses the same move at effectiveBP but
  // with a ???-typed attack on an empty field — eliminating STAB, weather
  // type boosts, and terrain boosts. Type effectiveness is already neutral
  // because the defender is ???-typed.

  // rawDesc.moveBP is only set when BP differs from the move's default (e.g. Weather Ball in Sun).
  // Fall back to move.bp for standard moves.
  const effectiveBP = moveBP ?? move.bp;
  // It seems like there's an edge case where a typeless move is used against a typeless opponent. 
  // So we will create a typeless attacker (with the same ability to account for ex. Huge Power).
  const baselineAttacker = new Pokemon(gen, attacker.name, {level: 50, ability: attacker.ability, types: ["???"], evs: attacker.evs, nature: attacker.nature});
  baselineAttacker.species.types = ["???", null];
  baselineAttacker.types = ["???", null];
  const baselineMove = new Move(gen, move.name, {overrides: {basePower: effectiveBP}});
  
  const baselineResult = calculate(gen, baselineAttacker, defender, baselineMove, new Field({ gameType: "Doubles" }));
  const baselineDamages = baselineResult.damage;
  // console.log("Received damage: ", calcAvg(damages))
  // console.log("Baseline damages: ", calcAvg(baselineDamages))
  if (baselineDamages && baselineDamages.length === damages.length) {
    // Use the max roll, as that is 1.0
    return snapMultiplier(damages[damages.length-1] / baselineDamages[baselineDamages.length-1]);
  }
  // console.log("Error: Invalid base damage calculation.");
  return -1;
}

function buildCalcInputs(pokemonName, ability, fieldOpts, bpOverride = null, evs = {}, nature = "Docile") {
  return {
    calcName: CALC_NAME_MAP[pokemonName] || pokemonName,
    ability,
    fieldOpts,
    bpOverride,
    evs,
    nature,
  };
}

function _calcSingleMode(calcName, moveName, bpOverride, ability, fieldOpts, evs, nature) {
  const attacker = new Pokemon(gen, calcName, {
    level: 50,
    evs,
    nature,
    ...(ability ? { ability } : {}),
  });
  const defender = makeMew();
  const field = new Field({ ...fieldOpts, gameType: "Doubles" });

  let move;
  try {
    move = bpOverride !== null
      ? new Move(gen, moveName, { overrides: { basePower: bpOverride } })
      : new Move(gen, moveName);
  } catch {
    return null;
  }

  if (!move.bp || move.bp === 0) return null;

  const result = calculate(gen, attacker, defender, move, field);

  const damages = result.damage;
  if (!damages || damages.length === 0) return null;

  const avg = calcAvg(damages);

  return {
    avg_damage: avg,
    avg_pct: (avg / MEW_HP) * 100,
    multiplier: calcMultiplier(result.damage, result.rawDesc.moveBP, move, attacker, defender),
    desc: result.desc(),
  };
}

function calcSingleMode(pokemonName, moveName, ability, fieldOpts, bpOverride = null, evs = {}, nature = "Docile") {
  const inputs = buildCalcInputs(pokemonName, ability, fieldOpts, bpOverride, evs, nature);
  return _calcSingleMode(inputs.calcName, moveName, inputs.bpOverride, inputs.ability, inputs.fieldOpts, inputs.evs, inputs.nature);
}

function getMoveCategory(moveName) {
  try {
    return new Move(gen, moveName).category; // 'Physical', 'Special', or 'Status'
  } catch {
    return null;
  }
}

function processInput(input) {
  const { pokemon_name, moves, ability } = input;

  const calcName = CALC_NAME_MAP[pokemon_name] || pokemon_name;

  // Determine field weather/terrain from the given ability (or species default).
  let effectiveAbility = ability || null;
  if (!effectiveAbility) {
    try {
      effectiveAbility = new Pokemon(gen, calcName, { level: 50 }).ability;
    } catch {
      effectiveAbility = null;
    }
  }

  // Explicit weather/terrain overrides take precedence over ability-derived ones.
  const fieldOpts = {};
  const weatherOverride = input.weather || null;
  const terrainOverride = input.terrain || null;
  if (weatherOverride) {
    fieldOpts.weather = weatherOverride;
  } else if (effectiveAbility) {
    const weather = ABILITY_TO_WEATHER[effectiveAbility];
    if (weather) fieldOpts.weather = weather;
  }
  if (terrainOverride) {
    fieldOpts.terrain = terrainOverride;
  } else if (effectiveAbility) {
    const terrain = ABILITY_TO_TERRAIN[effectiveAbility];
    if (terrain) fieldOpts.terrain = terrain;
  }

  const results = {};

  for (const moveName of moves) {
    const category = getMoveCategory(moveName);
    if (!category || category === "Status") {
      results[moveName] = null;
      continue;
    }

    const spread = input.spread;
    const modes = {
      Spread: { evs: convertSpreadEvs(spread.evs), nature: spread.nature },
    };

    // Expand KO-scaled moves into multiple named variants.
    const variants = KO_SCALED_MOVES[moveName] || [{ suffix: "", basePower: null }];

    for (const variant of variants) {
      const key = moveName + variant.suffix;
      const modeResults = {};

      for (const [modeName, config] of Object.entries(modes)) {
        modeResults[modeName] = _calcSingleMode(
          calcName,
          moveName,
          variant.basePower,
          ability || null,
          fieldOpts,
          config.evs,
          config.nature
        );
      }

      if (Object.values(modeResults).every((r) => r === null)) {
        results[key] = null;
      } else {
        results[key] = { category, modes: modeResults };
      }
    }
  }

  return results;
}

module.exports = { processInput, calcSingleMode, calcMultiplier, buildCalcInputs };

if (require.main === module) {
  const input = JSON.parse(process.argv[2]);
  console.log(JSON.stringify(processInput(input)));
}

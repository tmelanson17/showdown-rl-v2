"use strict";

const assert = require("assert");
const { Generations, Pokemon, Move, Field, calculate } = require("@smogon/calc");
const { processInput, calcSingleMode } = require("./showdown_calc")

const gen = Generations.get(9);

function calcMultiplier(pokemonName, moveName, ability, fieldOpts, bpOverride = null, evs = {}, nature = "Docile") {
  return calcSingleMode(pokemonName, moveName, ability, fieldOpts, bpOverride, evs, nature)?.multiplier ?? null;
}

function assertMultiplierClose(label, actual, expected, tolerance = 0.01) {
  assert.ok(
    actual !== null,
    `${label}: multiplier was null`
  );
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${label}: expected ~${expected}, got ${actual.toFixed(4)}`
  );
  console.log(`  PASS  ${label}: ${actual.toFixed(4)} (expected ${expected})`);
}

console.log("Running multiplier tests...\n");

// Charizard-Mega-Y: Flamethrower in Sun — Fire STAB + Sun boost = 1.5 × 1.5 = 2.25
assertMultiplierClose(
  "Charizard-Mega-Y Overheat in Sun (STAB)",
  calcSingleMode("Charizard-Mega-Y", "Overheat", "Drought", { weather: "Sun" })?.multiplier,
  2.25
);

// Make sure it works with EVs.
assertMultiplierClose(
  "Charizard-Mega-Y Overheat in Sun (STAB)",
  calcSingleMode("Charizard-Mega-Y", "Overheat", "Drought", { weather: "Sun" }, null, {spa: 252})?.multiplier,
  2.25
);

// Also that it works with natures.
assertMultiplierClose(
  "Charizard-Mega-Y Overheat in Sun (STAB)",
  calcSingleMode("Charizard-Mega-Y", "Overheat", "Drought", { weather: "Sun" }, null, {}, "Modest")?.multiplier,
  2.25
);

// Check dual typing.
assertMultiplierClose(
  'Charizard-Mega-Y Air Slash (STAB)',
  calcSingleMode("Charizard-Mega-Y", "Air Slash", "Drought", {}, null, {}, "Modest")?.multiplier,
  1.5
)

// Both nature and EVs
assertMultiplierClose(
  "Charizard-Mega-Y Overheat in Sun (STAB)",
  calcSingleMode("Charizard-Mega-Y", "Overheat", "Drought", { weather: "Sun" }, null, {spa: 252}, "Modest")?.multiplier,
  2.25
);

// Charizard-Mega-Y: Drought sets Sun, Weather Ball becomes Fire-type (100 BP).
// Charizard is Fire/Flying, so Fire STAB applies: 1.5 (Sun) × 1.5 (STAB) = 2.25
assertMultiplierClose(
  "Charizard-Mega-Y Weather Ball in Sun (STAB)",
  calcSingleMode("Charizard-Mega-Y", "Weather Ball", "Drought", { weather: "Sun" })?.multiplier,
  2.25
);

// Charizard-Mega-Y: Flamethrower with no weather — STAB only = 1.5
assertMultiplierClose(
  "Charizard-Mega-Y Flamethrower no weather (STAB only)",
  calcSingleMode("Charizard-Mega-Y", "Flamethrower", null, {})?.multiplier,
  1.5
);

// Normal type Hyper Voice: Expect STAB
assertMultiplierClose(
  "Normal type Hyper Voice",
  calcMultiplier("Audino", "Hyper Voice", null, {}),
  1.5
)

// Mew: uses a Normal move with no STAB on a ??? defender — multiplier should be 1.0
assertMultiplierClose(
  "Mew Hyper Voice (no STAB, neutral)",
  calcMultiplier("Mew", "Hyper Voice", null, {}),
  1.0
);

// Huge Power Azumarill only shows 1.5x boost, not 3x
assertMultiplierClose(
  "Azumarill (Huge Power) Liquidation (ability modifier, but no move modifier)",
  calcMultiplier("Azumarill", "Liquidation", "Huge Power", {}),
  1.5
)

// Also check for Adaptability.
assertMultiplierClose(
  "Basculegion (Adaptability) Wave Crash in Rain",
  calcMultiplier("Basculegion", "Wave Crash", "Adaptability", {weather: "Rain"}),
  3.0
)


console.log("\nAll tests passed.");

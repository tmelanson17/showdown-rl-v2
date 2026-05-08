"use strict"; 

const {calculate, Generations, Pokemon, Move} = require('@smogon/calc');

const gen = Generations.get(9); // alternatively: const gen = 5;
const result = calculate(
  gen,
  new Pokemon(gen, 'Starmie-Mega', {
    nature: 'Jolly',
    evs: {atk: 252},
  }),
  new Pokemon(gen, 'Floette-Mega', {
    nature: 'Bold',
    evs: {hp: 252, def: 252},
  }),
  new Move(gen, 'Liquidation')
);

console.log(result);

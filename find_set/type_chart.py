from datatypes import Type

# Effectiveness multipliers: TYPE_CHART[attacking][defending] -> multiplier
# 0 = immune, 0.5 = not very effective, 1 = neutral, 2 = super effective
TYPE_CHART: dict[Type, dict[Type, float]] = {
    Type.NORMAL: {
        Type.ROCK: 0.5, Type.GHOST: 0, Type.STEEL: 0.5,
    },
    Type.FIRE: {
        Type.FIRE: 0.5, Type.WATER: 0.5, Type.GRASS: 2, Type.ICE: 2,
        Type.BUG: 2, Type.ROCK: 0.5, Type.DRAGON: 0.5, Type.STEEL: 2,
    },
    Type.WATER: {
        Type.FIRE: 2, Type.WATER: 0.5, Type.GRASS: 0.5, Type.GROUND: 2,
        Type.ROCK: 2, Type.DRAGON: 0.5,
    },
    Type.GRASS: {
        Type.FIRE: 0.5, Type.WATER: 2, Type.GRASS: 0.5, Type.POISON: 0.5,
        Type.GROUND: 2, Type.FLYING: 0.5, Type.BUG: 0.5, Type.ROCK: 2,
        Type.DRAGON: 0.5, Type.STEEL: 0.5,
    },
    Type.ELECTRIC: {
        Type.WATER: 2, Type.ELECTRIC: 0.5, Type.GRASS: 0.5, Type.GROUND: 0,
        Type.FLYING: 2, Type.DRAGON: 0.5,
    },
    Type.ICE: {
        Type.WATER: 0.5, Type.GRASS: 2, Type.ICE: 0.5, Type.GROUND: 2,
        Type.FLYING: 2, Type.DRAGON: 2, Type.STEEL: 0.5,
    },
    Type.FIGHTING: {
        Type.NORMAL: 2, Type.ICE: 2, Type.POISON: 0.5, Type.FLYING: 0.5,
        Type.PSYCHIC: 0.5, Type.BUG: 0.5, Type.ROCK: 2, Type.GHOST: 0,
        Type.DARK: 2, Type.STEEL: 2, Type.FAIRY: 0.5,
    },
    Type.POISON: {
        Type.GRASS: 2, Type.POISON: 0.5, Type.GROUND: 0.5, Type.ROCK: 0.5,
        Type.GHOST: 0.5, Type.STEEL: 0, Type.FAIRY: 2,
    },
    Type.GROUND: {
        Type.FIRE: 2, Type.ELECTRIC: 2, Type.GRASS: 0.5, Type.POISON: 2,
        Type.FLYING: 0, Type.BUG: 0.5, Type.ROCK: 2, Type.STEEL: 2,
    },
    Type.FLYING: {
        Type.ELECTRIC: 0.5, Type.GRASS: 2, Type.FIGHTING: 2, Type.BUG: 2,
        Type.ROCK: 0.5, Type.STEEL: 0.5,
    },
    Type.PSYCHIC: {
        Type.FIGHTING: 2, Type.POISON: 2, Type.PSYCHIC: 0.5, Type.DARK: 0,
        Type.STEEL: 0.5,
    },
    Type.BUG: {
        Type.FIRE: 0.5, Type.GRASS: 2, Type.FIGHTING: 0.5, Type.POISON: 0.5,
        Type.FLYING: 0.5, Type.PSYCHIC: 2, Type.GHOST: 0.5, Type.DARK: 2,
        Type.STEEL: 0.5, Type.FAIRY: 0.5,
    },
    Type.ROCK: {
        Type.FIRE: 2, Type.ICE: 2, Type.FIGHTING: 0.5, Type.GROUND: 0.5,
        Type.FLYING: 2, Type.BUG: 2, Type.STEEL: 0.5,
    },
    Type.GHOST: {
        Type.NORMAL: 0, Type.PSYCHIC: 2, Type.GHOST: 2, Type.DARK: 0.5,
    },
    Type.DRAGON: {
        Type.DRAGON: 2, Type.STEEL: 0.5, Type.FAIRY: 0,
    },
    Type.DARK: {
        Type.FIGHTING: 0.5, Type.PSYCHIC: 2, Type.GHOST: 2, Type.DARK: 0.5,
        Type.FAIRY: 0.5,
    },
    Type.STEEL: {
        Type.FIRE: 0.5, Type.WATER: 0.5, Type.ELECTRIC: 0.5, Type.ICE: 2,
        Type.ROCK: 2, Type.STEEL: 0.5, Type.FAIRY: 2,
    },
    Type.FAIRY: {
        Type.FIRE: 0.5, Type.FIGHTING: 2, Type.POISON: 0.5, Type.DRAGON: 2,
        Type.DARK: 2, Type.STEEL: 0.5,
    },
}


def get_type_effectiveness(
    move_type: Type,
    defending_type1: Type,
    defending_type2: Type | None = None,
) -> float:
    chart = TYPE_CHART.get(move_type, {})
    multiplier = chart.get(defending_type1, 1.0)
    if defending_type2 is not None:
        multiplier *= chart.get(defending_type2, 1.0)
    return multiplier

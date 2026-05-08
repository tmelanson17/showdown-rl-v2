import math
from dataclasses import dataclass, field

from datatypes import Stat, Ivs, PokemonEvs, MoveCategory
from nature import Nature, BOOSTING, NEUTRAL, get_info, get_boost

MIN_DAMAGE_ROLL = 0.85
MAX_DAMAGE_ROLL = 1.0

GEN3 = False


def stat_helper_calc(base: int, ev: int, iv: int = 31, level: int = 50):
    return math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100)


# Calculate the base stat based on EV's
def calculate_hp(base: int, ev: int, iv: int = 31, level: int = 50):
    return stat_helper_calc(base, ev, iv, level) + level + 10


def calculate_stat(
    base: int,
    ev: int,
    iv: int = 31,
    level: int = 50,
    nature_multiplier: float = NEUTRAL,
):
    return math.floor((stat_helper_calc(base, ev, iv, level) + 5) * nature_multiplier)


def get_critical_multiplier(critical: bool = False):
    return 1 + int(critical) if GEN3 else 1 + int(critical) * 0.5


# Calculates stat range assuming at least 31 IV's and neutral nature
def get_nominal_stat_range(base: int, level: int = 50):
    return (
        calculate_stat(base, 0, level=level, nature_multiplier=NEUTRAL),
        calculate_stat(base, 252, level=level, nature_multiplier=NEUTRAL),
        calculate_stat(base, 252, level=level, nature_multiplier=BOOSTING),
    )


def get_nominal_hp_range(base: int, level: int = 50):
    return (calculate_hp(base, 0, level=level), calculate_hp(base, 252, level=level))


def calculate_damage_roll(
    bp: int,
    attack: int,
    level: int,
    defense: int,
    stab: float = 1,
    spread: float = 1,
    effectiveness: float = 1,
    critical: bool = False,
    burn: float = 1,
    weather: float = 1,
) -> tuple[int, int]:
    base_damage = ((2 * level / 5 + 2) * bp * (attack / defense)) / 50 + 2
    crit_mux = get_critical_multiplier(critical)
    max_damage = base_damage * stab * spread * effectiveness * crit_mux * burn * weather
    return (int(MIN_DAMAGE_ROLL * max_damage), int(max_damage))


# Finds the minimum and maximum offensive value to generate the damage
def find_attack_range(
    damage: float,
    bp: int,
    defense: int,
    level: int,
    stab: float = 1,
    effectiveness: float = 1,
    critical: bool = False,
    burn: float = 1,
    weather: float = 1,
):
    critical_multiplier = get_critical_multiplier(critical)
    attack_range = [0, float("inf")]
    for i, roll in enumerate([MAX_DAMAGE_ROLL, MIN_DAMAGE_ROLL]):
        natural_damage = (
            damage
            / (roll * stab * effectiveness * critical_multiplier * burn * weather)
            - 2
        )
        power_ratio = natural_damage * 50 / ((2 * level) / 5 + 2)
        attack = power_ratio * defense / bp
        attack_range[i] = attack
    return (attack_range[0], attack_range[1])


@dataclass
class PokemonStats:
    base_stats: Stat
    evs: PokemonEvs
    nature: str
    level: int
    name: str
    ivs: Ivs = field(default_factory=Ivs.all_max)


# Get approximate values for an attack into an opponent with various levels of bulk
# TODO: Combine the added effects into a single dataclass.
def get_damage_range(
    attacker: PokemonStats,
    defender_base: Stat,
    defender_level: int,
    move_bp: int,
    move_cat: MoveCategory,
    stab: float = 1.0,
    spread: float = 1.0,
    effectiveness: float = 1.0,
    critical: bool = False,
    burn: float = 1.0,
    weather: float = 1.0,
):
    attack_stat = calculate_stat(
        move_cat.get_attacking_stat(attacker.base_stats),
        move_cat.get_attacking_stat(attacker.evs),
        move_cat.get_attacking_stat(attacker.ivs),
        level=attacker.level,
        nature_multiplier=get_boost(
            str(attacker.nature), move_cat.get_attacking_stat_name()
        ),
    )
    print(f"Attacker stat: {attack_stat}")
    min_def, _, max_def = get_nominal_stat_range(
        move_cat.get_defending_stat(defender_base), defender_level
    )
    print(f"Defense range: {min_def}, {max_def}")
    average_damage = []
    for defense_num in (max_def, min_def):
        min_roll, max_roll = calculate_damage_roll(
            move_bp,
            attack_stat,
            attacker.level,
            defense_num,
            stab,
            spread,
            effectiveness,
            critical,
            burn,
            weather,
        )
        # TODO: Get an average of all of the damage ranges, not just min and max.
        average_damage.append((min_roll + max_roll) / 2)

    return tuple(average_damage)


def get_percentage(base_hp, damage, level=50):
    min_hp, max_hp = get_nominal_hp_range(base_hp, level)
    return (damage / min_hp) * 100, (damage / max_hp) * 100


if __name__ == "__main__":
    # Jirachi base stats are all 100
    jirachi = Stat(hp=100, atk=100, defn=100, spatk=100, spdef=100, speed=100)
    weezing = Stat(hp=65, atk=90, defn=120, spatk=85, spdef=70, speed=60)

    # EV spreads (see )
    jirachi_evs = PokemonEvs(hp=252, defn=32, spdef=224)

    move_bp = 120
    damage_fraction = 0.364
    defender_hp = calculate_hp(jirachi.hp, jirachi_evs.hp, level=100)
    effectiveness = 2.0
    defender_spdef = calculate_stat(
        jirachi.spdef, jirachi_evs.spdef, level=100, nature_multiplier=BOOSTING
    )
    level = 100
    critical = False
    burn = 1.0
    weather = 1.0
    print(
        find_attack_range(
            damage_fraction * defender_hp,
            move_bp,
            defender_spdef,
            level,
            effectiveness=effectiveness,
            critical=critical,
            burn=burn,
            weather=weather,
        )
    )
    min_spatk, invested_spatk, max_spatk = get_nominal_stat_range(
        base=weezing.spatk, level=100
    )
    print(f"Weezing attack range: {min_spatk}-{invested_spatk}-{max_spatk}")

    # Get damage range on max attack Garchomp
    garchomp = PokemonStats(
        base_stats=Stat(hp=108, atk=130, defn=95, spatk=80, spdef=85, speed=102),
        evs=PokemonEvs(atk=252),
        nature="Adamant",
        name="Garchomp",
        level=50,
    )
    archaludon_base = Stat(hp=90, atk=105, defn=130, spatk=125, spdef=65, speed=85)

    def calculate_expected_damages(
        attacker: PokemonStats, defender_base: Stat, defender_name: str
    ):
        min_damage, max_damage = get_damage_range(
            garchomp,
            defender_base,
            defender_level=50,
            move_bp=75,
            move_cat=MoveCategory.PHYSICAL,
            stab=1.5,
            effectiveness=2.0,
        )
        print(
            f"Expected Stomping Tantrum damage from {attacker.name} into {defender_name}: {min_damage}-{max_damage}"
        )
        print("Damage ranges: ")
        offensive, bulky = get_percentage(defender_base.hp, max_damage)
        _, defensive = get_percentage(defender_base.hp, min_damage)
        print(f"Offensive: {offensive:.02f}")
        print(f"Bulky:  {bulky:.02f}")
        print(f"Defensive:  {defensive:.02f}")

    calculate_expected_damages(garchomp, archaludon_base, "Archaludon")

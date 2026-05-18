MYTHICAL_DEFENSE = 120

import numpy as np
from typing import Optional

from find_set.type_chart import get_type_effectiveness
from find_set.datatypes import Type


def _get_adjusted_damage(total_damage, multiplier, effectiveness, defense):
    base_damage = total_damage / multiplier - 2
    return (MYTHICAL_DEFENSE / defense * base_damage + 2) * effectiveness * multiplier


def adjust_damage(
    total_damage: np.ndarray,
    multiplier: np.ndarray,
    defense: float,
    move_type: list[Type],
    defender_type1: Type,
    defender_type2: Type | None,
):
    effectiveness = [
        get_type_effectiveness(mt, defender_type1, defender_type2) for mt in move_type
    ]

    return _get_adjusted_damage(
        total_damage,
        multiplier,
        np.array(effectiveness),
        defense,
    )

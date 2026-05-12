MYTHICAL_DEFENSE = 120

import numpy as np
from typing import Optional

from ..find_set.type_chart import get_type_effectiveness
from ..find_set.datatypes import Type


def _get_adjusted_damage(total_damage, multiplier, effectiveness):
    base_damage = total_damage / multiplier - 2
    return MYTHICAL_DEFENSE / base_damage * effectiveness + 2


def adjust_damage(
    total_damage: np.ndarray,
    multiplier: np.ndarray,
    move_type1: Type,
    defender_type1: Type,
    defender_type2: Optional[Type],
):
    return _get_adjusted_damage(
        total_damage,
        multiplier,
        get_type_effectiveness(move_type1, defender_type1, defender_type2),
    )

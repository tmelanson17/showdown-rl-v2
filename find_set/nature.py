from typing import Optional
from datatypes import MoveCategory

Nature = dict[str, float]

NEUTRAL = 1.0
BOOSTING = 1.1
DIMINISHING = 0.9


_NATURES = {
    "Hardy": {},
    "Docile": {},
    "Serious": {},
    "Bashful": {},
    "Quirky": {},
    "Lonely": {"atk": 1.1, "def": 0.9},
    "Brave": {"atk": 1.1, "spe": 0.9},
    "Adamant": {"atk": 1.1, "spatk": 0.9},
    "Naughty": {"atk": 1.1, "spdef": 0.9},
    "Bold": {"def": 1.1, "atk": 0.9},
    "Relaxed": {"def": 1.1, "spe": 0.9},
    "Impish": {"def": 1.1, "spatk": 0.9},
    "Lax": {"def": 1.1, "spdef": 0.9},
    "Timid": {"spe": 1.1, "atk": 0.9},
    "Hasty": {"spe": 1.1, "def": 0.9},
    "Jolly": {"spe": 1.1, "spatk": 0.9},
    "Naive": {"spe": 1.1, "spdef": 0.9},
    "Modest": {"spatk": 1.1, "atk": 0.9},
    "Mild": {"spatk": 1.1, "def": 0.9},
    "Quiet": {"spatk": 1.1, "spe": 0.9},
    "Rash": {"spatk": 1.1, "spdef": 0.9},
    "Calm": {"spdef": 1.1, "atk": 0.9},
    "Gentle": {"spdef": 1.1, "def": 0.9},
    "Sassy": {"spdef": 1.1, "spe": 0.9},
    "Careful": {"spdef": 1.1, "spatk": 0.9},
}


def get_info(nature: str) -> Nature:
    return _NATURES[nature.capitalize()]


def get_boost(nature: str, stat: str) -> float:
    nature_info = get_info(nature)
    return 1.0 if stat not in nature_info else nature_info[stat]

import enum
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Stat:
    hp: int
    atk: int
    defn: int
    spatk: int
    spdef: int
    speed: int


@dataclass
class PokemonEvs(Stat):
    hp: int = 0
    atk: int = 0
    defn: int = 0
    spatk: int = 0
    spdef: int = 0
    speed: int = 0


@dataclass
class Ivs(Stat):
    @staticmethod
    def all_max() -> "Ivs":
        return Ivs(hp=31, atk=31, defn=31, spatk=31, spdef=31, speed=31)


class MoveCategory(enum.Enum):
    PHYSICAL = 1
    SPECIAL = 2

    def get_attacking_stat(self, pokemon: Stat):
        return pokemon.atk if self == MoveCategory.PHYSICAL else pokemon.spatk

    def get_defending_stat(self, pokemon: Stat):
        return pokemon.defn if self == MoveCategory.PHYSICAL else pokemon.spdef

    def get_attacking_stat_name(self) -> str:
        return "atk" if self == MoveCategory.PHYSICAL else "spatk"

    def get_defending_stat_name(self) -> str:
        return "defn" if self == MoveCategory.PHYSICAL else "spdef"


class Type(enum.Enum):
    NORMAL = 1
    FIRE = 2
    WATER = 3
    GRASS = 4
    ELECTRIC = 5
    FLYING = 6
    BUG = 7
    POISON = 8
    ROCK = 9
    GROUND = 10
    FIGHTING = 11
    ICE = 12
    PSYCHIC = 13
    GHOST = 14
    DRAGON = 15
    DARK = 16
    STEEL = 17
    FAIRY = 18


@dataclass
class Move:
    type: Type
    category: MoveCategory
    bp: int
    name: str

    def stab(self, type1: Type, type2: Optional[Type]) -> float:
        return (
            1.5
            if self.type == type1 or (type2 is not None and self.type == type2)
            else 1.0
        )


@dataclass
class PokemonStats:
    base_stats: Stat
    nature: str
    level: int
    type1: Type
    type2: Optional[Type]
    name: str
    evs: Optional[PokemonEvs] = None
    ivs: Ivs = field(default_factory=Ivs.all_max)

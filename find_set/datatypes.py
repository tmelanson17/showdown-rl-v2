import enum
from dataclasses import dataclass


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

"""
teams.py — Team management for Pokémon Showdown battles.

Handles loading, saving, generating, and converting teams between formats:
- Export format (PokePaste) - human-readable
- Packed format - compressed, for sending to PS server
- JSON format - structured, for internal use

For gen9ou battles, we need to provide actual teams. This module can:
1. Load teams from files (export or packed format)
2. Generate random teams using PS's team generator
3. Fetch sample teams from the data files
"""

from __future__ import annotations
import json
import os
import random
import subprocess
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Path to Pokemon Showdown (WSL format)
PS_PATH = "/mnt/c/Users/tmela/development/pokemans/pokemon-showdown"


@dataclass
class PokemonSet:
    """A single Pokémon's configuration."""

    species: str
    item: str = ""
    ability: str = ""
    moves: List[str] = field(default_factory=list)
    nature: str = "Hardy"
    evs: Dict[str, int] = field(
        default_factory=lambda: {
            "hp": 0,
            "atk": 0,
            "def": 0,
            "spa": 0,
            "spd": 0,
            "spe": 0,
        }
    )
    ivs: Dict[str, int] = field(
        default_factory=lambda: {
            "hp": 31,
            "atk": 31,
            "def": 31,
            "spa": 31,
            "spd": 31,
            "spe": 31,
        }
    )
    level: int = 100
    gender: str = ""
    shiny: bool = False
    nickname: str = ""
    teraType: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PokemonSet":
        return cls(
            species=d.get("species", ""),
            item=d.get("item", ""),
            ability=d.get("ability", ""),
            moves=d.get("moves", []),
            nature=d.get("nature", "Hardy"),
            evs=d.get(
                "evs", {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
            ),
            ivs=d.get(
                "ivs", {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31}
            ),
            level=d.get("level", 100),
            gender=d.get("gender", ""),
            shiny=d.get("shiny", False),
            nickname=d.get("name", d.get("nickname", "")),
            teraType=d.get("teraType", ""),
        )


class TeamManager:
    """Manages teams for battles."""

    def __init__(self, teams_dir: str = "teams", ps_path: str = PS_PATH):
        self.teams_dir = teams_dir
        self.ps_path = ps_path
        os.makedirs(teams_dir, exist_ok=True)

        # Cache of loaded teams
        self._team_cache: Dict[str, List[str]] = {}  # format -> list of packed teams

    def load_team_from_file(self, filepath: str) -> str:
        """Load a team from a file and return in packed format."""
        with open(filepath, "r") as f:
            content = f.read().strip()

        # If it looks like packed format (no newlines in pokemon entries), return as-is
        if "]" in content and "\n\n" not in content:
            return content

        # Otherwise, convert from export format to packed
        return self.export_to_packed(content)

    def save_team_to_file(
        self, packed_team: str, filepath: str, as_export: bool = True
    ) -> None:
        """Save a team to a file."""
        if as_export:
            content = self.packed_to_export(packed_team)
        else:
            content = packed_team

        with open(filepath, "w") as f:
            f.write(content)

        logger.info("TeamManager: saved team to %s", filepath)

    def export_to_packed(self, export_team: str) -> str:
        """Convert export format (PokePaste) to packed format using PS."""
        cmd = f"cd {self.ps_path} && node pokemon-showdown --skip-build pack-team"

        result = subprocess.run(
            ["wsl", "-e", "bash", "-c", cmd],
            input=export_team,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error("TeamManager: pack-team failed: %s", result.stderr)
            raise ValueError(f"Failed to pack team: {result.stderr}")

        return result.stdout.strip()

    def packed_to_export(self, packed_team: str) -> str:
        """Convert packed format to export format (PokePaste) using PS."""
        cmd = f"cd {self.ps_path} && node pokemon-showdown --skip-build export-team"

        result = subprocess.run(
            ["wsl", "-e", "bash", "-c", cmd],
            input=packed_team,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error("TeamManager: export-team failed: %s", result.stderr)
            raise ValueError(f"Failed to export team: {result.stderr}")

        return result.stdout.strip()

    def generate_random_team(self, format_id: str = "gen9randombattle") -> str:
        """Generate a random team for a format using PS's generator."""
        # For random battle formats, PS can generate teams
        if "random" in format_id.lower():
            cmd = f"cd {self.ps_path} && node -e \"const {{Teams, Dex}} = require('./dist/sim'); Dex.formats.get('{format_id}'); console.log(Teams.pack(Teams.generate('{format_id}')))\""

            result = subprocess.run(
                ["wsl", "-e", "bash", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

        # For non-random formats, use sample teams
        return self.get_sample_team(format_id)

    def get_sample_team(self, format_id: str = "gen9ou", team_index: int | None = None) -> str:
        """Get a sample team for a format.

        Args:
            format_id: Battle format string.
            team_index: If set, return the built-in team at this 0-based index,
                        bypassing any saved team files.
        """
        if team_index is not None:
            return self._get_builtin_sample_team(format_id, team_index=team_index)

        # Check cache first
        if format_id in self._team_cache and self._team_cache[format_id]:
            return random.choice(self._team_cache[format_id])

        # Check for saved teams in teams directory
        format_dir = os.path.join(
            self.teams_dir, format_id.replace("[", "").replace("]", "")
        )
        if os.path.exists(format_dir):
            team_files = [f for f in os.listdir(format_dir) if f.endswith(".txt")]
            if team_files:
                filepath = os.path.join(format_dir, random.choice(team_files))
                return self.load_team_from_file(filepath)

        # Use built-in sample teams
        return self._get_builtin_sample_team(format_id)

    def _get_builtin_sample_team(self, format_id: str, team_index: int | None = None) -> str:
        """Get a built-in sample team for testing."""
        # Sample teams by format in packed format
        teams_by_format = {
            "gen9ou": [
                # Team 1: Balanced offense
                "Great Tusk||leftovers|protosynthesis|headlongrush,rapidspin,stealthrock,knockoff|Jolly|252,4,,,252,||,20,,,,|||]"
                "Gholdengo||choicescarf|goodasgold|makeitrain,shadowball,trick,focusblast|Timid|,,,252,4,252|||||]"
                "Dragapult||choiceband|infiltrator|dragondarts,phantomforce,uturn,suckerpunch|Jolly|,252,4,,,252|||||]"
                "Kingambit||leftovers|supremeoverlord|swordsdance,kowtowcleave,suckerpunch,ironhead|Adamant|252,252,4,,,|||||]"
                "Slowking-Galar||heavydutyboots|regenerator|futuresight,sludgebomb,flamethrower,slackoff|Calm|252,,4,,252,|||||]"
                "Rillaboom||choiceband|grassysurge|woodhammer,grassyglide,knockoff,uturn|Adamant|,252,4,,,252|||||",
                # Team 2: Hyper offense
                "Iron Valiant||boosterenergy|quarkdrive|swordsdance,spiritbreak,closecombat,knockoff|Jolly|,252,4,,,252|||||]"
                "Roaring Moon||boosterenergy|protosynthesis|dragondance,acrobatics,knockoff,earthquake|Jolly|,252,4,,,252|||||]"
                "Dragapult||focussash|infiltrator|dracometeor,shadowball,uturn,thunderwave|Timid|,,,252,4,252|||||]"
                "Gholdengo||airballoon|goodasgold|nastyplot,makeitrain,shadowball,recover|Timid|,,,252,4,252|||||]"
                "Great Tusk||leftovers|protosynthesis|headlongrush,icespinner,rapidspin,stealthrock|Jolly|,252,4,,,252|||||]"
                "Kingambit||blackglasses|supremeoverlord|swordsdance,kowtowcleave,suckerpunch,ironhead|Adamant|252,252,4,,,|||||",
                # Team 3: Bulky offense
                "Clefable||leftovers|magicguard|moonblast,stealthrock,thunderwave,softboiled|Bold|252,,252,,4,|||||]"
                "Heatran||leftovers|flashfire|magmastorm,earthpower,taunt,stealthrock|Timid|,,,252,4,252|||||]"
                "Garchomp||rockyhelmet|roughskin|earthquake,dragontail,stealthrock,spikes|Impish|252,,236,,20,|||||]"
                "Slowking-Galar||heavydutyboots|regenerator|futuresight,sludgebomb,icebeam,slackoff|Calm|252,,4,,252,|||||]"
                "Corviknight||leftovers|pressure|bravebird,bodypress,roost,defog|Impish|252,,168,,88,|||||]"
                "Dragapult||choicespecs|infiltrator|dracometeor,shadowball,fireblast,uturn|Timid|,,,252,4,252|||||",
            ],
            "gen3ou": [
                # Gen 3 OU Team 1: Classic SkarmBliss + Dragon Dance Tyranitar
                "Tyranitar||leftovers|sandstream|dragondance,rockslide,earthquake,focuspunch|Adamant|40,252,,,,216|||||]"
                "Skarmory||leftovers|keeneye|spikes,roar,drillpeck,rest|Impish|252,,252,,4,|||||]"
                "Blissey||leftovers|naturalcure|aromatherapy,thunderwave,softboiled,seismictoss|Bold|252,,252,,4,|||||]"
                "Swampert||leftovers|torrent|earthquake,icebeam,protect,toxic|Relaxed|252,,240,,16,|||||]"
                "Jirachi||leftovers|serenegrace|calmmind,psychic,thunderbolt,substitute|Timid|,,,252,4,252|||||]"
                "Starmie||leftovers|naturalcure|surf,thunderbolt,icebeam,recover|Timid|,,,252,4,252|||||",
                # Gen 3 OU Team 2: Salamence offense
                "Salamence||leftovers|intimidate|dragondance,aerialace,earthquake,rockslide|Adamant|,252,4,,,252|||||]"
                "Magneton||leftovers|magnetpull|thunderbolt,toxic,substitute,thunderwave|Modest|,,,252,4,252|||||]"
                "Swampert||leftovers|torrent|earthquake,icebeam,hydropump,toxic|Relaxed|252,,216,,40,|||||]"
                "Skarmory||leftovers|keeneye|spikes,roar,drillpeck,rest|Impish|252,,252,,4,|||||]"
                "Celebi||leftovers|naturalcure|calmmind,psychic,gigadrain,recover|Bold|252,,216,,40,|||||]"
                "Gengar||leftovers|levitate|thunderbolt,icepunch,willowisp,destinybond|Timid|,,,252,4,252|||||",
                # Gen 3 OU Team 3: Mixed Attacker Offense
                "Metagross||leftovers|clearbody|meteormash,earthquake,explosion,rockslide|Adamant|252,252,4,,,|||||]"
                "Suicune||leftovers|pressure|calmmind,surf,icebeam,rest|Bold|252,,252,,4,|||||]"
                "Aerodactyl||choiceband|rockhead|rockslide,earthquake,aerialace,doubleedge|Jolly|,252,4,,,252|||||]"
                "Gengar||leftovers|levitate|willowisp,thunderbolt,icepunch,destinybond|Timid|,,,252,4,252|||||]"
                "Forretress||leftovers|sturdy|spikes,rapidspin,earthquake,explosion|Relaxed|252,,252,,4,|||||]"
                "Claydol||leftovers|levitate|earthquake,psychic,rapidspin,explosion|Adamant|252,252,,,4,|||||",
            ],
        }

        # Get teams for the format, fall back to gen9ou if not found
        format_key = format_id.lower()
        team_list = teams_by_format.get(format_key, teams_by_format["gen9ou"])
        if format_key not in teams_by_format:
            logger.warning(
                "TeamManager: No built-in teams for format %s, using gen9ou", format_id
            )

        if team_index is not None:
            return team_list[team_index % len(team_list)]
        return random.choice(team_list)

    def load_teams_from_smogon_sets(self, format_id: str = "gen9ou") -> List[str]:
        """
        Load team sets from Smogon's format data if available.
        This requires the PS data files to have sample teams.
        """
        # This is a placeholder - in practice you'd parse Smogon's team database
        # or use the PS random team generator
        return []

    def validate_team(self, packed_team: str, format_id: str = "gen9ou") -> List[str]:
        """Validate a team for a format. Returns list of problems or empty list if valid."""
        cmd = f"cd {self.ps_path} && node pokemon-showdown --skip-build validate-team {format_id}"

        result = subprocess.run(
            ["wsl", "-e", "bash", "-c", cmd],
            input=packed_team,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # If return code is 0, team is valid
        if result.returncode == 0:
            return []

        # Parse problems from output
        problems = [line.strip() for line in result.stdout.split("\n") if line.strip()]
        return problems


# ---------------------------------------------------------------------------
# TeamBuilder — caching wrapper around TeamManager for a single player slot
# ---------------------------------------------------------------------------

class TeamBuilder:
    """Handles team selection for a single player slot.

    Wraps TeamManager with caching and optional persistence.
    """

    def __init__(
        self,
        teams_dir: str = "teams",
        team_index: Optional[int] = None,
        name: str = "agent",
    ) -> None:
        self.teams_dir = teams_dir
        self._team_index = team_index
        self._name = name
        self._team_cache: Dict[str, str] = {}

    def get_team(self, format_id: str) -> Optional[str]:
        """Return a packed team string, or None for random-battle formats."""
        if "random" in format_id.lower():
            return None
        if format_id in self._team_cache:
            return self._team_cache[format_id]
        manager = TeamManager(teams_dir=self.teams_dir)
        packed_team = manager.get_sample_team(format_id, team_index=self._team_index)
        self._save_team(format_id, packed_team, manager)
        self._team_cache[format_id] = packed_team
        logger.info("TeamBuilder[%s]: loaded team for %s", self._name, format_id)
        return packed_team

    def invalidate_cache(self, format_id: str) -> None:
        """Evict a cached team so the next call fetches a fresh one."""
        self._team_cache.pop(format_id, None)

    def _save_team(
        self, format_id: str, packed_team: str, manager: TeamManager
    ) -> None:
        """Save the team to a file for future reference."""
        import time

        os.makedirs(self.teams_dir, exist_ok=True)
        format_dir = os.path.join(self.teams_dir, format_id)
        os.makedirs(format_dir, exist_ok=True)
        filepath = os.path.join(
            format_dir, f"team_{self._name}_{int(time.time())}.txt"
        )
        try:
            manager.save_team_to_file(packed_team, filepath, as_export=True)
            logger.info(
                "TeamBuilder[%s]: saved team to %s", self._name, filepath
            )
        except Exception as e:
            logger.warning(
                "TeamBuilder[%s]: failed to save team: %s", self._name, e
            )


# Convenience function
def get_random_team(format_id: str = "gen9ou") -> str:
    """Get a random valid team for the specified format."""
    manager = TeamManager()

    if "random" in format_id.lower():
        return manager.generate_random_team(format_id)
    else:
        return manager.get_sample_team(format_id)


# Sample teams in export format (PokePaste) for reference/testing
SAMPLE_TEAMS_EXPORT = {
    "gen9ou": """Great Tusk @ Leftovers
Ability: Protosynthesis
Tera Type: Steel
EVs: 252 HP / 4 Atk / 252 Spe
Jolly Nature
- Headlong Rush
- Rapid Spin
- Stealth Rock
- Knock Off

Gholdengo @ Choice Scarf
Ability: Good as Gold
Tera Type: Steel
EVs: 252 SpA / 4 SpD / 252 Spe
Timid Nature
- Make It Rain
- Shadow Ball
- Trick
- Focus Blast

Dragapult @ Choice Band
Ability: Infiltrator
Tera Type: Ghost
EVs: 252 Atk / 4 SpD / 252 Spe
Jolly Nature
- Dragon Darts
- Phantom Force
- U-turn
- Sucker Punch

Kingambit @ Leftovers
Ability: Supreme Overlord
Tera Type: Dark
EVs: 252 HP / 252 Atk / 4 SpD
Adamant Nature
- Swords Dance
- Kowtow Cleave
- Sucker Punch
- Iron Head

Slowking-Galar @ Heavy-Duty Boots
Ability: Regenerator
Tera Type: Poison
EVs: 252 HP / 4 Def / 252 SpD
Calm Nature
- Future Sight
- Sludge Bomb
- Flamethrower
- Slack Off

Rillaboom @ Choice Band
Ability: Grassy Surge
Tera Type: Grass
EVs: 252 Atk / 4 SpD / 252 Spe
Adamant Nature
- Wood Hammer
- Grassy Glide
- Knock Off
- U-turn
"""
}

# TODO

## Damage Range Calculator (`find_set`)

- [ ] Create a `Move` dataclass with `bp`, `category`, and `type` fields; refactor `get_damage_range` and `calculate_expected_damages` to accept a `Move` instead of `move_bp` / `move_cat` directly
- [ ] Add `find_set/type_effectiveness.py` defining the full type chart and a function that returns the effectiveness multiplier given a move type and the defender's type(s); wire it into the damage calculator for automatic type effectiveness and STAB determination

## Does It Live?

- Need to reset markers when the Pokemon changes
- Marker color needs to be a different swath 
- X for the specific location of the point
- Reset counter when closing it


## Dataset Creation

- [ ] Check if the dataset creation takes into account the user being trapped / taunted / otherwise limited in options from a previous turn
  - Moves like Mean Look, Block, Spider Web prevent switching
  - Taunt prevents status moves
  - Encore locks into previous move
  - Disable prevents using a specific move
  - Torment prevents using the same move twice in a row
  - Choice items lock into the first move used
  - Partial trapping moves (Wrap, Bind, Fire Spin, etc.) prevent switching

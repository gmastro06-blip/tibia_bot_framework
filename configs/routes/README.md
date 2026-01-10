# Routes

This folder contains cavebot routes.

## Route formats

Routes are JSON lists of waypoints with:

- `x` (int)
- `y` (int)
- `z` (int, optional floor)
- optional `name`
- optional `action`

See `src/navigation/route.py`.

## Step mode (no absolute position)

The bot supports a **step-based cavebot** mode that does **not** require your real in-game coordinates.

- Set `CAVEBOT_MODE=steps`
- Start your character at the first waypoint location in-game
- The bot will execute `|dx| + |dy|` steps to reach the next waypoint

Because there is no pathfinding in step mode, if a segment crosses a wall/obstacle you must adjust the route.

## Newhaven templates

- `newhaven_town_loop_steps.json`: a simple loop that visits key town NPC areas (bank/shop/spells/docks/gate).
- `newhaven_hunt_north_loop_steps.json`: a simple loop for the northern hunting area.

These are templates: you may need to tweak waypoints to match your exact walking paths.

## Converting TibiaMaps coordinates

If you have coordinates copied from TibiaMaps like `X: 32561 Y: 32496 Z: 7`, you can generate a route JSON:

- Paste your coordinate lines into a text file (one per line)
- Convert:
	- `poetry run python -m tools.coords_to_route --input coords.txt --output configs/routes/my_route.json --drop-duplicates`

Accepted line formats:
- `X: 32561 Y: 32496 Z: 7`
- `32561 32496 7`
- `32561,32496,7`

## Recording from clipboard (easy TibiaMaps workflow)

If you don't have in-game coordinates, you can still build a route by using TibiaMaps and copying coordinates.

1) Open TibiaMaps and move/click along the path you want.
2) Each time you have a good point (turn/door/ladder), copy the coordinate text (the bottom-left X/Y/Z).
3) Run the clipboard recorder:

`poetry run python -m tools.record_route_from_clipboard --seconds 300 --out configs/routes/newhaven_recorded.json --print`

It watches the clipboard and appends points whenever you copy a coordinate string.
